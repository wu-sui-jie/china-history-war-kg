"""地名噪声过滤：三条正则必须真的生效。

写错成双反斜杠就会静默失效：

    r".*等\\d+方国$"

raw string 里 `\\` 是两个字符（反斜杠 + 反斜杠），正则引擎把它解释成"一个字面反斜杠"，
于是这三条只能匹配 `等\\d方国` 这种根本不存在的字面量——"等3方国"这类噪声地名一路穿过
过滤，进了地理编码队列。必须是单反斜杠才是"等 + 若干数字 + 方国"。

用例只断言这三条的行为（外加一条对照，确认其它噪声规则没被顺手改坏）。
"""

import pytest

from war_extraction.utils.normalizer import Normalizer


@pytest.fixture(scope="module")
def normalizer():
    return Normalizer()


@pytest.mark.parametrize(
    "noise",
    [
        "夏等3方国",     # 等\d+方国$
        "商等12国",      # 等\d+国$
        "羌等5部落",     # 等\d+部落$
        "羌方等30部落",
    ],
)
def test_place_names_with_counts_are_noisy(normalizer, noise):
    """带"等 N 个方国/国/部落"的地名是描述而非地名，必须判为噪声。"""
    assert normalizer.is_noisy_place_name(noise) is True


@pytest.mark.parametrize(
    "real_name",
    [
        "方国",       # 只有"方国"两字，不构成"等 N 方国"
        "鬼方",
        "三苗",
        "牧野",
        "殷墟等遗址",  # 结尾不是方国/国/部落，不该被这三条误伤
    ],
)
def test_ordinary_place_names_are_kept(normalizer, real_name):
    """正常地名不能被这三条正则误判成噪声。"""
    assert normalizer.is_noisy_place_name(real_name) is False


@pytest.mark.parametrize(
    "noise",
    [
        "今河南一带",   # ^今.+一带$
        "渭水流域",     # .+流域$
        "中原",         # 直接命中
        "汉水与渭水之间",  # .+之间$
    ],
)
def test_other_noise_rules_still_work(normalizer, noise):
    """对照：其余噪声规则的行为不变（改动只应影响那三条正则）。"""
    assert normalizer.is_noisy_place_name(noise) is True
