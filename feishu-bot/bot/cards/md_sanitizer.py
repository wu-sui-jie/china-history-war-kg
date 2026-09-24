r"""Markdown 白名单收敛器（开发文档 5.5.1）。

飞书卡片 markdown 组件只支持 Markdown 子集，而 RAG（F06）输出的是通用 Markdown：
不收敛就会渲染错乱（需求文档风险 1）。收敛器按**保守白名单**处理——
拿不准的语法一律降级为纯文本，宁可少样式，不可渲染错乱。

实现是**逐行状态机**（表格与代码块都是多行结构，正则一把梭处理不了），
并且保证**幂等**：对已收敛文本再收敛结果不变（防御重复处理）。

白名单（原样保留）：加粗 `**x**`、斜体 `*x*`、删除线 `~~x~~`、行内代码 `` `x` ``、
链接 `[text](url)`（仅 http/https）、无序列表 `- `、有序列表 `1. `、引用 `> `、
分割线 `---`、换行。

转换与剥离规则：

| 输入 | 处理 |
| --- | --- |
| `# 标题`（1–6 级） | 转为 `**标题**` 独立行 |
| 表格（`\|` 分隔行） | 每行转 `**单元格** · 单元格 · …`；表头行加粗，分隔行（`---`）删除 |
| 代码块围栏 | 内容逐行加 `> ` 前缀（引用块承载），围栏行删除 |
| 图片 `![alt](url)` | 整行删除（卡片 md 不渲染外链图片） |
| HTML 标签 | 剥离标签保留内文 |
| 裸 URL | 不自动加链接语法，保持原样 |
| 超长（> 4000 字符） | 截断并追加提示（正文末行提示用户去网页版看完整内容） |
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 截断上限：与开发文档 5.5.1 一致。留出余量给卡片其它区块，
# 避免整张卡片 JSON 过大被飞书拒绝。
MAX_CHARS = 4000
TRUNCATE_SUFFIX = "……（内容过长已截断，完整内容请在网页版查看）"

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})(?:\s+(.*?))?\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```+|~~~+)\s*(.*)$")
_HR_RE = re.compile(r"^\s{0,3}([-*_])\s*(\1\s*){2,}$")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# 只匹配"标签名 + 可选属性"的形态；不会误伤 <https://example.com> 这类尖括号 URL
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")
# 链接：先取 target，再判断协议。target 允许一层嵌套括号
# （`wiki/A_(b)` 这类地址在维基链接里很常见，不处理会把收尾括号留成残渣）
_LINK_RE = re.compile(
    r"\[([^\]]*)\]\(([^()\s]*(?:\([^()]*\)[^()\s]*)*)(?:\s+\"[^\"]*\")?\)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_SAFE_SCHEMES = ("http://", "https://")


@dataclass
class _Counters:
    """收敛过程中的改动计数（观测用：能一眼看出回答里有什么飞书不吃的语法）。"""

    headings: int = 0
    table_rows: int = 0
    code_lines: int = 0
    images: int = 0
    html_tags: int = 0
    unsafe_links: int = 0
    truncated: int = 0

    def as_dict(self) -> dict[str, int]:
        return {k: v for k, v in self.__dict__.items() if v}


def _is_table_separator(line: str) -> bool:
    """表格分隔行：`| --- | :--: |` 这类只由 | - : 空白组成且含 - 的行。"""
    stripped = line.strip()
    if not stripped or "-" not in stripped:
        return False
    return set(stripped) <= set("|-: ")


def _is_table_row(line: str) -> bool:
    """表格行：以 | 开头且至少两个竖线（F06 输出走标准管道表格形态）。"""
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _render_table_row(line: str, *, header: bool) -> str:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    rendered = []
    for cell in cells:
        if not cell:
            continue
        rendered.append(f"**{cell}**" if header else cell)
    return " · ".join(rendered)


def _protect_inline_code(line: str, transform) -> str:
    """只对行内代码**之外**的片段做变换：`` `<div>` `` 是代码内容，不是 HTML 标签。"""
    out: list[str] = []
    last = 0
    for match in _INLINE_CODE_RE.finditer(line):
        out.append(transform(line[last:match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(transform(line[last:]))
    return "".join(out)


def _strip_html(line: str, counters: _Counters) -> str:
    def repl(match: re.Match) -> str:
        counters.html_tags += 1
        return ""

    return _HTML_TAG_RE.sub(repl, line)


def _normalize_links(line: str, counters: _Counters) -> str:
    """仅保留 http/https 链接；其它协议降级为纯文本（丢弃可能危险的 target）。"""

    def repl(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(_SAFE_SCHEMES):
            return match.group(0)
        counters.unsafe_links += 1
        return label or target

    return _LINK_RE.sub(repl, line)


def _strip_images(line: str, counters: _Counters) -> tuple[str, bool]:
    """去掉外链图片；返回 (处理后文本, 是否整行只由图片构成)。"""
    if "![" not in line:
        return line, False
    had_image = bool(_IMAGE_RE.search(line))
    if had_image:
        counters.images += len(_IMAGE_RE.findall(line))
    stripped = _IMAGE_RE.sub("", line)
    return stripped, had_image and not stripped.strip()


def _transform_inline(line: str, counters: _Counters) -> str:
    """行内规则：剥 HTML 标签、收敛链接协议（跳过行内代码内容）。"""
    line = _protect_inline_code(line, lambda seg: _strip_html(seg, counters))
    return _protect_inline_code(line, lambda seg: _normalize_links(seg, counters))


def sanitize(text: str, *, max_chars: int = MAX_CHARS,
             counters: _Counters | None = None) -> str:
    """把通用 Markdown 收敛为飞书卡片 md 子集（幂等）。

    `max_chars` 为截断上限；返回文本长度不会超过它（截断提示计入上限内）。
    """
    if not text:
        return ""
    counters = counters if counters is not None else _Counters()

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    in_fence = False
    prev_is_table_row = False

    for idx, raw_line in enumerate(lines):
        fence = _FENCE_RE.match(raw_line)
        if fence:
            # 围栏行本身删除；块内内容在后续行加引用前缀
            in_fence = not in_fence
            counters.code_lines += 1
            prev_is_table_row = False
            continue

        if in_fence:
            counters.code_lines += 1
            # 代码行用引用块承载：空行也要保留 `>`，否则引用块会被空行截断。
            # 行内规则照样应用（HTML 标签/链接协议）——否则第二遍收敛会把它们改掉，
            # 幂等性就没了（代码块内容在飞书侧终究是普通文本，不存在"代码语义"可保）。
            content = _transform_inline(raw_line, counters)
            out.append(f"> {content}" if content.strip() else ">")
            prev_is_table_row = False
            continue

        if _is_table_separator(raw_line) and prev_is_table_row:
            # 只有紧跟在表格行之后的分隔行才是表格分隔行；
            # 孤立出现的 `---` 是分割线，属于白名单，必须留着（否则整段结构被吃掉）
            counters.table_rows += 1
            prev_is_table_row = False
            continue

        if _is_table_row(raw_line):
            counters.table_rows += 1
            # 表头 = 紧跟分隔行的那一行（前瞻一行即可判断）
            next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
            out.append(_render_table_row(raw_line, header=_is_table_separator(next_line)))
            prev_is_table_row = True
            continue

        prev_is_table_row = False
        line = raw_line
        # 图片先处理：整行只有图片时直接丢弃该行
        line, drop_line = _strip_images(line, counters)
        if drop_line:
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            counters.headings += 1
            title = (heading.group(2) or "").strip()
            out.append(f"**{title}**" if title else "")
            continue

        out.append(_transform_inline(line, counters))

    result = "\n".join(out)

    # 超长截断放在最后：截断提示本身计入上限，保证总长不超标（也因此幂等）
    if len(result) > max_chars:
        counters.truncated += 1
        if max_chars <= len(TRUNCATE_SUFFIX):
            # 上限被调到比提示文案还短（只会在测试里出现）：连提示一起截断，
            # 保住"返回长度 ≤ max_chars"这个硬约定，调用方不必再判一次
            result = TRUNCATE_SUFFIX[:max_chars]
        else:
            result = result[:max_chars - len(TRUNCATE_SUFFIX)] + TRUNCATE_SUFFIX
    return result


def sanitize_with_counters(text: str, *, max_chars: int = MAX_CHARS) -> tuple[str, dict]:
    """收敛并返回改动计数（日志里记录，便于发现 F06 输出结构变化）。"""
    counters = _Counters()
    return sanitize(text, max_chars=max_chars, counters=counters), counters.as_dict()


def looks_unsupported(text: str) -> bool:
    """粗判文本里是否仍含飞书不支持的语法（单测断言用，不参与运行时逻辑）。

    行内代码内容不算（`` `# 不是标题` `` 里的 `#` 是代码文本）。
    """
    if not text:
        return False
    for line in text.split("\n"):
        if _FENCE_RE.match(line) or _HEADING_RE.match(line) or _is_table_row(line):
            return True
        if _IMAGE_RE.search(line):
            return True
        probe = _INLINE_CODE_RE.sub("", line)
        if _HTML_TAG_RE.search(probe):
            return True
        for match in _LINK_RE.finditer(probe):
            if not match.group(2).startswith(_SAFE_SCHEMES):
                return True
    return False
