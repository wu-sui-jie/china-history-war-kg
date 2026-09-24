"""EER-15：同输入两次评估必须给出同一份结果（评估可复现）。

**为什么需要这组用例。** 第 7 轮实测发现"同一 pred / gold / config 连跑两次，关系抽取
的 tp/fp/fn 与 F1 不同"，但第 8 轮复核跑了**两个独立进程各一次**，看到两边逐字段一致，
于是把它降级成"只有 error_samples 不可复现"。问题在**样本量**：漂移是"某些哈希种子下
相似度并列的取舍不同"，两次完全可能碰巧落在同一个结果上（第 9 轮三颗种子给出 3 个不同
tp 值，只抽 2 次有约 1/3 概率抽到同一对），而且那两次没有显式控制 `PYTHONHASHSEED`。
第 9 轮用三颗**显式**种子复测，指标级漂移真实存在：

    seed 0: filtered_pred=888 tp=739 fp=149 fn=360 F1=0.7438
    seed 1: filtered_pred=888 tp=743 fp=145 fn=356 F1=0.7479
    seed 2: filtered_pred=888 tp=741 fp=147 fn=358 F1=0.7458

根因是贪心一对一匹配直接迭代 `filter_relations` / `build_gold_triples` 返回的**集合**，
而 Python 的字符串哈希每进程随机化——换个进程就换一种匹配顺序，相似度并列时的取舍随之
改变，tp/fp/fn 就跟着动。

因此本套用例刻意从两个方向钉住：

1. `test_greedy_matching_ignores_input_order`：直接对匹配函数喂不同顺序的同两个集合，
   断言结果逐字段一致。进程内、确定性，针对**指标级**漂移（修前必失败，且失败信息直接
   指出是 tp 变了）。
2. `test_relation_evaluation_is_seed_independent`：用最小真实夹具、开 4 个不同
   PYTHONHASHSEED 的子进程跑**完整** `evaluate_relations`，断言返回结果完全一致。
   覆盖到 1 之外的地方（`filter_relations` 的集合构造、错误样例的取样与排序）。

刻意不用的做法：给 CI 钉一个固定 PYTHONHASHSEED。那是把症状藏起来——真实用户机器上的
种子由解释器随机决定，钉死只会让"本机复现不了"变成常态。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
ANNOTATION_DIR = MODULE_ROOT / "data" / "annotations"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "relation_slice_repro.json"

# 两个集合内容相同、顺序不同的输入，必须得到同一份匹配结果。
# 用例构造：gold 里 AAAA / BBBB 两条，pred 里有一条 AABB 与一条 BBBB。
#   - P1=(E1,指挥,AABB) 与两条 gold 的相似度完全并列（尾实体 fuzz.ratio 都是 75），
#     取谁取决于遍历顺序；
#   - P2=(E1,指挥,BBBB) 只能配 G2（尾实体与 G1 的相似度 0，低于阈值）。
# 于是谁先被取走决定 P2 还能不能配上：按 AAAA→BBBB 的顺序 tp=2，按 BBBB→AAAA 的顺序
# tp=1。修前把 gold 顺序反过来就能看到 tp 变化；修后两种顺序必然一致。
_TIE_HEAD = "E1"
_TIE_RELATION = "指挥"
_GOLD_TRIPLES = [(_TIE_HEAD, _TIE_RELATION, "AAAA"), (_TIE_HEAD, _TIE_RELATION, "BBBB")]
_PRED_TRIPLES = [(_TIE_HEAD, _TIE_RELATION, "AABB"), (_TIE_HEAD, _TIE_RELATION, "BBBB")]
_EVENT_MAPPING = {_TIE_HEAD: _TIE_HEAD}


@pytest.fixture(scope="module")
def evaluator():
    from war_extraction.evaluation.optimal_evaluator import OptimalEvaluator

    return OptimalEvaluator(annotation_dir=ANNOTATION_DIR)


def test_greedy_matching_ignores_input_order(evaluator):
    """贪心匹配只应取决于两个集合的内容，不应取决于它们的迭代顺序。"""
    orders = {
        "升序": (sorted(_PRED_TRIPLES), sorted(_GOLD_TRIPLES)),
        "降序": (sorted(_PRED_TRIPLES, reverse=True), sorted(_GOLD_TRIPLES, reverse=True)),
        "一升一降": (sorted(_PRED_TRIPLES), sorted(_GOLD_TRIPLES, reverse=True)),
        "集合": (set(_PRED_TRIPLES), set(_GOLD_TRIPLES)),
    }
    results = {
        name: evaluator.match_relation_triples(pred, gold, _EVENT_MAPPING)
        for name, (pred, gold) in orders.items()
    }

    baseline = results["升序"]
    # 先确认这个用例不是空跑：并列项确实两条都能配上，且都配上了
    assert baseline["tp"] == 2, (
        f"夹具本身失效（tp={baseline['tp']}）：并列/阈值条件与用例注释不符，请重新构造"
    )
    assert baseline["gold_list"] == sorted(_GOLD_TRIPLES), "金标候选未按内容定序"
    assert baseline["unmatched_predictions"] == []

    for name, got in results.items():
        assert got == baseline, (
            f"输入顺序为「{name}」时匹配结果与「升序」不同："
            f"tp={got['tp']} vs {baseline['tp']}、"
            f"未匹配预测={got['unmatched_predictions']} vs {baseline['unmatched_predictions']}"
        )


# 子进程里跑的是真实代码路径：从仓库数据目录读标注、走 filter_relations +
# build_gold_triples + 贪心匹配 + 错误样例组装。用 ensure_ascii 输出，避免
# Windows 控制台默认编码（cp936）把 stdout 里含中文的 JSON 读坏。
_RUNNER = """
import json, sys
from pathlib import Path
root = Path(sys.argv[1]); fixture = Path(sys.argv[2])
sys.path.insert(0, str(root))
from war_extraction.evaluation.optimal_evaluator import OptimalEvaluator
data = json.load(open(fixture, encoding="utf-8"))
evaluator = OptimalEvaluator(annotation_dir=root / "data" / "annotations")
result = evaluator.evaluate_relations(data["pred_relations"], data["event_mapping"])
print(json.dumps(result, ensure_ascii=True, sort_keys=True))
"""


def _run_in_subprocess(seed: str) -> dict:
    env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", _RUNNER, str(MODULE_ROOT), str(FIXTURE)],
        cwd=str(MODULE_ROOT), env=env, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, f"PYTHONHASHSEED={seed} 的子进程失败：\n{proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_relation_evaluation_is_seed_independent():
    """完整的关系评估在 4 个哈希种子下必须给出逐字段相同的结果。"""
    results = {seed: _run_in_subprocess(seed) for seed in ("0", "1", "2", "3")}

    baseline = results["0"]
    for seed, got in results.items():
        for field in ("counts", "error_samples", "precision", "recall", "f1"):
            assert got[field] == baseline[field], (
                f"PYTHONHASHSEED={seed} 与 =0 的 {field} 不同：\n"
                f"  {seed}: {got[field]}\n  0: {baseline[field]}"
            )
