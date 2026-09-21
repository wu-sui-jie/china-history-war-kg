"""RAGv4（F10 问答效果评测）命令行入口（薄封装）。

用法（从 RAG/ 根执行）：
    python scripts/run_evaluation.py check-bank
    python scripts/run_evaluation.py run [--suites main] [--configs ...]
    python scripts/run_evaluation.py report [--run <dir>] [--scores <scores.jsonl>]

业务逻辑在 evaluation/ 包；本脚本只做 sys.path 对齐并透传子命令参数。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
