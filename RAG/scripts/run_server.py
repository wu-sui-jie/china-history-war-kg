"""RAGv2 在线服务启动脚本（F02–F06 SSE 问答接口）。

用法：
    python scripts/run_server.py [--host 127.0.0.1] [--port 8000] [--version 20260904_v2]

说明：脚本保持薄，只做 uvicorn 启动；业务在 server/。
需要 fastapi/uvicorn（见 RAG requirements.txt）；无 LLM 密钥也可启动，
F06 自动使用离线摘要回答器；配好 LLM_BASE_URL/LLM_API_KEY 后接真实模型。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保 RAG/ 根目录在 sys.path（与离线脚本约定一致：从 RAG/ 根执行）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from config.settings import get_settings


def main() -> None:
    p = argparse.ArgumentParser(description="启动 RAGv2 SSE 问答服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--version", default=None, help="快照/索引版本（默认最新一致版本）")
    p.add_argument("--reload", action="store_true", help="开发热重载")
    args = p.parse_args()

    s = get_settings()
    print(f"[run_server] RAGv2 服务启动 host={args.host} port={args.port} version={args.version or 'latest'}")
    if not (s.llm_base_url and s.llm_api_key):
        print("[run_server] 未配置 LLM_BASE_URL/LLM_API_KEY → F06 使用离线摘要回答器（可正常演示检索链）")
    else:
        print(f"[run_server] LLM: {s.llm_model} @ {s.llm_base_url}"
              f"（备用 {s.fallback_llm_model or '未配'}，max_tokens={s.llm_max_tokens}）")
    print(f"[run_server] 文本模式: {getattr(s, 'text_mode', 'keyword')}"
          f" | 同源托管目录: {s.frontend_dist if s.frontend_dist.is_dir() else '未发现（先 npm run build）'}")

    # 启动自检：版本 / 向量产物 / 前端产物一次看清（失败不阻塞启动，由 /api/health 暴露）
    try:
        from server.runtime import resolve_version

        version, _snap_dir, index_dir = resolve_version(s, args.version)
        print(f"[run_server] 自检: 快照 {version} | 索引 {index_dir.name} | 前端产物 "
              f"{'有' if (s.frontend_dist / 'index.html').exists() else '无'}")
    except Exception as e:  # noqa: BLE001
        print(f"[run_server] 自检失败（服务仍会启动，可在 /api/health 查看 load_error）: {e}")

    uvicorn.run(
        "server.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1,
    )


if __name__ == "__main__":
    main()
