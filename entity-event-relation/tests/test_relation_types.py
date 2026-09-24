"""C-6（EER-9）：关系类型不能靠模糊比对蒙过去。

**修前的实测问题。** 关系类型原来只做模糊比对（`fuzz.ratio > relation_threshold`），
而五个规范的事件-事件关系类型**两两之间的 fuzz.ratio 都是 50**——都带"关系"二字、
4 个字里中 2 个（`2*2/8=50`），全部越过阈值 40。于是标注 `(E1, 顺承关系, E2)`
对上预测 `(E1, 因果关系, E2)` 或 `(E1, 并列关系, E2)` 都判 tp=1：**类型判错照样满分**，
关系指标对这类错误完全不敏感。顺带说明：head/tail 的模糊比对本身是合理的（写法不唯一），
问题只出在**类型**上。

修法：两侧都落在五个规范事件-事件关系类型里时要求**精确相等**；其余情况（自由文本关系名）
仍走模糊比对。这是口径变更，历史关系指标会下降，见第 11 轮实施记录的前后对照。
"""

from pathlib import Path

import pytest

from war_extraction.evaluation import OptimalEvaluator
from war_extraction.utils.normalizer import Normalizer

ANNOTATION_DIR = Path(__file__).resolve().parents[1] / "data" / "annotations"

#: 五个规范事件-事件关系类型两两之间的 fuzz.ratio 都是 50——修前它们互相顶替的原因
CANONICAL_PAIRS_SIMILARITY = 50


@pytest.fixture(scope="module")
def evaluator():
    if not ANNOTATION_DIR.exists():
        pytest.skip("没有标注目录，跳过评估器用例")
    return OptimalEvaluator(annotation_dir=ANNOTATION_DIR)


def test_规范类型之间必须精确相等(evaluator):
    """因果关系 ≠ 顺承关系 ≠ 并列关系，哪怕模糊相似度有 50。"""
    assert evaluator.relation_types_match("顺承关系", "顺承关系") is True
    assert evaluator.relation_types_match("因果关系", "顺承关系") is False
    assert evaluator.relation_types_match("并列关系", "顺承关系") is False
    assert evaluator.relation_types_match("包含关系", "条件关系") is False


def test_自由文本关系名仍走模糊比对(evaluator):
    """事件-地点/人物/组织那类关系名写法不唯一（主战场/战场/地点），模糊比对保留。"""
    assert evaluator.relation_types_match("主战场", "战场") is True        # ratio 80
    assert evaluator.relation_types_match("发生地点", "发生地") is True    # ratio 86
    assert evaluator.relation_types_match("参战方", "交战方") is True      # ratio 67
    # 差得太远仍然不匹配
    assert evaluator.relation_types_match("主战场", "阵亡") is False


def test_一条关系类型判错的三元组不算匹配(evaluator):
    """端到端复现工作单里那张实测表：修前这两条都是 tp=1，修后必须是 0。"""
    mapping = {"牧野之战": "牧野之战", "商纣王东征": "牧野之战"}

    # 标注：顺承关系；预测：因果关系 → 不该算匹配
    wrong = {("牧野之战", "因果关系", "商纣王东征")}
    gold = {("牧野之战", "顺承关系", "商纣王东征")}
    assert evaluator.match_relation_triples(wrong, gold, mapping)["tp"] == 0

    # 预测：并列关系 → 同样不该算匹配
    wrong_parallel = {("牧野之战", "并列关系", "商纣王东征")}
    assert evaluator.match_relation_triples(wrong_parallel, gold, mapping)["tp"] == 0

    # 类型写对 → 才算匹配
    right = {("牧野之战", "顺承关系", "商纣王东征")}
    assert evaluator.match_relation_triples(right, gold, mapping)["tp"] == 1


def test_config覆盖硬编码关系映射(evaluator):
    """硬编码表里"因果关系→顺承关系"这条被 config/relation_types.json 覆盖，最终以后者为准。

    这条断言的意义：提醒"改 optimal_evaluator 里那张硬编码 relation_map 可能看不到效果"
    （第 11 轮 C-6 附带的坑）。真要改关系归一，先看 config/relation_types.json。
    """
    assert evaluator.normalize_relation("因果关系") == "因果关系"
    assert evaluator.normalize_relation("导致") == "因果关系"
    assert evaluator.normalize_relation("顺承关系") == "顺承关系"


def test_五个规范类型的相似度确实是50(evaluator):
    """把"为什么会互相顶替"的数字钉住（口径说明里引用了它，别让它悄悄变）。"""
    from fuzzywuzzy import fuzz

    types = sorted(Normalizer.CANONICAL_EVENT_RELATION_TYPES)
    assert len(types) == 5
    for i, left in enumerate(types):
        for right in types[i + 1:]:
            assert fuzz.ratio(left, right) == CANONICAL_PAIRS_SIMILARITY, (left, right)
