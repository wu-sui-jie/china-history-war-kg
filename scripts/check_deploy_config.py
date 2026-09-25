#!/usr/bin/env python3
"""部署配置的结构检查（第 14 轮审计 P2-22）。

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


def main() -> int:
    problems: list[str] = []
    scripts = check_shell_syntax(problems)
    units = check_systemd_units(problems)
    site = check_nginx_site(problems)

    if problems:
        for problem in problems:
            print(f"✗ {problem}")
        print(f"\n部署配置检查失败：{len(problems)} 项")
        return 1
    print(f"✓ 部署配置检查通过（脚本 {scripts} 个、单元 {len(units)} 个、nginx 站点 {site} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
