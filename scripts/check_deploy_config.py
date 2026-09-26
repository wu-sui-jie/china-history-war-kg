#!/usr/bin/env python3
"""部署配置的结构检查。

## 为什么需要

CI 原先**没有任何步骤碰过 `deploy/`**：7 个脚本、nginx 站点、systemd 单元的语法与路径
错误可以全绿进 main，而文档还把 `bash -n deploy/scripts/*.sh` 记成"本机实测"。
本脚本把能在 CI 里判定的那部分固定下来——不需要服务器、不需要装 nginx：

- 所有 `deploy/scripts/*.sh` 的语法（`bash -n`）；
- systemd 单元：必需键、可执行路径的存在性、`WorkingDirectory` 指向仓库内目录；
- nginx 站点：关键 `location` 齐备、SSE 的两个反代位置都关了缓冲、
  `/api/internal/` 被屏蔽、`auth_basic` 若出现必须有口令文件。

**判不了的**（留给真机验收，见 deploy/README 第八节）：nginx 是否能起来、
systemd 是否能启动服务、timer 是否真的触发。

用法：
    python scripts/check_deploy_config.py          # 检查，非 0 退出表示有问题
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY = REPO_ROOT / "deploy"

# 单元里必须存在的键（缺一个就不是可用的服务定义）。timer 与 service 的要求不同：
# timer 没有 ExecStart/User，它靠 OnCalendar 触发另一个单元——把它按 service 校验
# 会得到四条假失败（本脚本第一版就是这么错的）。
REQUIRED_SERVICE_KEYS = ("Description=", "[Service]", "ExecStart=", "User=", "WorkingDirectory=")
REQUIRED_TIMER_KEYS = ("Description=", "[Timer]")
# timer 的触发方式三选一即可（同机部署常用 OnBootSec + OnUnitActiveSec 的
# "开机后等一会儿、之后按间隔重复"写法，不一定用 OnCalendar）
TIMER_SCHEDULE_KEYS = ("OnCalendar=", "OnUnitActiveSec=", "OnBootSec=")
# 允许出现非仓库内路径的键（运行时目录、日志、临时目录等）
ALLOWED_EXTERNAL_PREFIXES = ("/opt/", "/etc/", "/var/", "/run/", "/tmp/", "/usr/", "/home/")


def _fail(problems: list[str], message: str) -> None:
    problems.append(message)


def check_shell_syntax(problems: list[str]) -> int:
    bash = shutil.which("bash")
    scripts = sorted(DEPLOY.glob("scripts/*.sh"))
    if not scripts:
        _fail(problems, "deploy/scripts 下没有找到任何脚本")
        return 0
    if bash is None:
        print("（未安装 bash，跳过脚本语法检查）")
        return len(scripts)
    for script in scripts:
        result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
        if result.returncode != 0:
            _fail(problems, f"{script.relative_to(REPO_ROOT)} 语法错误：{result.stderr.strip()}")
    return len(scripts)


def check_systemd_units(problems: list[str]) -> list[str]:
    units = sorted(DEPLOY.glob("systemd/*"))
    for unit in units:
        text = unit.read_text(encoding="utf-8")
        rel = unit.relative_to(REPO_ROOT)
        required = REQUIRED_TIMER_KEYS if unit.suffix == ".timer" else REQUIRED_SERVICE_KEYS
        for key in required:
            if key not in text:
                _fail(problems, f"{rel} 缺少 {key}")
        if unit.suffix == ".timer":
            if not any(key in text for key in TIMER_SCHEDULE_KEYS):
                _fail(problems, f"{rel} 没有任何触发时机（OnCalendar / OnUnitActiveSec / OnBootSec）")
            # timer 必须指向一个真实存在的 service（写错单元名时 systemd 只是静默不跑）
            match = re.search(r"^Unit=(\S+)", text, re.M)
            if match:
                target = DEPLOY / "systemd" / match.group(1)
                if not target.exists():
                    _fail(problems, f"{rel} 的 Unit={match.group(1)} 在 deploy/systemd 下不存在")
            continue
        # ExecStart 的脚本必须在仓库里存在（部署后由 install_services.sh 渲染到 /etc）
        for match in re.finditer(r"^ExecStart=(\S+)", text, re.M):
            target = match.group(1)
            if target.startswith("/"):
                # 形如 /opt/china-war/RAG/... 或 /opt/miniconda3/...：只校验仓库内那部分
                repo_relative = re.sub(r"^/opt/(china-war|miniconda3)/", "", target)
                candidate = REPO_ROOT / repo_relative
                if not candidate.exists() and not repo_relative.startswith("envs/"):
                    # 解释器路径（/opt/miniconda3/envs/...）在服务器上才有，跳过
                    if "envs/" not in repo_relative:
                        _fail(problems, f"{rel} 的 ExecStart 指向不存在的路径：{target}")
        # WorkingDirectory 必须是仓库内的相对路径（拼上 /opt/china-war 后要存在）
        for match in re.finditer(r"^WorkingDirectory=/opt/china-war/(\S+)", text, re.M):
            if not (REPO_ROOT / match.group(1)).is_dir():
                _fail(problems, f"{rel} 的 WorkingDirectory 指向不存在的目录：{match.group(1)}")
    return [unit.name for unit in units]


def check_nginx_site(problems: list[str]) -> int:
    site = DEPLOY / "nginx" / "china-war.conf"
    if not site.exists():
        _fail(problems, "缺少 deploy/nginx/china-war.conf")
        return 0
    text = site.read_text(encoding="utf-8")

    # 站点必须覆盖的四类入口（集成与入口约定第二节）
    for location, why in (
        ("location /static/", "旧前端静态资源"),
        ("location = /", "首页"),
        ("location /rag/", "RAG 页面与接口"),
        ("location /", "旧后端（含 /api/*）"),
        ("location /api/internal/", "内部接口必须公网不可达"),
    ):
        if not any(line.strip().startswith(location) for line in text.splitlines()):
            _fail(problems, f"nginx 站点缺少 {location}（{why}）")

    # SSE 反代不能缓冲：两个位置（/rag/ 与旧后端）都要关
    if text.count("proxy_buffering off;") < 2:
        _fail(problems, "nginx 站点的 SSE 反代位置不足 2 处关闭了 proxy_buffering"
                        "（/rag/ 与旧后端各一处，否则回答会憋住、最后一次性吐出）")

    # 内部接口屏蔽
    if "location /api/internal/" not in text:
        _fail(problems, "nginx 站点没有屏蔽 /api/internal/（内部接口不该暴露到公网）")

    # auth_basic 一旦启用就必须配口令文件（nginx -t 会因缺文件失败）
    enabled_basic = [line for line in text.splitlines()
                     if line.strip().startswith("auth_basic ")]
    if enabled_basic and "auth_basic_user_file" not in text:
        _fail(problems, "nginx 站点启用了 auth_basic 却没有 auth_basic_user_file（nginx -t 会失败）")
    return 1


def check_install_services_unit_list(problems: list[str]) -> int:
    """install_services.sh 里列出的单元名，必须都能在 deploy/systemd/ 下找到。

    现实教训：那个循环里前三个名字漏了 `.service` 后缀（`china-war-backend` 而不是
    `china-war-backend.service`），脚本拼出的源路径因此不存在，执行时在 **2/5 步**
    直接 `die "缺少 …/systemd/china-war-backend"`——照 README 从上到下部署的人必然
    卡在这里。`bash -n` 只查语法，名字写错它一个字都不会说；只有把"名字必须对应
    真实文件"这条关系钉下来，CI 才能提前拦住。
    """
    script = DEPLOY / "scripts" / "install_services.sh"
    if not script.exists():
        _fail(problems, "缺少 deploy/scripts/install_services.sh")
        return 0
    text = script.read_text(encoding="utf-8")
    # 取 `for unit in …; do` 那一段（允许用反斜杠续行）。`^` 配合 re.M 锚到行首，
    # 免得匹配到注释或文档里提到的同一句话。
    match = re.search(r"^\s*for\s+unit\s+in\s+(.*?);\s*do", text, re.S | re.M)
    if not match:
        _fail(problems, "install_services.sh 里找不到 `for unit in ...; do`（脚本结构变了？）")
        return 0
    # 续行的反斜杠会被 split() 当成独立 token。**必须显式滤掉**：这一条是本检查
    # 第一版的实际缺陷——本机（Windows）跑它居然通过，因为 `Path("deploy/systemd") / "\\"`
    # 在 Windows 上被当成"目录自身"（反斜杠是路径分隔符），`.exists()` 为真；
    # 到了 Linux 它就是两个字面字符，CI 立刻报"单元 '\\' 不存在"。同一段代码在
    # 两个平台上给出相反结论——这正是本仓库反复踩到的"只在 Windows 验证过"。
    names = []
    for token in match.group(1).split():
        token = token.strip("\\")
        if not token or token.startswith("#"):
            continue
        names.append(token)
    for name in names:
        # 单元名必须长得像单元文件：`xxx.service` / `xxx.timer`
        if not re.fullmatch(r"[A-Za-z0-9@._-]+\.(service|timer)", name):
            _fail(problems, f"install_services.sh 列出的 {name!r} 不像 systemd 单元文件名"
                            f"（应为 xxx.service / xxx.timer；续行符或注释漏进解析了？）")
            continue
        if not (DEPLOY / "systemd" / name).exists():
            _fail(problems, f"install_services.sh 列出的单元 {name!r} 在 deploy/systemd/ 下不存在"
                            f"（会以“缺少 …/systemd/{name}”在第 2 步中止部署）")
    return len(names)


def main() -> int:
    problems: list[str] = []
    scripts = check_shell_syntax(problems)
    units = check_systemd_units(problems)
    site = check_nginx_site(problems)
    listed = check_install_services_unit_list(problems)

    if problems:
        for problem in problems:
            print(f"✗ {problem}")
        print(f"\n部署配置检查失败：{len(problems)} 项")
        return 1
    print(f"✓ 部署配置检查通过（脚本 {scripts} 个、单元 {len(units)} 个、"
          f"install_services 引用 {listed} 个、nginx 站点 {site} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
