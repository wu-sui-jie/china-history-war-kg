"""抽样核验表的上下文扩全（`tools/expand_review_context.py`）。

**为什么值得钉。** 这张表是 B 组人工判定的唯一入口，而它有两个容易悄悄坏掉的地方：

1. **`定位键` 必须与冻结样本逐行一致**——判定结果靠它映射回
   `sample_<seed>.json` 那批基准；只要有一个键变了，跨版本对比就对不上，
   而且这种错**不会报错**，只是在将来比对时少一行、多一行；
2. **"未能定位"不许编造上下文**——给一段别人的原文比留空更糟：
   判的人会照着错的原文判"错"，这个错误会直接进精确率。
   同理，定位到的上下文**必须真的包含产物的证据**（含"模型删了括号"这种情况）。

用例读真实文件（原文、冻结样本、产物），因为这里要钉的正是"真实数据上的定位行为"。
"""
import csv
import json
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.expand_review_context import (  # noqa: E402
    REVIEW_COLUMNS,
    build_rows,
)

FROZEN_JSON = MODULE_ROOT / "evaluation" / "review" / "sample_20260926.json"
CONTEXT_CSV = MODULE_ROOT / "evaluation" / "review" / "sample_20260926_with_source.csv"
CONTEXT_JSON = MODULE_ROOT / "evaluation" / "review" / "sample_20260926_with_source.json"
BOOK = MODULE_ROOT / "data" / "中国历代战争简史.txt"
PRED = MODULE_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"


def _rows():
    if not CONTEXT_CSV.is_file():
        pytest.skip("还没有生成含原文上下文的核验表")
    with open(CONTEXT_CSV, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_定位键与冻结样本逐行一致():
    """
    表的定位键必须与冻结样本**一个不差、顺序也对**——判定结果要映射回那批基准定位。

    只比集合是不够的：行数一样、内容一样、顺序乱了，人工按行填完之后再回填就会错位。
    """
    if not (FROZEN_JSON.is_file() and CONTEXT_JSON.is_file()):
        pytest.skip("缺少样本文件")
    frozen = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))
    fresh = json.loads(CONTEXT_JSON.read_text(encoding="utf-8"))
    assert [r["定位键"] for r in fresh["records"]] == [r["定位键"] for r in frozen["records"]]
    assert [r["记录摘要"] for r in fresh["records"]] == [r["记录摘要"] for r in frozen["records"]]


def test_生成表的三列是空的而原表内容没丢():
    """
    判定/判据/备注三列必须留给填表人；产物证据要一字不动地保留下来。

    **这条查的是"生成器刚产出的行"，不是仓库里那份表。** 那份表已经被人填过——
    人工判定是有价值的产出（`tools/summarize_review.py` 就是读它算精确率的），
    不是"表被弄脏了"，所以拿"三列为空"断言仓库文件会在填完之后恒红。
    该恒红的是生成侧：生成器一旦预填，判的人就会照着提示填。
    """
    frozen = json.loads(FROZEN_JSON.read_text(encoding="utf-8"))["records"][:2]
    rows, _provenance = build_rows(frozen, BOOK, PRED)
    assert len(rows) == len(frozen), "生成器不许悄悄丢行"
    for row, original in zip(rows, frozen):
        assert row["判定（对/错/无法判断）"] == ""
        assert row["判据"] == ""
        assert row["备注"] == ""
        assert row["产物证据"] == original["原文上下文"], "原表那一列的内容必须原样保留"
    assert list(rows[0].keys()) == REVIEW_COLUMNS


def test_上下文含前一段_时间类判据可见():
    """
    验收口径（这条是为"判时间"立的）：`崤底之战` 的产物证据段里**没有年份**，
    时间写在前一段；扩全后的上下文必须能把 `公元27年` 带进来，并且用 〈〉 标出证据位置。
    """
    rows = _rows()
    row = next((r for r in rows if "崤底之战" in r["记录摘要"]), None)
    if row is None:
        pytest.skip("样本里没有这条记录")
    context = row["原文上下文"]
    assert "公元27年" in context, "时间在上一段，扩展窗口必须带进来"
    assert "〈" in context and "〉" in context, "证据位置要用 〈〉 标出来"


def test_模型删掉括号也仍能定位():
    """
    模型写证据时会删掉括号里的内容（实测 `辽西渔阳之战`）：证据是"袭掠辽西，杀太守"，
    原文是"袭掠辽西（治所辽宁义县西），杀太守"。只比开头会在第 13 个字上断掉、
    整行被误判成"未能定位"——所以定位要分几处抽样例比。
    """
    rows = _rows()
    row = next((r for r in rows if "辽西渔阳" in r["定位键"]), None)
    if row is None:
        pytest.skip("样本里没有这条记录")
    assert row["定位方式"] == "记录证据", row["定位方式"]
    assert "燕兵救援" in row["原文上下文"]


