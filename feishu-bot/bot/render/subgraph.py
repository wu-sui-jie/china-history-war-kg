"""panel.subgraph → PNG（开发文档 5.6，P2-1）。

选型：Node + echarts SSR（`renderer: 'svg', ssr: true`）+ resvg-js 转 PNG，
不需要浏览器内核。布局约束见 render/render_subgraph.js 顶部注释：
SSR 不支持力导向布局，必须在 Node 侧用 d3-force 预计算坐标。

约定：
- 空图（nodes 为空）直接走文字降级，**不起 Node 进程**；
- 任何失败（Node 缺失/超时/脚本报错/非 PNG）都返回 None，
  由 builder 自动降级为分组文字列表——**不允许空白**（开发文档十二-2：
  降级路径是必经路径，与成功路径同等测试）。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

# render/ 目录（Node 脚本与 node_modules 所在处）
RENDER_DIR = Path(__file__).resolve().parent.parent.parent / "render"
RENDER_SCRIPT = RENDER_DIR / "render_subgraph.js"

# 出图尺寸：够看清节点名与关系标签，又不至于让卡片过大
DEFAULT_WIDTH = 1000
DEFAULT_HEIGHT = 700


class SubgraphRenderer:
    """子图渲染 + 上传（成功返回 image_key，失败返回 None）。"""

    def __init__(self, feishu, *, enabled: bool = True, timeout: float = 10.0,
                 node_bin: str = "node", work_dir: Path | None = None):
        self.feishu = feishu
        self.enabled = enabled
        self.timeout = timeout
        self.node_bin = node_bin
        self.work_dir = work_dir or Path(tempfile.gettempdir()) / "feishu-bot-render"

    # ---- 可用性 ----
    def unavailable_reason(self) -> str | None:
        """预检失败原因（None = 可用）。启动时探测一次，日志里给出可执行的修复提示。"""
        if not self.enabled:
            return "已通过 SUBGRAPH_RENDER_ENABLED=false 关闭"
        if shutil.which(self.node_bin) is None:
            return f"未找到 Node 可执行文件（{self.node_bin}）：请安装 Node ≥ 18，或关闭子图出图"
        if not RENDER_SCRIPT.exists():
            return f"渲染脚本不存在：{RENDER_SCRIPT}"
        if not (RENDER_DIR / "node_modules").exists():
            return (f"缺少 Node 依赖：请在 {RENDER_DIR} 执行 "
                    f"`npm install echarts @resvg/resvg-js d3-force`")
        return None

    def is_available(self) -> bool:
        return self.unavailable_reason() is None

    # ---- 渲染 ----
    def render_png(self, subgraph: dict) -> Path | None:
        """渲染为 PNG 文件；失败返回 None（调用方走文字降级）。"""
        nodes = [n for n in (subgraph.get("nodes") or []) if isinstance(n, dict)]
        if not nodes:
            log.info("子图节点为空，直接走文字降级（不起 Node 进程）")
            return None
        reason = self.unavailable_reason()
        if reason:
            log.warning("子图出图不可用：%s", reason)
            return None

        self.work_dir.mkdir(parents=True, exist_ok=True)
        stem = f"subgraph-{int(time.time() * 1000)}"
        input_path = self.work_dir / f"{stem}.json"
        out_path = self.work_dir / f"{stem}.png"
        payload = {
            "nodes": nodes,
            "edges": [e for e in (subgraph.get("edges") or []) if isinstance(e, dict)],
            "out": str(out_path),
            "width": DEFAULT_WIDTH,
            "height": DEFAULT_HEIGHT,
        }
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        started = time.monotonic()
        try:
            proc = subprocess.run(
                [self.node_bin, str(RENDER_SCRIPT), str(input_path)],
                cwd=str(RENDER_DIR), capture_output=True, text=True,
                # 必须显式声明编码：Node 的输出是 UTF-8，而 Windows 上 Python 默认按
                # 本地编码（中文机器是 cp936）解读子进程 stdout——按错编码解出来的
                # JSON 解析失败，会被误判成"渲染失败"（实测踩过，且错误信息为空）。
                # errors="replace" 是为了让异常路径也能拿到可读文本而不是解码异常。
                encoding="utf-8", errors="replace",
                timeout=self.timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("子图渲染超时（%.0fs），已降级为文字列表", self.timeout)
            return None
        except OSError as e:
            log.warning("子图渲染无法启动（%s），已降级为文字列表", e)
            return None
        finally:
            input_path.unlink(missing_ok=True)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        result = self._parse_result(proc.stdout)
        if proc.returncode != 0 or not (result or {}).get("ok"):
            # 失败必须留下可诊断的信息：没有 error 字段（或没有结果 JSON）时，
            # 把 stdout/stderr 的片段一并打出来——否则只能看到一个空冒号
            error = (result or {}).get("error") or ""
            if not error:
                error = (f"无结果输出；stdout={self._excerpt(proc.stdout)} "
                         f"stderr={self._excerpt(proc.stderr)}")
            log.warning("子图渲染失败（exit=%s，%dms）：%s", proc.returncode, elapsed_ms, error)
            out_path.unlink(missing_ok=True)     # 半成品不留在临时目录里
            return None
        if not out_path.exists() or out_path.stat().st_size == 0:
            log.warning("子图渲染声称成功但产物缺失：%s", out_path)
            return None
        log.info("子图渲染完成：%d 节点 / %d 边 / %d 字节，%dms",
                 len(nodes), len(payload["edges"]), out_path.stat().st_size, elapsed_ms)
        return out_path

    @staticmethod
    def _excerpt(text: str, limit: int = 200) -> str:
        """截断一段子进程输出用于日志（避免把整段 SVG 或堆栈刷进日志）。"""
        cleaned = (text or "").strip().replace("\n", " ⏎ ")
        return repr(cleaned[:limit]) if cleaned else "''"

    @staticmethod
    def _parse_result(stdout: str) -> dict | None:
        """脚本 stdout 的最后一行为结果 JSON（前面可能有 Node 的告警输出）。"""
        for line in reversed((stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(parsed, dict):
                    return parsed
        return None

    # ---- 渲染 + 上传 ----
    def render_and_upload(self, subgraph: dict) -> str | None:
        """渲染并上传，返回 image_key；任一步失败返回 None。"""
        png_path = self.render_png(subgraph)
        if png_path is None:
            return None
        try:
            image_key = self.feishu.upload_image(png_path)
        finally:
            try:
                png_path.unlink(missing_ok=True)     # 临时文件不累积
            except OSError:
                pass
        if not image_key:
            log.warning("子图上传失败，已降级为文字列表")
            return None
        return image_key
