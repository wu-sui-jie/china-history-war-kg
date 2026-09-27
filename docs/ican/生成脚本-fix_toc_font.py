#!/usr/bin/env python3
"""把目录（TOC）各级条目的字号改为五号（10.5pt = 21 半磅）。

背景：docx-js 不生成 TOC 条目样式，样式由 add_toc_placeholders.py 补齐，
其 rPr 里没有 w:sz，于是条目字号继承 Normal（小四 12pt）。中文公文里目录用
五号是常见口径，因此这里在样式表上显式补 w:sz / w:szCs。

必须在 add_toc_placeholders.py **之后**执行：样式是那个脚本创建的。
用法：python fix_toc_font.py <docx_file>
"""

import re
import shutil
import sys
import zipfile
from pathlib import Path

TARGET_HALF_POINTS = "21"  # 五号 = 10.5pt


def patch_style_block(block: str) -> tuple[str, bool]:
    """给单个 <w:style> 块补上字号；返回 (新块, 是否改动)。"""
    if "<w:rPr>" in block:
        if "<w:szCs" in block:
            block = re.sub(r'<w:szCs\b[^/]*/>', f'<w:szCs w:val="{TARGET_HALF_POINTS}"/>', block, count=1)
        else:
            block = block.replace("</w:rPr>", f'<w:szCs w:val="{TARGET_HALF_POINTS}"/></w:rPr>', 1)
        if "<w:sz " in block:
            block = re.sub(r'<w:sz\b[^/]*/>', f'<w:sz w:val="{TARGET_HALF_POINTS}"/>', block, count=1)
        else:
            block = block.replace("</w:rPr>", f'<w:sz w:val="{TARGET_HALF_POINTS}"/></w:rPr>', 1)
        return block, True
    if "<w:rPr/>" in block:
        rpr = f'<w:rPr><w:sz w:val="{TARGET_HALF_POINTS}"/><w:szCs w:val="{TARGET_HALF_POINTS}"/></w:rPr>'
        return block.replace("<w:rPr/>", rpr, 1), True
    # 完全没 rPr：插在 </w:style> 前
    rpr = f'<w:rPr><w:sz w:val="{TARGET_HALF_POINTS}"/><w:szCs w:val="{TARGET_HALF_POINTS}"/></w:rPr>'
    return block.replace("</w:style>", rpr + "</w:style>", 1), True


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    docx = Path(sys.argv[1])
    if not docx.is_file():
        print(f"找不到文件：{docx}")
        return 1

    tmp = docx.with_suffix(".tmp.docx")
    changed = []

    with zipfile.ZipFile(docx, "r") as zin:
        names = zin.namelist()
        if "word/styles.xml" not in names:
            print("word/styles.xml 不存在，跳过")
            return 0
        styles = zin.read("word/styles.xml").decode("utf-8")

        def repl(m: "re.Match[str]") -> str:
            block = m.group(0)
            name_m = re.search(r'<w:name w:val="([^"]*)"', block)
            style_name = (name_m.group(1) if name_m else "").strip().lower().replace(" ", "")
            if not re.fullmatch(r"toc[123]", style_name):
                return block
            new_block, _ = patch_style_block(block)
            changed.append(name_m.group(1))
            return new_block

        new_styles = re.sub(r"<w:style\b.*?</w:style>", repl, styles, flags=re.DOTALL)

        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/styles.xml":
                    data = new_styles.encode("utf-8")
                zout.writestr(item, data)

    shutil.move(str(tmp), str(docx))
    if changed:
        print(f"目录样式字号已设为五号（{TARGET_HALF_POINTS} 半磅）：{', '.join(changed)}")
    else:
        print("未找到 toc 1/2/3 样式（目录可能为空或样式名不同）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
