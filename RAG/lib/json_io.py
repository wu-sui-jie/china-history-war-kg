"""JSON 读写工具：utf-8、ensure_ascii=False、自动建父目录。

行尾固定为 `\\n`（第五轮整改复核 B1）：Windows 文本模式会把 `\\n` 翻译成 `\\r\\n`，
同一份清单在不同平台就会产生不同字节与不同哈希——`sha256sum -c` 这类标准工具在
Linux 上会逐行失败，而"本机验证通过"用的是能容忍 `\\r` 的自建解析器。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_text_lf(path: Path, text: str) -> None:
    """以 UTF-8 + LF 写文本（跨平台字节一致；校验和与发布证据必须用它）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_json(path: Path, data: Any, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def read_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
