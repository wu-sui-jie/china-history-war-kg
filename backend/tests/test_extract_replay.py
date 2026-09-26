"""`/api/extract/entities-events` 抽取链的**录制回放**回归。

**为什么需要这组用例。** 这个端点的行为不能只靠快照兜底：`tools/snapshot_responses.py`
刻意跳过它（"结果不确定，放进来只会制造噪音"），"实体对象被当 place_list 渲染进提示词"
这类 bug 正是从这儿漏过去的。三层防护各管一段，别指望一层包打天下：

| 手段 | 能兜住 | 兜不住 |
| --- | --- | --- |
| `test_extract_prompt.py`（桩 LLM 断言提示词内容） | 提示词被塞进 repr、参数传错 | 序列化与后处理的变化 |
| **本组用例（录制回放）** | 序列化、字段归一、后处理的回归 | 提示词变了但回答是罐头 → 载荷不变 |
| 真实运行 + 人工看 | 抽取质量 | 不可在 CI 复现 |

夹具 `fixtures/extract_replay.json` 是用本机 Ollama 对一段**合成短文本**真跑一次
`extract_all_optimized` 录下来的（录制脚本 `tools/record_extract_replay.py`）：
`llm_responses` 是每次 `llm.call` 的原样返回，`expected_payload` 是同一次运行经
`serialize_extraction_result` 得到的 data 段。回放时不联网、不花额度。

判据是**逐字段精确相等**，差异按 JSON 路径列出（`_diff_payload`），而不是一个笼统的
`assert payload == expected`——后者报错时看不出差在哪，事后没法判断是不是预期变化。
"""

import json
from pathlib import Path

import pytest

import llm_pipeline

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "extract_replay.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class _ReplayLLM:
    """按录制顺序回放 LLM 返回，并记录每次调用被识别出的阶段标签。"""

    model = "replay"

    def __init__(self, responses):
        self._responses = list(responses)
        self.cursor = 0
        self.stages = []

    def call(self, prompt, temperature=0.1, max_retries=3, json_mode=False):
        index = self.cursor
        if index >= len(self._responses):
            raise AssertionError(
                "回放用的返回不够了：抽取链比录制时多调了 %d 次 LLM。"
                "是编排变了还是新增了阶段？确认后重新录制 "
                "（python tools/record_extract_replay.py）。" % (index + 1 - len(self._responses)))
        self.cursor += 1
        entry = self._responses[index]
        self.stages.append(entry.get("stage"))
        return entry["response"]


def _diff_payload(expected, actual, path=""):
    """逐字段比对，返回差异行（格式与 tools/snapshot_responses.py 的 diff 一致）。"""
    lines = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            sub = "%s.%s" % (path, key) if path else key
            if key not in expected:
                lines.append("+ %s: 新增 = %s" % (sub, json.dumps(actual[key], ensure_ascii=False)[:120]))
            elif key not in actual:
                lines.append("- %s: 缺失（原 = %s）" % (sub, json.dumps(expected[key], ensure_ascii=False)[:120]))
            else:
                lines.extend(_diff_payload(expected[key], actual[key], sub))
        return lines
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            lines.append("! %s: 长度 %d -> %d" % (path, len(expected), len(actual)))
        for i, (x, y) in enumerate(zip(expected, actual)):
            lines.extend(_diff_payload(x, y, "%s[%d]" % (path, i)))
        return lines
    if expected != actual:
        lines.append("! %s: %s -> %s" % (path,
                                         json.dumps(expected, ensure_ascii=False)[:120],
                                         json.dumps(actual, ensure_ascii=False)[:120]))
    return lines


@pytest.fixture(scope="module")
def replay_run():
    """回放一次录制，返回 (载荷, 回放器, 诊断)。"""
    fixture = _load_fixture()
    llm = _ReplayLLM(fixture["llm_responses"])
    entities, event_result, relations, diagnostics = llm_pipeline.extract_all_optimized(
        llm, fixture["input_text"])
    # process_time 是运行时值：录制与回放都固定 0.0，否则每次都差在这一项
    payload = llm_pipeline.serialize_extraction_result(entities, event_result, relations, 0.0)
    return payload, llm, diagnostics, fixture


def test_replay_consumes_every_recorded_response(replay_run):
    """回放把录制的返回一条不剩地吃掉——多调/少调 LLM 都要立刻暴露。"""
    _payload, llm, _diagnostics, fixture = replay_run

    assert llm.cursor == len(fixture["llm_responses"]), (
        "抽取链的 LLM 调用次数与录制时不同：录制 %d 次，本次 %d 次。"
        % (len(fixture["llm_responses"]), llm.cursor))


def test_replay_keeps_stage_order(replay_run):
    """阶段调用顺序也要一致——顺序变了往往意味着编排改了。"""
    _payload, llm, _diagnostics, fixture = replay_run

    recorded = [entry["stage"] for entry in fixture["llm_responses"]]
    assert llm.stages == recorded


def test_serialized_payload_matches_recording(replay_run):
    """核心断言：序列化载荷逐字段等于录制值。

    改坏任何一处后处理（字段归一、默认值、去重、序列化字段清单）都会在这里变红。
    """
    payload, _llm, _diagnostics, fixture = replay_run

    differences = _diff_payload(fixture["expected_payload"], payload)
    assert not differences, "序列化载荷与录制值不一致：\n  " + "\n  ".join(differences[:30])


def test_replay_run_still_succeeds(replay_run):
    """顺带确认这次回放没有阶段失败（失败会让上面的载荷比对变成"比两次空结果"）。"""
    _payload, _llm, diagnostics, _fixture = replay_run

    assert diagnostics["stage_ok"] == {"entity": 1, "event": 1, "relation": 1}
    assert diagnostics["partial_errors"] == []


def test_fixture_stays_small_and_synthetic():
    """夹具防呆：输入必须是合成短句（不进原书段落），且不夹带密钥样式的内容。

    仓库是公开的、原书受版权约束（已 gitignore），夹具一带书里的段落就等于公开转载。
    """
    fixture = _load_fixture()

    assert len(fixture["input_text"]) <= 200, "夹具输入应是一句合成短文本，不是原书段落"
    blob = json.dumps(fixture, ensure_ascii=False)
    for marker in ("sk-", "AMAP_API_KEY=", "JWT_SECRET", "NEO4J_PASSWORD"):
        assert marker not in blob, "夹具里不该出现密钥或口令：%s" % marker
