"""部署鉴权门禁的用例（第 13 轮复核整改 §2.5，与第 13 轮复核第四节）。

`deploy/scripts/check_rag_auth.sh` 是**安装期**门禁：拦的是"两份配置各看起来都对、
合起来却漏了一半"这类组合。这类脚本最容易退化成"写了但没人跑过"，所以这里用合成配置
把它的判定逐条跑一遍——包括最核心的升级项：**模板占位符不能通过检查**。

§2.5 的具体场景：部署模板里写的是 `CHANGE_ME_openssl_rand_base64_48`。部署者若把同一个
占位符复制到两侧，"非空 + 两侧同值"两条老检查会全部通过——一串所有人都知道的值就这样
成了生产密钥。门禁因此还要判"像不像占位符"与"够不够长"，并检查 Neo4j 口令不是默认值。

为什么不把门禁改写成 Python 再测：它必须在服务器上**装 Python 环境之前**就能跑，
所以只能是 bash。代价由本文件补偿——直接执行那个脚本，测的是真货。

跨平台：Windows 上没有 bash 时整组跳过（CI 跑在 ubuntu，那里必跑）。
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "deploy" / "scripts" / "check_rag_auth.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="未安装 bash（Windows 无 Git Bash 时跳过）")

# 一组"真随机"密钥：长度合规、不含任何占位词
REAL_JWT = "Zk9sVnBxQmNkM2xSaFR1V2V4YW1wbGVOZXZlclVzZWQxMjM0NTY3OA=="
REAL_SERVICE_KEY = "9f2c7a4b8e1d6f3a5c0b9e8d7a6f4c2b1e0d9c8b7a6f5e4d3c2b1a09f8e7d6c"
REAL_NEO4J = "nQ7vR2xL9pM4tK8wY3zA"
PLACEHOLDER = "CHANGE_ME_openssl_rand_base64_48"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _gate(tmp_path: Path, *, jwt_secret: str = REAL_JWT,
          rag_secret: str | None = None, service_key: str = REAL_SERVICE_KEY,
          backend_service_key: str | None = None, neo4j_password: str = REAL_NEO4J,
          nginx_mode: bool = False,
          auth_basic_enabled: bool = False,
          introspect_configured: bool = True,
          allow_delayed_revocation: bool = False,
          extra_path: str | None = None,
          bot_key: str | None = None,
          bot_deployed: bool = False) -> subprocess.CompletedProcess:
    """用合成配置跑一次门禁，返回完成的进程（看 returncode 与输出）。"""
    rag_env = tmp_path / "RAG" / ".env"
    backend_env = tmp_path / "backend" / ".env"
    site = tmp_path / "site.conf"
    htpasswd = tmp_path / ".htpasswd"
    rag_unit = tmp_path / "china-war-rag.service"

    revocation_lines = (
        ["RAG_INTROSPECT_URL=http://127.0.0.1:5000/api/internal/token/introspect",
         f"RAG_INTERNAL_SERVICE_KEY={service_key}",
         "RAG_INTROSPECT_FAIL_MODE=closed"]
        if introspect_configured else [])
    if allow_delayed_revocation:
        revocation_lines.append("RAG_ALLOW_DELAYED_REVOCATION=true")
    if bot_key:
        revocation_lines.append(f"RAG_BOT_API_KEY={bot_key}")
    _write(rag_env, "\n".join([
        "RAG_REQUIRE_ACTIVE_VERSION=true",
        f"RAG_AUTH_MODE={'nginx' if nginx_mode else 'jwt'}",
        f"RAG_JWT_SECRET={jwt_secret if rag_secret is None else rag_secret}",
        *revocation_lines,
    ]) + "\n")
    _write(backend_env, "\n".join([
        f"JWT_SECRET={jwt_secret}",
        f"INTERNAL_SERVICE_KEY={service_key if backend_service_key is None else backend_service_key}",
        f"NEO4J_PASSWORD={neo4j_password}",
    ]) + "\n")
    # 站点配置里的 auth_basic 是否启用，与 RAG_AUTH_MODE 是**两个独立的开关**：
    # 那正是第 13 轮复核发现的缺陷形态——模式选了 nginx，而这里还是注释。
    auth_lines = (['    auth_basic           "china-war";',
                   '    auth_basic_user_file /etc/nginx/.htpasswd;'] if auth_basic_enabled else
                  ['    # auth_basic           "china-war";',
                   '    # auth_basic_user_file /etc/nginx/.htpasswd;'])
    _write(site, "\n".join([
        "location /rag/ {",
        "    proxy_pass http://rag_backend/;",
        *auth_lines,
        "}",
        "location /api/internal/ {",
        "    return 404;",
        "}",
    ]) + "\n")
    if bot_deployed:
        # 机器人存在 = 这个部署要接它 → 门禁按"必须配 Bot Key"判（P2-20）
        _write(tmp_path / "feishu-bot" / ".env", "FEISHU_APP_ID=cli_x\n")
    _write(htpasswd, "china-war:$apr1$abcdefgh$0123456789abcdefghij\n")
    _write(rag_unit,
           "[Service]\nExecStart=/opt/china-war/RAG/.venv/bin/python scripts/run_server.py "
           "--host 127.0.0.1 --port 8000\n")

    return subprocess.run(
        [BASH, str(GATE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={
            # extra_path 用于把"假的 nginx"放进 PATH，复现 CI 上"装了 nginx"的分支
            "PATH": (f"{extra_path}:" if extra_path else "") + "/usr/bin:/bin:/usr/sbin:/sbin",
            "APP_DIR": str(tmp_path),
            "RAG_ENV": str(rag_env),
            "BACKEND_ENV": str(backend_env),
            "NGINX_SITE": str(site),
            "HTPASSWD": str(htpasswd),
            "RAG_UNIT": str(rag_unit),
        },
    )


def _out(result: subprocess.CompletedProcess) -> str:
    return (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------- 正常路径


def test_真实密钥的配置通过门禁(tmp_path):
    """先钉住"配对了就放行"：否则下面那些失败断言可能只是脚本坏掉。"""
    result = _gate(tmp_path)

    assert result.returncode == 0, _out(result)
    assert "JWT 密钥两侧同值" in result.stdout


# ---------------------------------------------------------------- 占位符（§2.5）


def test_两侧同一个模板占位符必须失败(tmp_path):
    """§2.5 的核心场景：同一个占位符复制到两侧，老检查会全部通过。"""
    result = _gate(tmp_path, jwt_secret=PLACEHOLDER)

    assert result.returncode != 0
    assert "占位符" in result.stdout


def test_只有一侧是占位符也要失败(tmp_path):
    result = _gate(tmp_path, jwt_secret=REAL_JWT, rag_secret=PLACEHOLDER)

    assert result.returncode != 0
    assert "占位符" in result.stdout


def test_服务间密钥是占位符要失败(tmp_path):
    result = _gate(tmp_path, service_key="CHANGE_ME_openssl_rand_hex_32")

    assert result.returncode != 0
    assert "服务间密钥" in result.stdout and "占位符" in result.stdout


def test_Neo4j_口令是模板占位符要失败(tmp_path):
    result = _gate(tmp_path, neo4j_password="CHANGE_ME")

    assert result.returncode != 0
    assert "NEO4J_PASSWORD" in result.stdout


def test_Neo4j_默认口令要失败(tmp_path):
    """§2.5 的另一半：默认值本身不是"占位符"，但同样所有人都知道。"""
    result = _gate(tmp_path, neo4j_password="neo4j")

    assert result.returncode != 0
    assert "默认" in result.stdout or "弱口令" in result.stdout


# ---------------------------------------------------------------- 长度（§2.5）


def test_密钥过短要失败(tmp_path):
    result = _gate(tmp_path, jwt_secret="abcdefghij0123456789")   # 20 字符

    assert result.returncode != 0
    assert "过短" in result.stdout


def test_Neo4j_口令过短要失败(tmp_path):
    result = _gate(tmp_path, neo4j_password="short1234")

    assert result.returncode != 0
    assert "过短" in result.stdout


# ---------------------------------------------------------------- 撤销策略必须显式选择（§2.7）


def test_生产档未配撤销查询且未显式接受时失败(tmp_path):
    """§2.7：不能靠"两个值都不填"隐式接受"停用后 7 天内仍可用"。

    口径与 RAG 自己的启动门禁一致（那种部署下服务会拒绝启动），安装期就要红。
    """
    result = _gate(tmp_path, introspect_configured=False)

    assert result.returncode != 0, _out(result)
    assert "撤销" in result.stdout


def test_生产档显式接受延迟撤销后通过(tmp_path):
    """显式接受是一个**有效**的选择：配置里写下的决定不该被当成漏配。"""
    result = _gate(tmp_path, introspect_configured=False, allow_delayed_revocation=True)

    assert result.returncode == 0, _out(result)
    assert "显式接受延迟撤销" in result.stdout


# ---------------------------------------------------------------- 与密钥扫描同口径


def _scanner_allowlist():
    """加载 `RAG/scripts/check_secrets.py` 的 ALLOWLIST（脚本模块级只有常量与函数）。"""
    path = REPO_ROOT / "RAG" / "scripts" / "check_secrets.py"
    spec = importlib.util.spec_from_file_location("check_secrets_for_gate_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ALLOWLIST


@pytest.mark.parametrize("sample", [
    PLACEHOLDER,
    "your_jwt_secret_here_padding_padding_padding",
    "example-secret-key-padding-padding-padding",
    "placeholder_token_padding_padding_padding",
    "x" * 40,
    "test-secret-padding-padding-padding-pad0",
    "do_not_use_this_in_production_padding_00",
    REAL_JWT,
    REAL_SERVICE_KEY,
])
def test_占位符判定与密钥扫描器一致(tmp_path, sample):
    """§2.5 验收项：密钥扫描与部署门禁的占位符口径一致。

    `RAG/scripts/check_secrets.py` 的 ALLOWLIST 回答"这个值像不像占位符（所以不算泄露）"，
    门禁把它反过来用："像占位符就不能当密钥"。两处若分叉，就会出现
    "扫描器认为它是占位符（不报警）、门禁却认为它是真密钥（放行）"的缝——
    而那正是模板占位符能变成生产密钥的路径。
    """
    scanner_says_placeholder = _scanner_allowlist().search(sample) is not None

    result = _gate(tmp_path, jwt_secret=sample)
    gate_says_placeholder = "占位符" in result.stdout

    assert gate_says_placeholder == scanner_says_placeholder, (
        f"两侧口径不一致：样本 {sample!r}；"
        f"扫描器认为占位符={scanner_says_placeholder}，门禁认为占位符={gate_says_placeholder}"
    )
    # 顺带钉住整体结论：非占位符且够长才允许通过
    expected_pass = (not scanner_says_placeholder) and len(sample) >= 32
    assert (result.returncode == 0) is expected_pass, _out(result)


# ---------------------------------------------------------------- nginx 档的旧检查仍在


def test_nginx_档下_auth_basic_仍是注释要失败(tmp_path):
    """第 13 轮复核发现的原始缺陷：RAG 说"nginx 在把关"，而 auth_basic 是注释状态。

    这种组合两边都能正常启动、日志里没有异常，唯一后果是公网没有访问控制——
    所以它必须在安装期就红。
    """
    result = _gate(tmp_path, nginx_mode=True, auth_basic_enabled=False)

    assert result.returncode != 0, _out(result)
    assert "auth_basic" in result.stdout


def test_nginx_档且_auth_basic_已启用时通过(tmp_path):
    """对照组：把 auth_basic 真正打开后，同一份配置必须能过——否则上面那条"
    "红"可能只是脚本永远报错。"""
    result = _gate(tmp_path, nginx_mode=True, auth_basic_enabled=True)

    assert result.returncode == 0, _out(result)
    assert "auth_basic 处于启用状态" in result.stdout


def test_回环监听与内部接口屏蔽在两种档位下都被检查(tmp_path):
    for kwargs in ({"nginx_mode": True, "auth_basic_enabled": True}, {}):
        result = _gate(tmp_path, **kwargs)

        assert "nginx 已屏蔽 /api/internal/" in result.stdout
        assert "只监听回环" in result.stdout


# ---------------------------------------------------------------- systemd 依赖（§2.9）


def test_rag_服务声明依赖_backend():
    """§2.9：`RAG_INTROSPECT_FAIL_MODE=closed`（默认）下 backend 不可用 = 所有 JWT 用户的
    问答全部失败，而原先 unit 只依赖 network-online，RAG 可能先于 backend 起来把用户全拒掉。

    这里直接读 unit 文件：`After`/`Wants` 写在 [Unit] 段里，是**声明式**的启动顺序，
    没有别的机制能替代它（进程内的重试只能缓解，不能保证顺序）。
    """
    unit = (REPO_ROOT / "deploy" / "systemd" / "china-war-rag.service").read_text(encoding="utf-8")
    directives = " ".join(
        line for line in unit.splitlines()
        if line.startswith(("After=", "Wants="))
    )

    assert "china-war-backend.service" in directives, (
        "RAG 的凭证撤销查询要调 backend；启动顺序必须声明出来。"
        f"当前 directive 行：{directives!r}"
    )


def test_站点不在_nginx_配置树时跳过生效性校验(tmp_path):
    """复现 CI 的真实场景：runner 自带 nginx，但站点文件没装进 /etc/nginx。

    此时 `nginx -T` 的输出里根本不会有这个文件，拿它判断等于"用一份不含被测对象的
    快照去证明被测对象"——会得到一条**假失败**（CI 上实测：报"nginx -T 无输出"，
    与本项目的脚本毫无关系）。门禁必须跳过并说明原因，而不是装作验证过。
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_nginx = fake_bin / "nginx"
    # 只做一件事：让 `command -v nginx` 与 `nginx -T` 都不报错（模拟"装了 nginx"）
    fake_nginx.write_text("""#!/bin/sh
exit 0
""", encoding="utf-8")
    fake_nginx.chmod(0o755)

    result = _gate(tmp_path, nginx_mode=True, auth_basic_enabled=True,
                   extra_path=str(fake_bin))

    assert result.returncode == 0, _out(result)
    assert "跳过 nginx -T" in result.stdout
    assert ".htpasswd 存在且非空" in result.stdout
    assert "auth_basic 处于启用状态" in result.stdout


