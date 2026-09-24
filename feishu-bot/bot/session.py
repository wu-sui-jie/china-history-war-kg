"""会话与历史（开发文档 5.2）。

会话键 `f"{open_id}:{chat_id}"` 天然满足"会话按用户 × 群隔离"（G3）：
同一用户在不同群的上下文互不串，同一群里不同用户也互不串。

历史组装的**硬边界是总字节数**，不是条数：RAG 的 `REQUEST_MAX_BYTES=65536`
在参数校验之前就把请求拒掉（413），而契约里的"单条 4000 字符 × 40 条"在中文
UTF-8 下理论上限约 480 KB——两个上限本身就会打架。所以这里按字节预算反向组装：
从最近的消息往前取，装满即停；条数与单条长度只作次级保护。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

from bot.db import Database, now_ts

log = logging.getLogger(__name__)

# 哪些终态的回答可以进入下一轮历史（开发文档 5.2 末条）：
# interrupted 的回答可能不完整，RAG 契约明确其不得进入下一轮历史；
# failed / cancelled 同理（没有可用内容）。normal / refused / degraded 可以。
HISTORY_ELIGIBLE_REASONS = frozenset({"normal", "refused", "degraded"})

TRUNCATE_SUFFIX = "……（已截断）"


def session_key(open_id: str, chat_id: str) -> str:
    """`{open_id}:{chat_id}`；open_id/chat_id 约 30+ 字符，拼接后约 70 字符，
    低于 RAG 契约的 session_id ≤ 128 上限，无需哈希（开发文档 5.2 已核对）。"""
    return f"{open_id}:{chat_id}"


@dataclass
class AssistantTurn:
    """技能返回的"本轮回答"，由 dispatcher 在发送成功后落库。"""

    content: str                       # answer_md 原文（收敛器处理前，保真优先）
    finish_reason: str = ""
    citations: list[dict] | None = None
    bot_message_id: str | None = None  # 发送后才拿得到，故由 dispatcher 回填

    @property
    def is_history_eligible(self) -> bool:
        """终态是否允许进入下一轮历史。空终态按不可入历史处理（宁缺勿错）。"""
        return self.finish_reason in HISTORY_ELIGIBLE_REASONS


class SessionStore:
    """会话与消息的读写（SQLite）。"""

    def __init__(self, db: Database, *, ttl_hours: float = 24.0,
                 history_max_bytes: int = 40 * 1024,
                 history_max_items: int = 40,
                 history_content_max_chars: int = 4000,
                 history_assistant_max_chars: int = 800,
                 clock=time.time):
        self.db = db
        self.ttl_seconds = max(0.0, float(ttl_hours) * 3600.0)
        self.history_max_bytes = max(1, int(history_max_bytes))
        self.history_max_items = max(1, int(history_max_items))
        self.history_content_max_chars = max(1, int(history_content_max_chars))
        # 回答进入历史时的额外上限（0 = 不额外截断）。比提问严得多，因为：
        # 1) RAG 的指代消解只读历史里的**提问**（server/query/understand.py 的
        #    `_resolve_coref` 重新对 user 轮跑词典匹配），不读回答正文——所以截回答
        #    不影响"他后来怎么样了"这类追问；
        # 2) 长回答会一路滚进后续每一轮请求：实测一个 4 轮会话历史达 11.8KB，
        #    按 400 字截（当时的实测取值）只剩 3.7KB，直接决定生成耗时（真机踩过 30s 超时）；
        # 3) 模型还会模仿历史里的回答长度——留着上千米的长答，它下一轮也写那么长。
        # 注：默认值是 800 字，400 只是上面那次实测的取值（见 .env.example）。
        self.history_assistant_max_chars = max(0, int(history_assistant_max_chars))
        self.clock = clock

    # ---- 会话 ----
    def touch(self, key: str, open_id: str, chat_id: str) -> None:
        """建会话或刷新 updated_at（TTL 软口径按它过滤）。"""
        ts = now_ts()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions (session_key, open_id, chat_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_key) DO UPDATE SET updated_at=excluded.updated_at",
                (key, open_id, chat_id, ts, ts),
            )

    # ---- 消息 ----
    def record_user(self, key: str, message_id: str | None, content: str) -> int:
        cur = self.db.execute(
            "INSERT INTO messages (session_key, message_id, role, content, created_at) "
            "VALUES (?, ?, 'user', ?, ?)",
            (key, message_id, content, now_ts()),
        )
        return int(cur.lastrowid)

    def record_assistant(self, key: str, *, bot_message_id: str | None, content: str,
                         finish_reason: str = "", citations: list[dict] | None = None) -> int:
        """写 assistant 轮。调用方（dispatcher）已按 `is_history_eligible` 过滤终态。"""
        cur = self.db.execute(
            "INSERT INTO messages (session_key, bot_message_id, role, content, finish_reason, "
            "citations, created_at) VALUES (?, ?, 'assistant', ?, ?, ?, ?)",
            (key, bot_message_id, content, finish_reason,
             json.dumps(citations or [], ensure_ascii=False) if citations is not None else None,
             now_ts()),
        )
        return int(cur.lastrowid)

    def get_message(self, msg_id: int) -> dict | None:
        row = self.db.query_one("SELECT * FROM messages WHERE id = ?", (msg_id,))
        return dict(row) if row else None

    def set_bot_message_id(self, msg_id: int, bot_message_id: str) -> None:
        """回填机器人消息 id（发送成功后才拿得到，见开发文档第七节的写入约定）。"""
        self.db.execute("UPDATE messages SET bot_message_id = ? WHERE id = ?",
                        (bot_message_id, msg_id))

    def delete_messages(self, msg_ids: Iterable[int | None]) -> int:
        """物理删除指定消息行，返回删除条数（发送失败时撤回本轮历史用）。

        与 `cleanup` 的 TTL 删除是两件事：这是"刚写就撤"。回答没送达意味着用户
        从未见过它，留在历史里既污染下一轮上下文，也让 RAG 的回答缓存键永远命中不了
        （见 `dispatcher._discard_unsent_turn`）。两行是一起写的，所以一起撤。
        """
        ids = [int(i) for i in msg_ids if i]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        cur = self.db.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
        return int(cur.rowcount or 0)

    def question_before_assistant(self, session_key_value: str, assistant_id: int) -> str:
        """取同一会话中"紧邻在这条回答之前的那次提问"。

        用自增主键（而非时间戳）排序：同一秒内的两条提问也能定序，
        反馈工单里带错的提问比不带更误导。
        """
        row = self.db.query_one(
            "SELECT content FROM messages WHERE session_key = ? AND role = 'user' AND id < ? "
            "ORDER BY id DESC LIMIT 1",
            (session_key_value, assistant_id),
        )
        return str(row["content"]) if row else ""

    # ---- 历史组装 ----
    def history_for_rag(self, key: str) -> list[dict]:
        """组装传给 RAG 的 history（开发文档 5.2）。

        规则（顺序即优先级）：
        1. 只取 TTL 内的记录（软口径）；
        2. 终态不可入历史的 assistant 轮排除（防御性：正常路径压根不落库）；
        3. 条数上限与单条长度上限作次级保护；
        4. **总字节预算 ≤ history_max_bytes**，从最近往前取，装满即停；
        5. 反转为时间正序；若头部是孤立的 assistant 轮则丢弃（没有对应提问的回答不是上下文）。
        """
        cutoff = now_ts() - int(self.ttl_seconds)
        # 终态过滤放在 SQL 里：否则不可入历史的轮次会白占条数名额（LIMIT 先于过滤生效）
        eligible = sorted(HISTORY_ELIGIBLE_REASONS)
        placeholders = ", ".join("?" for _ in eligible)
        rows = self.db.query_all(
            "SELECT id, role, content, finish_reason FROM messages "
            "WHERE session_key = ? AND created_at >= ? "
            f"  AND (role = 'user' OR finish_reason IS NULL OR finish_reason IN ({placeholders})) "
            "ORDER BY id DESC LIMIT ?",
            (key, cutoff, *eligible, self.history_max_items),
        )

        picked: list[dict] = []
        used = 0
        for row in rows:                      # 已按 id 倒序 = 从最近往前
            turn = {"role": str(row["role"]),
                    "content": self._clip(str(row["content"]), role=str(row["role"]))}
            cost = self._turn_bytes(turn)
            if used + cost > self.history_max_bytes:
                # 字节预算优先于条数：装不下就停，不为了凑条数把请求撑到 413
                break
            used += cost
            picked.append(turn)

        picked.reverse()
        picked = self._drop_orphans(picked)
        self._assert_within_budget(picked)
        return picked

    @staticmethod
    def _drop_orphans(turns: list[dict]) -> list[dict]:
        """把历史收敛成"提问 → 回答"的成对结构，去掉三类孤儿行。

        孤儿只可能来自异常路径（处理中途进程被打断）或更早版本"提问先落库"的写法：
        - 提问之前的孤立回答（没有对应提问）；
        - 连续出现的多条提问（只保留最后一条）；
        - 尾部没有回答的提问。

        为什么必须清：对模型是无意义的上下文；**更实际的是历史参与 RAG 回答缓存的键**
        ——真机踩过：库里残留的 `/help` 与"超时轮留下的提问行"把每次提问都变成
        12–16s 的真生成（本该命中缓存），直接把 25s 预算顶穿。
        """
        out: list[dict] = []
        for turn in turns:
            if turn["role"] == "user":
                if out and out[-1]["role"] == "user":
                    out[-1] = turn
                else:
                    out.append(turn)
            elif out:                      # 回答：只有存在对应提问时才保留
                out.append(turn)
        while out and out[-1]["role"] == "user":
            out.pop()
        return out

    def _clip(self, content: str, *, role: str = "") -> str:
        """单条截断：保留开头，末尾加"……（已截断）"（次级保护，开发文档 5.2）。

        回答轮用更严的 `history_assistant_max_chars`（默认 800，0 = 不额外截断），
        理由见构造函数里的注释：指代消解不读回答正文，而长回答会滚进后续每一轮请求。
        """
        limit = self.history_content_max_chars
        if role == "assistant" and self.history_assistant_max_chars:
            limit = min(limit, self.history_assistant_max_chars)
        if len(content) <= limit:
            return content
        keep = max(0, limit - len(TRUNCATE_SUFFIX))
        return content[:keep] + TRUNCATE_SUFFIX

    @staticmethod
    def _turn_bytes(turn: dict) -> int:
        """一条历史在请求体里的字节数（含 JSON 结构开销，估算需偏保守）。"""
        payload = json.dumps(turn, ensure_ascii=False)
        return len(payload.encode("utf-8")) + 8      # 逗号、引号与外层数组的余量

    def _assert_within_budget(self, turns: list[dict]) -> None:
        """最终校验：逐条估算可能偏乐观，这里用真实序列化结果兜底。

        只做"超了就丢最旧的一条"，不做重排——历史的时间顺序是语义的一部分。
        """
        while turns and len(self._serialized_bytes(turns)) > self.history_max_bytes:
            turns.pop(0)

    @staticmethod
    def _serialized_bytes(turns: list[dict]) -> bytes:
        return json.dumps(turns, ensure_ascii=False).encode("utf-8")

    # ---- 反馈（P2）----
    def record_feedback(self, *, open_id: str, key: str, message_id: str | None,
                        question: str, answer_md: str,
                        citations: list[dict] | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO feedback (open_id, session_key, message_id, question, answer_md, "
            "citations, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
            (open_id, key, message_id, question, answer_md,
             json.dumps(citations or [], ensure_ascii=False), now_ts()),
        )
        return int(cur.lastrowid)

    def get_feedback(self, feedback_id: int) -> dict | None:
        row = self.db.query_one("SELECT * FROM feedback WHERE id = ?", (feedback_id,))
        return dict(row) if row else None

    def find_feedback(self, *, open_id: str, bot_message_id: str | None) -> dict | None:
        """同一用户对同一条机器人消息是否已提过反馈（重复点击的去重口径）。

        按 `(open_id, message_id)` 去重而不是按时间窗：按钮点第二次时用户想表达的
        还是同一条意见，重复投递只会打扰运营群。
        """
        if not bot_message_id:
            return None
        row = self.db.query_one(
            "SELECT * FROM feedback WHERE open_id = ? AND message_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (open_id, bot_message_id),
        )
        return dict(row) if row else None

    # ---- 清理 ----
    def reset(self, key: str) -> dict[str, int]:
        """清空一个会话的上下文（`/new` 命令，批次③-2）。返回各表删除行数。

        删 `messages` 与 `sessions` 两张表里该 session_key 的行；**`feedback` 表不动**
        ——纠错记录是治理队列，不该因为用户重置会话而消失（开发文档 5.7）。

        删掉的 `messages.id` 是已发出卡片上反馈按钮的 `msg_key`，用户在新会话里再点旧卡片时
        由 `report_error` 走"消息不存在"的静默分支（开发文档 5.5.3）。
        """
        with self.db.transaction() as conn:
            messages = conn.execute(
                "DELETE FROM messages WHERE session_key = ?", (key,)).rowcount
            sessions = conn.execute(
                "DELETE FROM sessions WHERE session_key = ?", (key,)).rowcount
        return {"messages": int(messages or 0), "sessions": int(sessions or 0)}

    def cleanup(self, *, events_ttl_hours: float = 24.0, dry_run: bool = False) -> dict[str, int]:
        """物理删除过期数据（硬口径，开发文档 5.2 TTL 尾段）。

        **`feedback` 表不在清理范围内**：它是治理队列（`status=open` → 运营处理后 done），
        没有 TTL，也不该被 TTL 删掉；需要腾空间时由人工导出归档（开发文档 5.7）。
        """
        cutoff = now_ts() - int(self.ttl_seconds)
        events_cutoff = now_ts() - int(max(0.0, float(events_ttl_hours)) * 3600.0)

        counts: dict[str, int] = {}
        targets = (
            ("messages", "SELECT COUNT(*) FROM messages WHERE created_at < ?", (cutoff,),
             "DELETE FROM messages WHERE created_at < ?", (cutoff,)),
            ("sessions", "SELECT COUNT(*) FROM sessions WHERE updated_at < ?", (cutoff,),
             "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)),
            ("processed_events",
             "SELECT COUNT(*) FROM processed_events WHERE received_at < ?", (events_cutoff,),
             "DELETE FROM processed_events WHERE received_at < ?", (events_cutoff,)),
        )
        for name, count_sql, count_params, del_sql, del_params in targets:
            row = self.db.query_one(count_sql, count_params)
            counts[name] = int(row[0]) if row else 0
            if not dry_run and counts[name]:
                self.db.execute(del_sql, del_params)
        return counts


def as_rag_history(rows: list[Any]) -> list[dict[str, str]]:
    """把 history_for_rag 的结果转成请求体里的 [{role, content}]（RAG 契约形状）。

    单独留一个转换点：以后要给 RAG 送额外字段（如 corrected_entities 的种子）时，
    只改这里，不必翻遍组装逻辑。
    """
    return [{"role": str(t["role"]), "content": str(t["content"])} for t in rows]
