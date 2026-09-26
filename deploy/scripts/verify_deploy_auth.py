"""部署验收：四类问题的端到端验证（在服务器上跑，用 backend 的 3.11 环境）。

只打印判定结果，**不打印任何密钥或 token 原文**。

覆盖：
1. 内部撤销查询接口（RAG 判定"停用/改密码后旧 token 立刻失效"时调的就是它）
2. 无密钥调用内部接口必须被拒
3. RAG 问答接口的四种身份：匿名 / 有效 JWT / 已删号 JWT / 错误 X-Bot-Key
4. 经 nginx 的公网入口：匿名必须 401
5. 旧后端的错误响应格式：必须是 JSON（前端只有读 JSON 才能显示 msg）
6. RAG health 里的鉴权与撤销运行时状态
"""

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

BACKEND = "/opt/china-war/backend"
os.chdir(BACKEND)
sys.path.insert(0, BACKEND)

import local_settings  # noqa: E402  读 backend/.env（值不打印）
import jwt_util  # noqa: E402

PUBLIC = "http://47.117.100.163"


def req(method, url, body=None, headers=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # 连接层问题（服务没起、端口不通）
        return -1, f"{type(exc).__name__}: {exc}"


con = sqlite3.connect(f"file:{BACKEND}/database?mode=ro", uri=True)
row = con.execute(
    "SELECT id, account, token_version FROM UserInfo WHERE disabled = 0 ORDER BY id LIMIT 1"
).fetchone()
disabled_count = con.execute("SELECT COUNT(*) FROM UserInfo WHERE disabled = 1").fetchone()[0]
con.close()
uid, account, ver = row
print(f"（用于测试的在用账号：id={uid}，禁用账号数={disabled_count}）")

token_ok = jwt_util.encode(uid, token_version=ver)
token_ghost = jwt_util.encode(999999, token_version=1)   # 库里不存在的账号
svc_key = local_settings.get("INTERNAL_SERVICE_KEY", "")

print("\n【1】内部撤销查询接口（RAG 判撤销时调的就是它）")
for label, tok in (("有效 token（在用账号）", token_ok),
                   ("已删号账号的 token", token_ghost),
                   ("垃圾串", "not-a-jwt")):
    status, body = req("POST", "http://127.0.0.1:5000/api/internal/token/introspect",
                       {"token": tok},
                       {"X-Internal-Service-Key": svc_key, "Content-Type": "application/json"})
    try:
        data = json.loads(body).get("data") or {}
        verdict = f"active={data.get('active')} reason={str(data.get('reason'))[:45]}"
    except Exception:
        verdict = body[:90]
    print(f"    {label:20s} HTTP {status}  {verdict}")

print("\n【2】不带服务间密钥调用内部接口（应被拒，不能被当成公网查询服务）")
status, body = req("POST", "http://127.0.0.1:5000/api/internal/token/introspect",
                   {"token": token_ok}, {"Content-Type": "application/json"})
print(f"    无 X-Internal-Service-Key → HTTP {status}  {body[:90]}")

print("\n【3】RAG 问答接口四种身份（空请求体：只看鉴权结论，不触发 LLM 调用）")
for label, headers in (
    ("匿名", {"Content-Type": "application/json"}),
    ("有效 JWT", {"Content-Type": "application/json", "Authorization": f"Bearer {token_ok}"}),
    ("已删号 JWT", {"Content-Type": "application/json", "Authorization": f"Bearer {token_ghost}"}),
    ("错误的 X-Bot-Key", {"Content-Type": "application/json", "X-Bot-Key": "wrong-key-xxx"}),
):
    status, body = req("POST", "http://127.0.0.1:8000/api/query/json", {}, headers)
    print(f"    {label:18s} HTTP {status}  {body[:80]}")

print("\n【4】经 nginx 的公网入口")
status, body = req("POST", f"{PUBLIC}/rag/api/query/json", {}, {"Content-Type": "application/json"})
print(f"    匿名 POST /rag/api/query/json → HTTP {status}  {body[:80]}")
status, body = req("GET", f"{PUBLIC}/rag/api/health")
print(f"    匿名 GET  /rag/api/health     → HTTP {status}  {body[:60]}")
status, body = req("GET", f"{PUBLIC}/api/internal/token/introspect")
print(f"    公网 GET  /api/internal/…     → HTTP {status}  {body[:60]}")

print("\n【5】旧后端的错误响应格式（必须是 JSON 带 code/msg/request_id）")
status, body = req("GET", f"{PUBLIC}/api/there-is-no-such-route", None,
                   {"Authorization": f"Bearer {token_ok}"})
print(f"    不存在的路径        → HTTP {status}  {body[:130]}")
status, body = req("GET", f"{PUBLIC}/api/admin/users")
print(f"    未登录访问受保护接口 → HTTP {status}  {body[:130]}")
status, body = req("DELETE", f"{PUBLIC}/api/admin/users", None,
                   {"Authorization": f"Bearer {token_ok}"})
print(f"    方法不对            → HTTP {status}  {body[:130]}")

print("\n【6】RAG health 里的鉴权状态")
status, body = req("GET", "http://127.0.0.1:8000/api/health")
try:
    health = json.loads(body)
    print("    auth =", json.dumps(health.get("auth"), ensure_ascii=False))
except Exception:
    print("    health 解析失败：", body[:150])
