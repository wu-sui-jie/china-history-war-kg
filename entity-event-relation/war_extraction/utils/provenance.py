"""
产物溯源：把"这份数据是哪版代码、哪个模型、哪个提示词跑出来的"记进产物自身。

**为什么要有这个模块。** 现有产物（`output/中国历代战争简史/9_final_all.json`）的
`metadata` 只有时间戳、两个版本串与输入文件，缺 `model` / `api_base` / 温度 /
`git_commit` / 产物哈希。后果是"指标变了"时无法区分是模型变了、提示词变了还是评估器变了——
只能靠人回忆。这里把这几项机械派生出来，在写产物时一次性落进 `metadata`。

`artifact_digest` 的口径：对"除 `metadata.artifact_sha256` 之外的全部内容"做规范化
（`sort_keys=True`、紧凑分隔符）JSON 序列化后取 sha256。这样哈希可以自校验——
重新读产物、把该字段抠掉再算一遍即可比对，不必依赖任何外部记录。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = [
    "STAGE_TEMPERATURES",
    "file_sha256",
    "git_commit",
    "artifact_digest",
    "content_digest",
    "generation_metadata",
]

#: 三个阶段实际使用的采样温度（与各 extractor 的调用参数一致）。
#: 记进产物 metadata 的理由：温度是"同一份提示词为什么两次结果不同"的第一嫌疑，
#: 现在它只写在代码里、不落在产物上，事后无法核对。
STAGE_TEMPERATURES = {
    "entity_extraction": 0.1,
    "event_identification": 0.2,
    "full_event": 0.1,
    "relation_extraction": 0.1,
}

#: 仓库根（本文件在 war_extraction/utils/ 下）
_REPO_ROOT = Path(__file__).resolve().parents[3]

_HASH_CHUNK = 1 << 20


def file_sha256(path: Path) -> Optional[str]:
    """文件的 sha256；文件不存在时返回 None（不抛异常，因为它只是记录项）。"""
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(short: bool = False) -> Optional[str]:
    """
    当前代码的 git 提交号；不在仓库里、或没装 git 时返回 None。

    只读不写：`git rev-parse` 不改工作区，所以可以在任何一次抽取里安全调用。
    """
    args = ["git", "rev-parse"] + (["--short"] if short else []) + ["HEAD"]
    try:
        result = subprocess.run(
            args, cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def artifact_digest(payload: Dict[str, Any]) -> str:
    """
    产物内容哈希：忽略 `metadata.artifact_sha256` 自身，其余内容规范化后取 sha256。

    忽略它是必须的——否则哈希值依赖于它自己，写进去就再也对不上。
    规范化用 `sort_keys=True` + 紧凑分隔符，所以与写文件的 `indent=2` 排版无关，
    换一次序列化排版不会让哈希漂移。
    """
    if not isinstance(payload, dict):
        raise TypeError("artifact_digest 只接受顶层为 dict 的产物")
    stripped = dict(payload)
    metadata = dict(stripped.get("metadata") or {})
    metadata.pop("artifact_sha256", None)
    stripped["metadata"] = metadata
    canonical = json.dumps(stripped, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: `content_sha256` 覆盖的**内容段**：只数"抽出来的记录"。
#: 不含 `metadata`（含时间戳、git 提交、模型与提示词版本——放进来就每次跑都不同，
#: 而"换了提示词版本"本来就该由 `prompt_version` 说明，不该混进内容哈希）
#: 也不含 `quality_report`（含 `generated_at`）。
_CONTENT_SECTIONS = ("entities", "events", "relations")


def content_digest(payload: Dict[str, Any]) -> str:
    """
    **只覆盖内容**的哈希：`entities` / `events` / `relations` 三段规范化后取 sha256。

    它回答的问题是"**两次跑抽出来的记录是否一致**"，与 `artifact_digest` 分工不同：
    后者是"这一份产物的整体自证"（口径是"除它自己之外的全部内容"，**含 `extracted_at`**，
    所以两次跑必然不同），前者只认记录本身。

    **为什么必须有它。** 没有它的时候，"内容有没有变"这个问题在产物指纹上**永远得到"变了"**——
    2026-09-27 我就据此误判过一次"流水线有非确定性"（把两次产物逐条比过：实体/事件/四类关系的
    列表**内容与顺序全部相同**，只有时间戳与 `artifact_sha256` 不同）。要区分"产物换代"与
    "跑批抖动"，用的必须是这个哈希。
    """
    if not isinstance(payload, dict):
        raise TypeError("content_digest 只接受顶层为 dict 的产物")
    content = {section: payload.get(section) for section in _CONTENT_SECTIONS}
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generation_metadata(
    *,
    model: Optional[str] = None,
    api_base: Optional[str] = None,
    model_served: Optional[str] = None,
    thinking_mode: Optional[str] = None,
    seed: Optional[int] = None,
    llm_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    组装"产物由什么生成"的那一组字段，供 `save_results` 写进 `metadata`。

    `llm_meta` 里的键优先（调用方显式传的 model/api_base 就是权威值），
    这里只补上其余机械可派生的项。`seed` 显式记 None：DeepSeek API 目前不接受该参数，
    写 None 表示"未设置"，而不是漏记。

    **`model` 与 `model_served` 是两个不同的字段，不要合并**：
      - `model` = **请求名**（env 里的 `DEEPSEEK_MODEL`）；
      - `model_served` = 服务端**实际服务**的模型名（取自响应的 `model` 字段）。
    实测服务端把 `deepseek-chat` 这个别名路由到了 `deepseek-flash`，所以只说请求名
    等于自证了错信息（写 `deepseek-chat`、实际跑的是 `flash`）。全部命中缓存没发生调用时
    `model_served` 为 None——那是"未知"，不是拿请求名冒充。
    """
    meta = dict(llm_meta or {})
    if model is not None:
        meta["model"] = model
    if api_base is not None:
        meta["api_base"] = api_base
    if model_served is not None:
        meta["model_served"] = model_served
    if thinking_mode is not None:
        meta["thinking_mode"] = thinking_mode
    meta.setdefault("model", None)
    meta.setdefault("api_base", None)
    meta.setdefault("model_served", None)
    meta["temperature"] = dict(STAGE_TEMPERATURES)
    meta["seed"] = seed
    meta["git_commit"] = git_commit(short=True)
    return meta