def test_标为记录证据的行_证据确实在上下文里():
    """
    凡是标成 `记录证据` 的行，上下文里至少得有证据的**两段**（四处抽样：0、1/5、2/5、3/5）——
    这正是工具打这个标签的条件，标签与给人的东西必须对得上。
    否则判的人会照着错的原文判"错"，这个错误会直接进精确率。

    两处细节让断言成立得干净：

    - **先剔掉 `〈 〉` 标记**：它们插在证据中间，不剔就会把证据切成两半
      （这是本用例第一版踩过的坑，不是工具的缺陷）；
    - **只要求 ≥2 段而不是全部**：产物证据本身可能有问题——实测 `entity:person|骆甲|西汉`
      的证据是"乃利用秦的甲士李必、骆甲利用秦的甲士李必、骆甲，迅速组建一支新骑兵"，
      **自己重复了一遍**，所以前半截在原文里根本不存在。这类"证据形态有问题"该写进备注，
      不该让定位函数去背。
    """
    rows = _rows()
    checked = 0
    for row in rows:
        if row["定位方式"] != "记录证据":
            continue
        evidence = "".join((row["产物证据"] or "").split())
        context = row["原文上下文"].replace("〈", "").replace("〉", "")
        starts = [0, len(evidence) // 5, 2 * len(evidence) // 5, 3 * len(evidence) // 5]
        chunks = [evidence[start:start + 24] for start in dict.fromkeys(starts)]
        chunks = [chunk for chunk in chunks if len(chunk) >= 12]
        if len(chunks) < 2:
            continue
        found = sum(1 for chunk in chunks if chunk in context)
        assert found >= 2, (
            f"{row['定位键']}：证据的四段里只有 {found} 段在上下文里，标成『记录证据』不成立"
        )
        checked += 1
    assert checked > 200, f"至少该有 200 行能这样核对，实际 {checked}"


def test_未能定位时不编造上下文():
    """定位不到就留空并写明方式，绝不退回一段"看起来像"的原文。"""
    record = {
        "分层": "事件:其他朝代",
        "定位键": "event|查无此战|虚构|9999年|虚构地",
        "记录摘要": "事件 查无此战（虚构 9999年）",
        "原文上下文": "这是一段原文里绝对不存在的证据文本，用来验证定位失败时的行为。",
    }
    if not BOOK.is_file():
        pytest.skip("没有原文")
    # 直接用真实原文做 haystack，省掉一次清洗（这段逻辑只依赖 haystack 字符串）
    raw = BOOK.read_text(encoding="utf-8")
    from tools.expand_review_context import collapse, locate_record as locate

    haystack, _index = collapse(raw)
    hit, way = locate(record, haystack, {})
    assert hit is None
    assert way.startswith("未能定位")
    # 有真实事件 source_text 时也不许把别的事件当成它
    hit2, way2 = locate(record, haystack, {"查无此战": ""})
    assert hit2 is None and way2.startswith("未能定位")


def test_定位方式只有四档():
    """四档之外的定位方式说明有人改坏了分支（名称定位那档会带括号说明，按前缀比）。"""
    allowed = ("记录证据", "所属事件证据", "名称定位", "未能定位")
    rows = _rows()
    for row in rows:
        assert row["定位方式"].startswith(allowed), row["定位方式"]


def test_生成结果覆盖整个样本():
    """350 行一行不少，且每行都有判定位置（上下文或凭证栏其一有内容）。"""
    rows = _rows()
    assert len(rows) == 350
    for row in rows:
        assert row["原文上下文"] or row["产物证据"], row["定位键"]


def test_build_rows_可独立调用(tmp_path):
    """`build_rows` 不依赖命令行与固定路径：给一份最小样本就能跑（便于以后换样本）。"""
    if not (BOOK.is_file() and PRED.is_file()):
        pytest.skip("缺少原文或产物")
    records = [{
        "分层": "事件:东汉", "定位键": "event|崤底|东汉|27年|崤底",
        "记录摘要": "事件 崤底之战（东汉 27年）",
        "原文上下文": "赤眉军击败邓禹、冯异后，部队饥疲不堪",
    }]
    rows, provenance = build_rows(records, BOOK, PRED)
    assert len(rows) == 1
    assert provenance["located"] == 1
    assert provenance["book_sha256"]
    assert "公元27年" in rows[0]["原文上下文"]
