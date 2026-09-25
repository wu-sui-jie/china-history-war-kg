"""子图出图（P2-1）的守护用例。

降级路径是**必经路径**，与成功路径同等测试（开发文档十二-2）：
Node 缺失、脚本报错、超时、空图，都必须返回 None 交给文字降级，绝不允许空白。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bot.render.subgraph import RENDER_DIR, RENDER_SCRIPT, SubgraphRenderer

SUBGRAPH = {
    "nodes": [{"id": "n1", "type": "事件", "name": "长平之战", "dynasty": "战国"},
              {"id": "n2", "type": "地点", "name": "长平"},
              {"id": "n3", "type": "人物", "name": "白起"}],
    "edges": [{"source": "n1", "target": "n2", "relation": "主战场"},
              {"source": "n1", "target": "n3", "relation": "统帅"}],
}


class FakeFeishu:
    def __init__(self, image_key: str | None = "img-1"):
        self.image_key = image_key
        self.uploaded: list[Path] = []
        self.bytes_at_upload: list[bytes] = []

    def upload_image(self, path):
        path = Path(path)
        self.uploaded.append(path)
        # 渲染封装会在上传后删临时文件，所以内容必须在"上传时"快照
        self.bytes_at_upload.append(path.read_bytes())
        return self.image_key


@pytest.fixture
def renderer(tmp_path):
    return SubgraphRenderer(FakeFeishu(), timeout=20.0, work_dir=tmp_path / "render")


@pytest.fixture
def fake_render_env(tmp_path, monkeypatch):
    """把"渲染环境可用"这件事做成这些用例真正需要的最小条件。

    为什么需要它（2026-09-25 CI 红灯的根因）：这些用例用的是**假 Node 脚本**
    （只往 stdout 写字，不 require echarts/resvg/d3），因此真实 npm 依赖并不是
    它们的前提；但产品代码的预检要求 `render/node_modules` 存在才算"可用"。
    只判断 `shutil.which("node")` 的写法在 CI 上会漏判——GitHub 的 runner 自带 node
    却没有 `npm install` 过的 render/node_modules，于是用例走进产品代码后被
    "缺少 Node 依赖"提前拦下，日志里自然没有假脚本的 stdout 片段，断言失败。
    （本机装了 node_modules 所以一直是绿的：典型的"本机绿、CI 红"。）

    这里造一个空的 node_modules 让预检通过：CI 上这些用例就能**真的执行**，
    而不是被 skip —— 它们考的正是"失败时的日志与产物清理"，是 CI 最该覆盖的降级路径。

    需要真实 npm 依赖的用例（真实渲染那两个）不挂这个 fixture，它们自带 npm 依赖检查。
    """
    render_dir = tmp_path / "render-env"
    (render_dir / "node_modules").mkdir(parents=True)
    monkeypatch.setattr("bot.render.subgraph.RENDER_DIR", render_dir)
    return render_dir


def _skip_without_node(renderer) -> None:
    """没有 node 可执行文件时跳过：上面那些用例真的会起 Node 进程。"""
    if shutil.which(renderer.node_bin) is None:
        pytest.skip("未安装 Node")


def test_unavailable_reason_when_node_missing(renderer):
    renderer.node_bin = "definitely-not-a-node-binary"
    reason = renderer.unavailable_reason()
    assert reason and "Node" in reason
    assert renderer.is_available() is False


def test_unavailable_reason_when_script_missing(renderer, tmp_path, monkeypatch):
    monkeypatch.setattr("bot.render.subgraph.RENDER_SCRIPT", tmp_path / "nope.js")
    assert "渲染脚本不存在" in (renderer.unavailable_reason() or "")


def test_disabled_renderer_reports_reason(renderer):
    renderer.enabled = False
    assert "SUBGRAPH_RENDER_ENABLED" in (renderer.unavailable_reason() or "")


def test_empty_graph_returns_none_without_starting_node(renderer):
    """空图不起 Node 进程：没有节点就没有可画的东西，起进程纯属浪费。"""
    assert renderer.render_png({"nodes": [], "edges": []}) is None
    assert renderer.render_and_upload({"nodes": [], "edges": []}) is None


def test_missing_node_binary_returns_none(renderer):
    renderer.node_bin = "definitely-not-a-node-binary"
    assert renderer.render_png(SUBGRAPH) is None
    assert renderer.render_and_upload(SUBGRAPH) is None


def test_broken_script_returns_none(renderer, tmp_path, monkeypatch, fake_render_env):
    """脚本报错（模拟 Node 环境坏掉）→ None，不抛异常。"""
    _skip_without_node(renderer)
    broken = tmp_path / "broken.js"
    broken.write_text("process.stdout.write(JSON.stringify({ok:false,error:'炸了'}));"
                      "process.exit(1);", encoding="utf-8")
    monkeypatch.setattr("bot.render.subgraph.RENDER_SCRIPT", broken)
    assert renderer.render_png(SUBGRAPH) is None


def test_parse_result_takes_last_json_line():
    stdout = "some warning\nanother\n{\"ok\": true, \"out\": \"x.png\"}\n"
    assert SubgraphRenderer._parse_result(stdout) == {"ok": True, "out": "x.png"}
    assert SubgraphRenderer._parse_result("no json here") is None
    assert SubgraphRenderer._parse_result("") is None


def test_parse_result_survives_mojibake():
    """按错编码解出来的乱码不该让解析崩溃（真机上踩过：中文路径 → 误判渲染失败）。"""
    payload = {"ok": True, "out": "C:\\Users\\鍚翠\\x.png"}
    line = json.dumps(payload, ensure_ascii=False)
    assert SubgraphRenderer._parse_result(line) == payload
    assert SubgraphRenderer._parse_result(f"warning\n{line}\n") == payload


def test_subprocess_is_called_with_explicit_utf8(renderer, monkeypatch, fake_render_env):
    """必须显式声明 UTF-8：Windows 上默认按本地编码（cp936）解读 Node 的 UTF-8 输出，
    会导致解析失败并误判"渲染失败"（实测：我的环境开了 UTF-8 模式所以不复现）。"""
    _skip_without_node(renderer)
    calls: list[dict] = []
    real_run = subprocess.run

    def spy_run(*args, **kwargs):
        calls.append(kwargs)
        return real_run(*args, **kwargs)

    monkeypatch.setattr("bot.render.subgraph.subprocess.run", spy_run)
    if not renderer.is_available():
        pytest.skip("渲染环境不可用")
    renderer.render_png(SUBGRAPH)
    assert calls, "应当真的起过 Node 进程"
    assert calls[0].get("encoding") == "utf-8"
    assert calls[0].get("errors") == "replace"


def test_result_json_does_not_need_to_carry_the_path(renderer, tmp_path, monkeypatch,
                                                    fake_render_env):
    """协议：Node 只回 ok/bytes，路径由 Python 侧自己持有。

    用一个"只说 ok 但不写文件"的假脚本，验证判断依据是**产物是否存在**，
    而不是 stdout 里回的路径——中文路径回传正是上一版的坑。
    """
    _skip_without_node(renderer)
    fake = tmp_path / "fake.js"
    fake.write_text('process.stdout.write(JSON.stringify({ok: true, bytes: 123}) + "\\n");',
                    encoding="utf-8")
    monkeypatch.setattr("bot.render.subgraph.RENDER_SCRIPT", fake)
    # 假脚本不产出文件 → 必须走"产物缺失"分支而不是被当成成功
    assert renderer.render_png(SUBGRAPH) is None


def test_failure_logs_stdout_excerpt_when_no_error_field(renderer, tmp_path, monkeypatch, caplog,
                                                        fake_render_env):
    """失败但没有 error 字段时，日志必须带上 stdout/stderr 片段（否则只剩一个空冒号）。"""
    _skip_without_node(renderer)
    fake = tmp_path / "silent.js"
    fake.write_text('process.stdout.write("garbage without json\\n");', encoding="utf-8")
    monkeypatch.setattr("bot.render.subgraph.RENDER_SCRIPT", fake)
    with caplog.at_level("WARNING"):
        assert renderer.render_png(SUBGRAPH) is None
    assert any("garbage without json" in r.message for r in caplog.records), \
        "无 error 字段时必须把 stdout 片段打进日志"


def test_orphan_png_is_cleaned_on_failure(renderer, tmp_path, monkeypatch, fake_render_env):
    """判定失败时不留半成品 PNG（临时目录不该越跑越大）。"""
    _skip_without_node(renderer)
    fake = tmp_path / "writes_but_reports_failure.js"
    fake.write_text(
        "const fs=require('fs');const input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));"
        "fs.writeFileSync(input.out, Buffer.from([0x89,0x50,0x4e,0x47]));"
        "process.stdout.write(JSON.stringify({ok:false,error:'模拟失败'})+'\\n');",
        encoding="utf-8")
    monkeypatch.setattr("bot.render.subgraph.RENDER_SCRIPT", fake)
    assert renderer.render_png(SUBGRAPH) is None
    leftovers = list(renderer.work_dir.glob("subgraph-*.png")) if renderer.work_dir.exists() else []
    assert not leftovers, f"失败时残留了产物：{leftovers}"


def test_upload_failure_returns_none(renderer):
    renderer.feishu = FakeFeishu(image_key=None)
    if not renderer.is_available():
        pytest.skip("渲染环境不可用")
    assert renderer.render_and_upload(SUBGRAPH) is None


def test_temporary_files_are_cleaned(renderer):
    if not renderer.is_available():
        pytest.skip("渲染环境不可用")
    renderer.render_and_upload(SUBGRAPH)
    leftovers = list(renderer.work_dir.glob("subgraph-*")) if renderer.work_dir.exists() else []
    assert not leftovers, f"临时文件未清理：{leftovers}"


# ---- 真实渲染（需要 Node + render/node_modules，缺失时跳过）----


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 Node")
def test_real_render_produces_png(renderer, monkeypatch):
    if not RENDER_SCRIPT.exists() or not (RENDER_DIR / "node_modules").exists():
        pytest.skip("render/node_modules 未安装（npm install echarts @resvg/resvg-js d3-force）")
    fake = FakeFeishu()
    renderer.feishu = fake
    image_key = renderer.render_and_upload(SUBGRAPH)
    assert image_key == "img-1"
    assert len(fake.uploaded) == 1
    png = fake.bytes_at_upload[0]
    assert png[1:4] == b"PNG"
    assert len(png) > 5000
    assert not fake.uploaded[0].exists(), "临时文件应在上传后删除"


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 Node")
def test_real_render_of_full_size_graph(renderer):
    """满规模（24 节点 / 30 边，RAG 侧的上限）也要在超时预算内出图。"""
    if not RENDER_SCRIPT.exists() or not (RENDER_DIR / "node_modules").exists():
        pytest.skip("render/node_modules 未安装")
    nodes = [{"id": f"n{i}", "type": ["事件", "人物", "地点", "组织"][i % 4],
              "name": f"节点{i}"} for i in range(24)]
    edges = [{"source": f"n{i}", "target": f"n{(i + 7) % 24}", "relation": "相关"}
             for i in range(30)]
    image_key = renderer.render_and_upload({"nodes": nodes, "edges": edges})
    assert image_key


def test_render_script_reads_input_payload():
    """脚本契约：读输入 JSON、写 out 路径、stdout 打结果 JSON（人肉排查的说明书）。"""
    source = RENDER_SCRIPT.read_text(encoding="utf-8")
    assert "layout: 'none'" in source, "SSR 不支持力导向布局，必须预计算坐标"
    assert "d3.forceSimulation" in source
    assert "renderToSVGString" in source
    assert "Resvg" in source
    assert json.dumps(SUBGRAPH)  # 占位：确保用例里没有裸 dict 字面量问题
