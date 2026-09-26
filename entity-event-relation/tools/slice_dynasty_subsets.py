"""按朝代切出评估子集的原文（一次性脚本，可重复运行）。

边界依据（2026-09-26 与项目负责人确认）：
  - 秦+汉：第二篇「秦汉三国时期战争」篇首起，到「第六章 魏、蜀、吴三国之战」止
    （排除三国）；
  - 唐   ：从「第二章 唐开国之战」起，到「第五章 五代篡位之战」止
    （排除隋与五代）；
  - 明   ：第五篇内「第一章 明朝建立及安边之战」整章。
    两条边界规则靠"按章切"自然满足：「第三节 伐南明诸帝及郑成功抗清复明」
    位于清章（第二章）内，不在明段；「第五节 李自成入京灭明」位于明章内。

切分定位方式：按字符 offset 切（offset 由标题行正则定位，见 README.md 记录）。
不修改源文件，输出到 data/dynasty_subsets/。
"""
from __future__ import annotations

import re
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
SRC = MODULE_ROOT / "data" / "中国历代战争简史.txt"
OUT_DIR = MODULE_ROOT / "data" / "dynasty_subsets"

# (输出名, 起始 offset 或锚点标题, 结束 offset 或锚点标题, 说明)
SLICES = [
    ("秦汉", None, "第六章魏、蜀、吴三国之战", "第二篇篇首（含导言）到三国章前"),
    ("唐", "第二章唐开国之战", "第五章五代篡位之战", "唐开国之战起，到五代章前"),
    ("明", "第一章明朝建立及安边之战", "第二章清人关统一中国之战", "明朝整章"),
]


def _norm(s: str) -> str:
    """标题行里混有全角空格与零宽字符，比对前统一去掉空白。"""
    return re.sub(r"\s+", "", s)


def locate_headings(text: str) -> dict:
    """把 第X章 标题行映射为 {归一标题: 起始 offset}。"""
    found = {}
    for m in re.finditer(r"^第[一二三四五六七八九十]+章[^\n]*$", text, re.M):
        found[_norm(m.group())] = m.start()
    return found


def resolve(token, headings: dict) -> int:
    """token 为 None 时返回 0（篇首），否则按标题归一后查 offset。"""
    if token is None:
        return 0
    key = _norm(token)
    if key not in headings:
        raise KeyError(f"找不到标题：{token}")
    return headings[key]


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    headings = locate_headings(text)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    total = 0
    lines = []
    for name, start_tok, end_tok, note in SLICES:
        if name == "秦汉":
            start = text.index("第二篇")  # 篇首，含篇导言
        else:
            start = resolve(start_tok, headings)
        end = resolve(end_tok, headings)
        if end <= start:
            raise ValueError(f"{name}: 结束位置早于起始位置")
        body = text[start:end]
        out = OUT_DIR / f"{name}.txt"
        out.write_text(body, encoding="utf-8")
        total += len(body)
        lines.append((name, start, end, len(body), note))
        print(f"{name:<4} offset {start:>7}-{end:<7} {len(body):>7} 字符 -> {out.name}")

    print(f"\n合计 {total} 字符，占全书 {total / len(text):.1%}（全书 {len(text)}）")

    readme = OUT_DIR / "README.md"
    rows = "\n".join(
        f"| {n} | {s} | {e} | {ln} | {note} |" for n, s, e, ln, note in lines
    )
    readme.write_text(
        f"""# 朝代子集原文（评估参考集用）

由 `tools/slice_dynasty_subsets.py` 从 `../中国历代战争简史.txt` 按字符 offset 切出，
**不修改源文件**，可重复运行（结果逐字节一致）。

| 子集 | 起始 offset | 结束 offset | 字符数 | 边界说明 |
| --- | ---: | ---: | ---: | --- |
{rows}

合计 {total} 字符，占全书 {total / len(text):.1%}。

## 边界依据（2026-09-26 确认）

- **不按篇切，按章切**：第五篇篇名写作「宋元时期战争」，但宋、明、清都在这一篇里，
  篇名不可靠；所以明段的起止都用章标题定位。
- **两条明段规则**（由"按章切"自然满足，无需再切节）：
  - 「伐南明诸帝及郑成功抗清复明」是**清章**的节（offset 266497），不在明段内 → 抗清复明不算；
  - 「李自成入京灭明」是明章第五节（offset 255504），在明段内 → 计入。
- **秦汉段含第二篇篇导言**（为给第一章提供时代背景）。注意：导言属于"时期概述"，
  按已定的事件粒度口径（过宽概括不算事件），核验时要留意这一段可能产出**概括型事件**，
  应逐条判掉。同理，唐段不含隋与五代。

## 用法

1. **不要直接拿它跑正式抽取**——它是参考集重建的输入，草稿要等流水线冻结
   （见 `docs/第一轮核验与第二轮整改方案.md` 的 C-2）。
2. 核验人员按这里的字符 offset 回原文定位：
   `子集内 offset + 起始 offset = 全书 offset`。
3. 事件粒度、证据口径以 `data/annotations/README.md` 为准。
""",
        encoding="utf-8",
    )
    print(f"已写 {readme}")


if __name__ == "__main__":
    main()
