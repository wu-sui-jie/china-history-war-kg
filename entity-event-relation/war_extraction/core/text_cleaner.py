"""
输入文本清洗与章节结构解析。

**为什么需要它。** 输入是书籍转换出来的长文本，问题有三类，都会一路传导到抽取结果：
① OCR 错字（`日军` → `目军`）；② 行内硬换行把句子切断（书籍 txt 每行一个硬换行，
分段器按 1800 字切时会把半句切在两段里）；③ 章节标题、总结段与具体战例混在同一段输入里。
抽取前不做处理，就等于把这些噪声当成"模型抽错了"来记账。

**清洗口径**：只做**有据可查**的修复，不改写内容、不做同义替换、不做断句重排：

1. 清掉零宽字符与全角空格等不可见字符；
2. 把行内硬换行合并（保留空行作为段落边界）——这一步让"句子不再被行折断"；
3. 应用 `config/text_cleaning.json` 里的错字订正表（明确的一对一替换，逐条可查）；
4. 识别章节标题位置（只定位、不删改），供分段器优先在这些位置切分。

清洗结果与统计随产物 metadata 记录（`text_cleaning`），所以"这一次到底洗了什么"
不需要靠人回忆；`--no-clean` 可关闭清洗。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

__all__ = [
    "DEFAULT_CLEANING_RULES",
    "SourceMapping",
    "load_cleaning_rules",
    "clean_text",
    "clean_text_with_mapping",
    "chapter_headings",
    "paragraph_index",
]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "text_cleaning.json"

#: 零宽字符与不可见字符
_INVISIBLE = re.compile(r"[\u200b-\u200f\u2028\u2029\ufeff\xa0]")
#: 行内换行：前一行没有以句末标点结束、后一行不是空行/标题 → 视为被折断的同一句。
#: 用"行尾非句末标点"作为判断依据，比"无脑合并所有换行"保守：真正的段落结束
#: 通常带句末标点，不会被合并掉。
_LINE_BREAK = re.compile(r"(?<![。！？；：!?;:])\n(?=[^\n])")
#: 章节标题：`第一章`、`第十二节`、`第一编`、`第三卷`、`一、`、`（一）`、`第1章` 等。
#: `编/卷/回` 也要收进来——书籍的顶层划分常写成"第一编"，漏了它顶层边界就找不到。
_CHAPTER_HEADING = re.compile(
    r"^[ \t　]*(?:第[一二三四五六七八九十百零〇\d]+[章节篇部编卷回]|"
    r"[一二三四五六七八九十百]+[、.．]|"
    r"[（(][一二三四五六七八九十百\d]+[)）])",
    re.MULTILINE,
)


def load_cleaning_rules(path: Path = None) -> Dict:
    """
    读取清洗规则；缺项用默认值补齐。

    `ocr_fixes` 是"错字 → 正字"的显式对照表，只有**确知**的才写进来：
    猜出来的替换会静默改掉原文，比 OCR 错字更难查。
    """
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    rules = json.loads(json.dumps(DEFAULT_CLEANING_RULES))
    if not target.exists():
        print(f"  [配置缺失] {target} 不存在：文本清洗按代码内默认规则运行")
        return rules
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [配置损坏] {target} 无法解析（{exc}）：文本清洗按代码内默认规则运行")
        return rules
    for key, value in DEFAULT_CLEANING_RULES.items():
        if key not in data:
            continue
        got = data[key]
        if key == "ocr_fixes":
            # 空表是合法配置（且默认就是空表），不能因为"假值"跳过
            if isinstance(got, dict):
                rules[key] = got
            continue
        # 布尔开关必须接受 **显式 false**：原先的条件是 `isinstance(got, type(value)) and got`，
        # 而 `and got` 让 `false` 被当成"没配"直接跳过——于是
        # `merge_soft_line_breaks: false` / `strip_invisible_chars: false` **关不掉**，
        # 配置里写的是"不清洗"，跑起来照样清洗。这类"配置写了不生效"最难查。
        if isinstance(got, bool):
            rules[key] = got
    return rules


#: 代码内默认值。`ocr_fixes` 为空是**有意**的：没有确知的对照表时宁可不改字，
#: 具体错字由使用者在 `config/text_cleaning.json` 里逐条登记。
DEFAULT_CLEANING_RULES: Dict = {
    "merge_soft_line_breaks": True,
    "strip_invisible_chars": True,
    "ocr_fixes": {},
}


def clean_text(text: str, rules: Dict = None) -> Tuple[str, Dict]:
    """
    清洗文本并返回 (清洗后文本, 统计)。

    统计里记的是"改了多少处"，不是"改了什么"——逐条明细在日志里，
    完整对照在配置文件中，产物 metadata 只需要能说明"这次清洗是否生效、影响多大"。
    """
    rules = rules or load_cleaning_rules()
    stats = {
        "input_length": len(text or ""),
        "invisible_chars_removed": 0,
        "soft_line_breaks_merged": 0,
        "ocr_fixes_applied": 0,
        "output_length": 0,
    }
    if not text:
        return text or "", stats

    cleaned = text
    if rules.get("strip_invisible_chars", True):
        cleaned, count = _INVISIBLE.subn("", cleaned)
        stats["invisible_chars_removed"] = count

    if rules.get("merge_soft_line_breaks", True):
        cleaned, count = _LINE_BREAK.subn("", cleaned)
        stats["soft_line_breaks_merged"] = count

    ocr_fixes = rules.get("ocr_fixes") or {}
    for wrong, right in ocr_fixes.items():
        if wrong and wrong in cleaned:
            count = cleaned.count(wrong)
            cleaned = cleaned.replace(wrong, right)
            stats["ocr_fixes_applied"] += count

    stats["output_length"] = len(cleaned)
    return cleaned, stats


def chapter_headings(text: str) -> List[Tuple[int, str]]:
    """
    章节标题的 (位置, 标题文本) 列表。只定位，不改动文本。

    用途是让分段器优先在章节边界处切分——按字符数硬切会把一章的开头切到上一段末尾，
    而每一段是独立送进模型的，"上一段末尾的半句 + 下一段的开头"这种切法直接损失上下文。
    """
    headings = []
    for match in _CHAPTER_HEADING.finditer(text or ""):
        line_end = text.find("\n", match.start())
        title = text[match.start():line_end if line_end >= 0 else len(text)].strip()
        headings.append((match.start(), title))
    return headings


def paragraph_index(text: str, position: int) -> int:
    """
    字符位置所属的段落序号（从 1 开始，按空行/换行切段）。

    给每个分段一个可复现的段落 ID：分段本身带的是字符区间，而"这段来自原文第几段"
    在人工核验（抽样回原文判定）时才是能定位的坐标。
    """
    if not text:
        return 1
    prefix = text[:max(position, 0)]
    return prefix.count("\n") + 1

# --------------------------------------------------------------------- 清洗后 → 原文 的位置映射
#
# **为什么需要它。** 清洗会**改变长度**（删不可见字符、合并行内换行、替换错字），
# 于是"清洗后文本的第 N 个字符"与"原文的第 N 个字符"是两回事。而抽取链拿到的是清洗后的
# 文本，它给出的分段起止位置全是清洗后的坐标——这些坐标**无法直接用来回原文定位**，
# 改成 --no-clean 跑一遍又是另一套坐标（实测同一位置的段落号不同）。
# 有了映射，分段就能带一个真正的原文坐标（`ChunkExtraction.source_offset`），
# 人工抽检、错误分析、逐条核验才有共同的参照点。


@dataclass
class SourceMapping:
    """
    清洗后文本 → 原文 的位置映射。

    `positions[i]` = 清洗后第 `i` 个字符对应的原文下标；末位是"文本结尾"的哨兵
    （长度 = 清洗后长度 + 1）。被删掉的字符不占位置，替换产生的字符全部归到替换起点
    （所以取 `to_original` 得到的是"这一段文字在原书里从哪开始"，不是逐字符精确对齐——
    用来定位段落足够了）。
    """

    positions: List[int] = field(default_factory=list)
    original_length: int = 0

    def to_original(self, index: int) -> int:
        """清洗后下标 → 原文下标（越界时夹到两端）。"""
        if not self.positions:
            return max(index, 0)
        if index < 0:
            return self.positions[0]
        if index >= len(self.positions):
            return self.positions[-1]
        return self.positions[index]

    def __bool__(self) -> bool:  # 空映射直接当假值用，免得各处再判 None
        return bool(self.positions)


def _remove_matches(text: str, positions: List[int], pattern) -> Tuple[str, List[int], int]:
    """删掉 `pattern` 命中的字符，返回 (新文本, 新位置数组, 删除字符数)。"""
    matches = list(pattern.finditer(text))
    if not matches:
        return text, positions, 0
    cut = set()
    for match in matches:
        cut.update(range(match.start(), match.end()))
    out_chars: List[str] = []
    out_positions: List[int] = []
    for index, char in enumerate(text):
        if index in cut:
            continue
        out_chars.append(char)
        out_positions.append(positions[index])
    out_positions.append(positions[len(text)])
    return "".join(out_chars), out_positions, len(cut)


def _replace_all(text: str, positions: List[int], wrong: str, right: str) -> Tuple[str, List[int], int]:
    """把 `wrong` 全部替换成 `right`，返回 (新文本, 新位置数组, 替换次数)。"""
    if not wrong or wrong not in text:
        return text, positions, 0
    out_chars: List[str] = []
    out_positions: List[int] = []
    count = 0
    index = 0
    while index < len(text):
        if text.startswith(wrong, index):
            out_chars.append(right)
            out_positions.extend([positions[index]] * len(right))
            index += len(wrong)
            count += 1
            continue
        out_chars.append(text[index])
        out_positions.append(positions[index])
        index += 1
    out_positions.append(positions[len(text)])
    return "".join(out_chars), out_positions, count


def clean_text_with_mapping(text: str, rules: Dict = None) -> Tuple[str, Dict, SourceMapping]:
    """
    同 `clean_text`，但额外返回"清洗后 → 原文"的位置映射。

    三步清洗每步都同步维护位置数组，所以映射与清洗结果必然自洽
    （分别算一遍会漂移，那是这类"两套坐标"最容易出的错）。
    """
    rules = rules or load_cleaning_rules()
    stats = {
        "input_length": len(text or ""),
        "invisible_chars_removed": 0,
        "soft_line_breaks_merged": 0,
        "ocr_fixes_applied": 0,
        "output_length": 0,
    }
    if not text:
        return text or "", stats, SourceMapping(positions=[0], original_length=0)

    original_length = len(text)
    current = text
    positions = list(range(original_length + 1))  # 末位哨兵：文本结尾

    if rules.get("strip_invisible_chars", True):
        current, positions, count = _remove_matches(current, positions, _INVISIBLE)
        stats["invisible_chars_removed"] = count

    if rules.get("merge_soft_line_breaks", True):
        current, positions, count = _remove_matches(current, positions, _LINE_BREAK)
        stats["soft_line_breaks_merged"] = count

    for wrong, right in (rules.get("ocr_fixes") or {}).items():
        current, positions, count = _replace_all(current, positions, wrong, right)
        stats["ocr_fixes_applied"] += count

    stats["output_length"] = len(current)
    return current, stats, SourceMapping(positions=positions, original_length=original_length)
