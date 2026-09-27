#!/usr/bin/env python3
"""录制 `/api/extract/entities-events` 的抽取链路回放 fixture。

**为什么需要它。** 这个端点的行为不能只靠快照兜底：`tools/snapshot_responses.py`
刻意跳过它（"结果不确定，放进来只会制造噪音"），"实体对象被当 place_list 渲染进提示词"
这类 bug 正是从这儿漏过去的。提示词层面已有
`tests/test_extract_prompt.py` 钉住，但**序列化 / 字段归一 / 后处理**的变化它看不见——
那正是本脚本 + `tests/test_extract_replay.py` 要兜的：

    把一次真实运行里每一次 `llm.call(prompt)` 的原样返回按顺序存进 fixture；
    用例回放同一串返回，断言最终交给前端的 data 段逐字段等于录制值。

**录不了什么**：提示词本身变了但模型回答是罐头，载荷就不变——所以这条测试**查不出提示词问题**
（那是 `test_extract_prompt.py` 的活）。两层各管一段，别指望一层包打天下。

用法（本机 Ollama 在跑时；不需要外网、不花云上额度）：

    cd backend
    python tools/record_extract_replay.py                      # 用内置的合成短文本
    python tools/record_extract_replay.py --text-file /tmp/样例.txt
    python tools/record_extract_replay.py --model deepseek-r1:7b --out tests/fixtures/extract_replay.json
    python tools/record_extract_replay.py --recompute tests/fixtures/extract_replay.json
                                                               # 只按当前后处理重算 expected_payload

**`--recompute` 什么时候用。** 后处理（派生关系、字段归一、发布过滤）改了而 `llm_responses`
没变时，`expected_payload` 会与当前代码不符、`test_extract_replay.py` 变红。
**这时不要重录**：重录会连模型回答一起换掉，把"后处理改了"混进"模型回答变了"，
下一任很难分清是哪一样造成的。`--recompute` 只回放已有的那串返回、按当前代码重算载荷，
`llm_responses` 一个字节都不动——第二轮改发布过滤时就是这么做的。

**注意**：fixture 要进公开仓库，所以默认只录**合成短文本**（内置默认值），别拿原书段落去录——
原书受版权约束、已 gitignore；提示词也只存角色标签与长度，不存全文。
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def _install_py2neo_stub() -> None:
    """
    装一个假的 `py2neo.Graph`——**必须在 `import llm_pipeline` 之前**。

    导入链是 `llm_pipeline → db_handle`，而 `db_handle` 在**模块级**就 `neo4j_db()` 建连接，
    Neo4j 没开时直接抛 `ConnectionUnavailable`。这个工具只跑抽取链（不碰图库），
    所以装个空替身即可。**与 `tests/conftest.py` 的替身同一个做法**（那里有完整说明：
    `Graph.run()` 返回空结果），两份都只在"离线跑得起来"这一件事上起作用。
    """
    if "py2neo" in sys.modules:
        return
    stub = types.ModuleType("py2neo")

    class _EmptyResult:
        def data(self):
            return []

    class _Graph:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            return _EmptyResult()

    stub.Graph = _Graph
    sys.modules["py2neo"] = stub


_install_py2neo_stub()
import llm_pipeline  # noqa: E402
from war_extraction.config import current_time_tag  # noqa: E402

#: 默认输入：自造的短句，覆盖"实体 + 事件 + 关系 + 朝代 + 角色"几条常见路径。
#: 刻意不用原书原文（版权 + 别把长文本塞进 fixture）。
DEFAULT_TEXT = (
    "公元前1046年，周武王率诸侯之师东进，与商纣王的军队战于牧野。"
    "商军阵前倒戈，商纣王大败，商朝灭亡，周武王建立周朝。"
)

#: 与 tests/test_extract_prompt.py 一致的角色句，用来给每次调用打阶段标签
STAGE_MARKERS = [
    ("entity", "实体抽取专家"),
    ("event_identify", "事件识别专家"),
    ("event_type", "事件类型"),
    ("event_full", "事件信息抽取专家"),
    ("relation", "事件关系抽取专家"),
]

DEFAULT_OUT = BACKEND_DIR / "tests" / "fixtures" / "extract_replay.json"


class RecordingLLM:
    """包住真实 LLM 客户端，把每一次调用的原始返回按顺序记下来。"""

    def __init__(self, inner):
        self.inner = inner
        self.model = getattr(inner, "model", "unknown")
        self.calls = []

    def call(self, prompt, temperature=0.1, max_retries=3, json_mode=False):
        response = self.inner.call(prompt, temperature=temperature,
                                   max_retries=max_retries, json_mode=json_mode)
        self.calls.append({
            "stage": _stage_of(prompt),
            "prompt_chars": len(prompt),
            "response": response,
        })
        return response


class ReplayLLM:
    """
    按顺序吐回夹具里记下的 `response`，**不调模型**（`--recompute` 用）。

    与 `tests/test_extract_replay.py` 的回放器同一个职责：调用次数对不上就报出来，
    而不是让后面的断言去猜"为什么载荷不一样"。
    """

    def __init__(self, entries):
        self.entries = entries
        self.cursor = 0

    def call(self, prompt, temperature=0.1, max_retries=3, json_mode=False):
        if self.cursor >= len(self.entries):
            raise AssertionError(
                f"回放用的返回不够了：抽取链比录制时多调了 LLM（已用 {self.cursor} 次，"
                f"录制 {len(self.entries)} 次）——是编排变了还是新增了阶段？"
                "确认后重新录制（去掉 --recompute）")
        entry = self.entries[self.cursor]
        self.cursor += 1
        return entry["response"]


def _stage_of(prompt: str) -> str:
    for label, marker in STAGE_MARKERS:
        if marker in prompt:
            return label
    return "unknown"


def _recompute(fixture_path: Path) -> int:
    """按当前后处理重算 `expected_payload`，`llm_responses` 不动。"""
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    llm = ReplayLLM(fixture["llm_responses"])
    text = fixture["input_text"]

    entities, event_result, relations, diagnostics = llm_pipeline.extract_all_optimized(llm, text)
    payload = llm_pipeline.serialize_extraction_result(entities, event_result, relations, 0.0)

    if llm.cursor != len(fixture["llm_responses"]):
        print(f"警告：回放只吃掉了 {llm.cursor}/{len(fixture['llm_responses'])} 条返回——"
              f"抽取链的调用次数与录制时不同，先确认原因再重算")

    old = fixture.get("expected_payload") or {}
    fixture["expected_payload"] = payload
    # **追加**一条重算记录，不覆盖原有说明：这份 note 里记着本夹具的口径与历史
    # （例如第二轮"在线载荷只带取值合法关系"那一段），覆盖掉就等于把来历删了。
    # 用固定分隔符，重算多次也只留最后一条记录。
    _RECORD_SEP = " ※ 重算记录："
    fixture["_note"] = fixture["_note"].split(_RECORD_SEP)[0].rstrip() + (
        f"{_RECORD_SEP}expected_payload 于 {current_time_tag()[:8]} 由 "
        f"`{Path(__file__).name} --recompute` 按当前后处理重算（`llm_responses` 仍未变）。")
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")

    new_summary = payload.get("summary") or {}
    old_summary = old.get("summary") or {}
    print(f"载荷统计: {old_summary} → {new_summary}")
    print(f"关系条数: 地点 {len(old.get('relations', {}).get('event_place', []))} "
          f"→ {len(payload['relations']['event_place'])}，"
          f"人物 {len(old.get('relations', {}).get('event_person', []))} "
          f"→ {len(payload['relations']['event_person'])}")
    print(f"已写回 {fixture_path}（llm_responses 未改动，仍 {len(fixture['llm_responses'])} 条）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="录制抽取链回放 fixture")
    parser.add_argument("--text", help="直接给输入文本")
    parser.add_argument("--text-file", help="从文件读输入文本")
    parser.add_argument("--model", default="deepseek-r1:7b")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--recompute", default=None, metavar="FIXTURE",
                        help="只按当前后处理重算 expected_payload（回放已有录制，不调模型）")
    args = parser.parse_args()

    if args.recompute:
        return _recompute(Path(args.recompute))

    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text
    else:
        text = DEFAULT_TEXT

    recorder = RecordingLLM(llm_pipeline.OllamaAdapter(model_name=args.model))
    print(f"模型: {args.model}")
    print(f"输入: {text[:60]}{'…' if len(text) > 60 else ''}（{len(text)} 字）")

    entities, event_result, relations, diagnostics = llm_pipeline.extract_all_optimized(
        recorder, text)

    # process_time 是运行时值，录制与回放都固定为 0.0，否则每次对比都会差在这一项
    payload = llm_pipeline.serialize_extraction_result(entities, event_result, relations, 0.0)

    fixture = {
        "_note": (
            "/api/extract/entities-events 抽取链的录制回放夹具。"
            "llm_responses 是用本机 Ollama 对 input_text 真跑一次 extract_all_optimized 时"
            "每次 llm.call 的原样返回（按调用顺序）；expected_payload 是同一次运行经"
            "serialize_extraction_result 得到的 data 段（process_time 固定 0.0）。"
            "用例回放这串返回并逐字段比对 expected_payload——它兜的是序列化 / 字段归一 /"
            "后处理的回归，**兜不住提示词本身的变化**（那由 tests/test_extract_prompt.py 钉住）。"
            "重新录制：python tools/record_extract_replay.py"
        ),
        "recorded_with_model": args.model,
        "input_text": text,
        "llm_responses": recorder.calls,
        "expected_payload": payload,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n阶段调用顺序: {[c['stage'] for c in recorder.calls]}")
    print(f"stage_ok: {diagnostics['stage_ok']}  部分失败: {diagnostics['partial_errors']}")
    print(f"载荷统计: {payload['summary']}")
    print(f"\n已写入 {out_path}（{len(recorder.calls)} 次 LLM 调用，"
          f"{len(json.dumps(fixture, ensure_ascii=False))} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
