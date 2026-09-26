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

**注意**：fixture 要进公开仓库，所以默认只录**合成短文本**（内置默认值），别拿原书段落去录——
原书受版权约束、已 gitignore；提示词也只存角色标签与长度，不存全文。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import llm_pipeline  # noqa: E402

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


def _stage_of(prompt: str) -> str:
    for label, marker in STAGE_MARKERS:
        if marker in prompt:
            return label
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="录制抽取链回放 fixture")
    parser.add_argument("--text", help="直接给输入文本")
    parser.add_argument("--text-file", help="从文件读输入文本")
    parser.add_argument("--model", default="deepseek-r1:7b")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

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
