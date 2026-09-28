"""同源托管前端产物的响应头：安全头 + **缓存策略**。

缓存策略为什么会单独成为一组用例（真实事故）：静态托管原先一个 Cache-Control 都不发，
浏览器就按 Last-Modified 走启发式缓存。于是"前端换了产物、用户页面还是旧代码"——
旧 index.html 与旧 JS 靠浏览器缓存活着（旧 JS 在服务器上已删除，缓存里还有），
页面看着完全正常、跑的却是旧代码；而新开窗口是冷缓存、拿到的是新产物，
所以"新窗口好了、嵌入的那页还是老毛病"，很难往缓存上想。

两条口径钉死：
- 入口 HTML **每次复验**（no-cache；文件没变时服务端回 304，代价极小）；
- 带内容哈希的静态资源**长缓存**（文件名变了才会重新下载）。
安全头一并回归：这几项一直在，删了不该无声无息。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

from server.api import _build_frontend_static_files

INDEX_HTML = "<!doctype html><html><body><div id=\"app\"></div></body></html>"
BUNDLE_JS = "console.log('bundle');"


@pytest.fixture
def static_client(tmp_path: Path):
    """临时目录里的假产物：目录请求与文件请求都走真实的 StaticFiles 实现。"""
    (tmp_path / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text(BUNDLE_JS, encoding="utf-8")
    app = Starlette(routes=[Mount("/", app=_build_frontend_static_files(tmp_path))])
    return TestClient(app)


def test_入口文档每次复验而非长缓存(static_client):
    """`/` 与 `/index.html` 都必须 no-cache。

    目录请求那条尤其重要：StaticFiles 在 html=True 下会递归取 index.html，
    若按"请求路径后缀"判断，外层那次会把 no-cache 覆盖成长缓存——产物换了页面却不换。
    """
    for url in ("/", "/index.html"):
        resp = static_client.get(url)
        assert resp.status_code == 200, url
        assert resp.headers["cache-control"] == "no-cache", url
        assert resp.headers["content-type"].startswith("text/html"), url


def test_哈希资源长缓存(static_client):
    """文件名里带内容哈希：可以放心让浏览器一年不再问。"""
    resp = static_client.get("/assets/index-abc123.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_安全头仍然齐全(static_client):
    """X-Frame-Options / CSP 是防第三方站点白蹭模型配额的那道墙，别在改动里丢掉。"""
    for url in ("/", "/assets/index-abc123.js"):
        resp = static_client.get(url)
        assert resp.headers["x-frame-options"] == "SAMEORIGIN", url
        assert resp.headers["content-security-policy"] == "frame-ancestors 'self'", url
        assert resp.headers["x-content-type-options"] == "nosniff", url
        assert resp.headers["referrer-policy"] == "no-referrer", url