# ---------------------------------------------------------------- 机器人通道（P2-20）


def test_jwt_档未配_Bot_Key_且要部署机器人时失败(tmp_path):
    """jwt 档下机器人只发 X-Bot-Key → 每问必 401，而机器人侧只说"RAG 不可用"。

    仓库里有 `feishu-bot/.env` 就说明这个部署要接机器人，此时必须拦住。
    """
    result = _gate(tmp_path, bot_deployed=True)

    assert result.returncode != 0, _out(result)
    assert "RAG_BOT_API_KEY" in result.stdout


def test_jwt_档未配_Bot_Key_但不部署机器人时只提醒(tmp_path):
    """不是每个部署都接机器人（它是可选 IM 入口），不该一票否决。"""
    result = _gate(tmp_path, bot_deployed=False)

    assert result.returncode == 0, _out(result)
    assert "RAG_BOT_API_KEY" in result.stdout      # 仍然提醒
    assert "若以后要接飞书机器人" in result.stdout


def test_配了_Bot_Key_后通过(tmp_path):
    result = _gate(tmp_path, bot_deployed=True, bot_key="a" * 40)

    assert result.returncode == 0, _out(result)
    assert "机器人共享密钥（RAG 侧）" in result.stdout
    assert "已设置且长度合规" in result.stdout
