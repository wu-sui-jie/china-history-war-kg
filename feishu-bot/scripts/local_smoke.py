"""本地冒烟：不接飞书也能看清「提问 → 卡片」到底长什么样（人工验收前的自检）。

它把机器人的**真实链路**跑一遍（真 config、真会话库、真调 RAG、真组卡片、真出图），
只把最后一步"发给飞书"换成一个**记录式替身**：卡片 JSON 落到 `--out-dir`，
你可以直接贴进飞书开放平台的**卡片搭建工具**预览渲染效果。

能验什么：
- RAG 通不通、非流式接口返回什么、回答质量如何；
- 卡片结构对不对（正文/引用折叠/实体卡/时间线/地点/子图/按钮）；
- 多轮追问有没有把历史带上（对话里能看出指代是否被理解）；
- 纠错反馈有没有落库、工单卡片长什么样；
- RAG 挂掉时的降级卡片（用 `--dead-rag` 把 RAG 地址指到一个空端口）。

不能验什么（仍需要真实飞书租户）：长连接能否收到事件、卡片回调能否触发、
飞书客户端上的实际渲染效果——那部分是人工验收，见 README「验收清单」。

用法：
    python scripts/local_smoke.py                        # 跑一遍默认流程
    python scripts/local_smoke.py --question "介绍一下涿鹿之战。"
    python scripts/local_smoke.py --dead-rag             # 看降级卡片
    python scripts/local_smoke.py --no-render            # 不出图（不装 Node 也能跑）
    python scripts/local_smoke.py --json                 # 顺带把完整卡片 JSON 打到屏幕
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

BOT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_ROOT))
# 卡片取值复用测试侧的唯一实现（tests/card_helpers.py，按卡片 2.0 结构取值）：
# 本脚本曾按 1.0 形态找 `tag == "action"` 容器与顶层 `value`，2.0 迁移后没同步，
# 结果是"按钮摘要永远为空、纠错反馈环节静默跳过"——两处解析必须共用一份实现。
sys.path.insert(0, str(BOT_ROOT / "tests"))

from bot.db import Database                                       # noqa: E402
from bot.dispatcher import Dispatcher, parse_card_action, parse_message_event  # noqa: E402
from bot.rag_client import DemoExamplesCache, RagClient           # noqa: E402
from bot.render.subgraph import SubgraphRenderer                  # noqa: E402
from bot.session import SessionStore                              # noqa: E402
from bot.skills.help import HelpSkill                             # noqa: E402
from bot.skills.knowledge_qa import KnowledgeQaSkill              # noqa: E402
from bot.skills.report_error import ReportErrorSkill              # noqa: E402
from card_helpers import buttons, elements, feedback_msg_key      # noqa: E402
from config import Config, load_config                            # noqa: E402

DEFAULT_QUESTION = "介绍一下涿鹿之战。"
DEFAULT_FOLLOWUP = "他后来怎么样了？"
OPERATORS_CHAT = "oc_smoke_operators"


class RecordingFeishu:
    """记录式飞书替身：不联网，只把"发出去的东西"留下来供检查。"""

    def __init__(self):
        self.replies: list[dict] = []
        self.sent: list[tuple[str, dict]] = []
        self.images: list[str] = []

    def reply_card(self, message_id, card):
        self.replies.append(card)
        return f"bot-msg-{len(self.replies)}"

    def reply_text(self, message_id, text):
        self.replies.append({"text": text})
        return f"bot-msg-{len(self.replies)}"

    def send_card(self, chat_id, card):
        self.sent.append((chat_id, card))
        return f"sent-{len(self.sent)}"

    def send_text(self, chat_id, text):
        self.sent.append((chat_id, {"text": text}))
        return f"sent-{len(self.sent)}"

    def patch_card(self, message_id, card):
        return True

    def upload_image(self, path):
        from pathlib import Path as _P

        data = _P(path).read_bytes()
        self.images.append(f"{_P(path).name} ({len(data)} 字节, {data[1:4].decode()})")
        return f"img_key_{len(self.images)}"


def _summarize(card: dict) -> str:
    """一行摘要：元素构成 / 折叠区 / 按钮——肉眼核对卡片结构用。"""
    items = elements(card)
    tags = [e.get("tag") for e in items]
    folds = [e["header"]["title"]["content"] for e in items
             if e.get("tag") == "collapsible_panel"]
    labels = [b["text"]["content"] for b in buttons(card)]
    size = len(json.dumps(card, ensure_ascii=False))
    parts = [f"{size} 字符", f"元素={tags}"]
    if folds:
        parts.append(f"折叠区={folds}")
    if labels:
        parts.append(f"按钮={labels}")
    return " | ".join(parts)


def _body_head(card: dict, chars: int = 90) -> str:
    for element in card.get("body", {}).get("elements", []):
        if element.get("tag") == "markdown":
            text = element.get("content", "").replace("\n", " ")
            return text[:chars] + ("…" if len(text) > chars else "")
    return "（无 markdown 元素）"


def _save(out_dir: Path, name: str, card: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _raw_message_event(text: str, event_id: str, chat_id: str = "oc_smoke",
                       open_id: str = "ou_smoke", chat_type: str = "p2p"):
    """构造 SDK 消息事件的等价物（原始形态，喂给 dispatcher.on_message）。"""
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id, event_type="im.message.receive_v1"),
        event=SimpleNamespace(
            message=SimpleNamespace(message_id=f"om_{event_id}", chat_id=chat_id,
                                    chat_type=chat_type, message_type="text",
                                    content=json.dumps({"text": text}), mentions=[]),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id=open_id))))


def _message_event(text: str, event_id: str, **kwargs):
    """解析后的消息事件（喂给 dispatcher.handle_message，跳过入队环节）。"""
    return parse_message_event(_raw_message_event(text, event_id, **kwargs))


def _card_action(value: dict, event_id: str):
    return parse_card_action(SimpleNamespace(
        header=SimpleNamespace(event_id=event_id, event_type="card.action.trigger"),
        event=SimpleNamespace(
            action=SimpleNamespace(tag="button", value=value),
            operator=SimpleNamespace(open_id="ou_smoke"),
            context=SimpleNamespace(open_message_id="om_feedback",
                                    open_chat_id="oc_smoke"))))


def build_config(args) -> Config:
    """构造配置：优先读 .env/环境变量，缺飞书凭证时用占位值（本工具不连飞书）。

    唯一真正需要的是 RAG 地址；`--dead-rag` 把它指到一个空端口，用来看降级卡片。
    """
    try:
        base = load_config()
    except Exception:  # noqa: BLE001 - 缺凭证属正常，本工具不需要它
        base = Config(feishu_app_id="placeholder", feishu_app_secret="placeholder")
    base.feishu_app_id = base.feishu_app_id or "placeholder"
    base.feishu_app_secret = base.feishu_app_secret or "placeholder"
    if args.base_url:
        base.rag_base_url = args.base_url
    if args.dead_rag:
        base.rag_base_url = "http://127.0.0.1:1"
        base.rag_connect_timeout = 0.5
        base.rag_query_timeout = 1.0
    base.subgraph_render_enabled = not args.no_render
    base.subgraph_render_timeout = args.render_timeout
    base.feishu_operators_chat_id = OPERATORS_CHAT
    base.demo_examples_enabled = not args.no_examples
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="飞书机器人本地冒烟（不接飞书）")
    parser.add_argument("--base-url", default="", help="RAG 地址；默认取配置的 RAG_BASE_URL")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="主问题")
    parser.add_argument("--followup", default=DEFAULT_FOLLOWUP, help="追问（验历史透传）")
    parser.add_argument("--db", default="", help="会话库路径；默认用临时库（跑完即弃）")
    parser.add_argument("--out-dir", default=str(BOT_ROOT / "data" / "smoke"),
                        help="卡片 JSON 的落盘目录")
    parser.add_argument("--dead-rag", action="store_true", help="把 RAG 指向空端口，看降级卡片")
    parser.add_argument("--no-render", action="store_true", help="不做出图（不装 Node 也能跑）")
    parser.add_argument("--no-examples", action="store_true", help="不取示例题（看无按钮的卡片）")
    parser.add_argument("--render-timeout", type=float, default=15.0)
    parser.add_argument("--skip-feedback", action="store_true", help="跳过纠错反馈那一步")
    parser.add_argument("--json", action="store_true", help="把完整卡片 JSON 打到屏幕")
    args = parser.parse_args(argv)

    config = build_config(args)
    out_dir = Path(args.out_dir)

    temp_dir = None
    if args.db:
        db_path = Path(args.db)
    else:
        temp_dir = tempfile.TemporaryDirectory(prefix="feishu-bot-smoke-")
        db_path = Path(temp_dir.name) / "smoke.db"

    print("=" * 78)
    print("飞书机器人本地冒烟（不连飞书；卡片 JSON 落盘后可贴进卡片搭建工具预览）")
    print(f"RAG      : {config.rag_base_url}{'   ← 故意指向空端口，看降级卡片' if args.dead_rag else ''}")
    print(f"会话库   : {db_path}{'（临时，跑完即弃）' if temp_dir else ''}")
    print(f"出图     : {'关' if args.no_render else f'开（超时 {config.subgraph_render_timeout:g}s）'}")
    print(f"卡片输出 : {out_dir}")
    print("=" * 78)

    db = Database(db_path)
    db.connect()
    session = SessionStore(db, ttl_hours=config.session_ttl_hours)
    rag = RagClient(config.rag_base_url, bot_api_key=config.rag_bot_api_key,
                    query_timeout=config.rag_query_timeout,
                    connect_timeout=config.rag_connect_timeout)
    feishu = RecordingFeishu()
    renderer = SubgraphRenderer(feishu, enabled=not args.no_render,
                                timeout=config.subgraph_render_timeout) if not args.no_render \
        else None
    skills = [
        HelpSkill(),
        KnowledgeQaSkill(rag=rag, session=session, renderer=renderer,
                         examples=DemoExamplesCache(rag, count=3), config=config),
        ReportErrorSkill(session=session, feishu=feishu, config=config),
    ]
    dispatcher = Dispatcher(db=db, session=session, skills=skills, feishu=feishu,
                            config=config)

    exit_code = 0
    try:
        # 0) RAG 探活（失败不阻塞，只提示）
        health = rag.health()
        ok = health.get("status") == "ok"
        status_text = "正常" if ok else "不通"
        print(f"\n[0] RAG 健康检查：{status_text}")
        if ok:
            print(f"    版本={health.get('version')} 向量={health.get('vector_available')} "
                  f"LLM={health.get('llm_available')}")
            if not health.get("llm_available"):
                print("    提醒：RAG 侧未配 LLM 密钥，回答会是离线摘要，质量受限")
            # health 正常 ≠ 接口存在：跑着改动前的旧实例时最容易被这一点噎住
            if rag.supports_json_query():
                print("    非流式接口 /api/query/json：可用")
            else:
                print("    非流式接口 /api/query/json：**不存在** —— 该地址上跑的是"
                      "改动前的旧版本，需要进行重启（python RAG/scripts/run_server.py "
                      "--version <版本>），否则每次提问都会收到降级卡片")
                exit_code = 1
        else:
            detail = health.get("message") or health.get("load_error") or health
            print(f"    原因：{detail}")
            if not args.dead_rag:
                print("    提示：RAG 不通时后面几步都会是降级卡片；--dead-rag 就是这个效果")

        # 1) /help
        feishu.replies.clear()
        dispatcher.handle_message(_message_event("/help", "smoke-help"))
        card = feishu.replies[-1]
        print(f"\n[1] /help 命令：{_summarize(card)}")
        print(f"    正文：{_body_head(card)}")
        path = _save(out_dir, "1_help", card)
        print(f"    已存：{path}")

        # 2) 首次提问（fresh 会话）
        feishu.replies.clear()
        started = time.monotonic()
        dispatcher.handle_message(_message_event(args.question, "smoke-q1"))
        elapsed = int((time.monotonic() - started) * 1000)
        card = feishu.replies[-1]
        print(f"\n[2] 提问「{args.question}」（{elapsed}ms）")
        print(f"    {_summarize(card)}")
        print(f"    正文：{_body_head(card, 120)}")
        if feishu.images:
            print(f"    子图：{'、'.join(feishu.images)}")
        if card.get("header", {}).get("template") == "orange" and not args.dead_rag:
            print("    注意：这是降级卡片。常见原因——"
                  "① RAG 刚重启，首次提问要加载词典/向量库并首次调用 embedding，"
                  "可能超过 25s 预算（再跑一次通常是热的）；"
                  "② RAG 侧模型/网络慢。慢问降级是有意设计，不是缺陷。")
        path = _save(out_dir, "2_answer", card)
        print(f"    已存：{path}")
        answer_card = card

        # 3) 追问（验历史透传）
        if args.followup:
            feishu.replies.clear()
            dispatcher.handle_message(_message_event(args.followup, "smoke-q2"))
            card = feishu.replies[-1]
            print(f"\n[3] 追问「{args.followup}」")
            print(f"    {_summarize(card)}")
            print(f"    正文：{_body_head(card, 120)}")
            path = _save(out_dir, "3_followup", card)
            print(f"    已存：{path}")
            history = session.history_for_rag("ou_smoke:oc_smoke")
            print(f"    会话历史：{len(history)} 条 "
                  f"（{[t['role'] for t in history]}）"
                  f"，总字节 {len(json.dumps(history, ensure_ascii=False).encode())}"
                  f"/{config.history_max_bytes} 上限")

        # 4) 纠错反馈（真实按钮回调路径）
        if not args.skip_feedback:
            msg_key = feedback_msg_key(answer_card)
            print(f"\n[4] 纠错反馈（按钮 msg_key={msg_key}）")
            if not msg_key:
                # 降级卡片本来就不带按钮（没有可纠错的回答）；非降级卡片没有按钮
                # 就是缺陷——这一条曾经因为按 1.0 解析而静默跳过，现在必须报出来
                degraded = answer_card.get("header", {}).get("template") == "orange"
                print("    没有反馈按钮" + ("（降级卡片不带按钮，符合预期）" if degraded
                                           else "——**异常**：非降级回答卡片应带「反馈有误」按钮"))
                if not degraded and not args.dead_rag:
                    exit_code = 1
            else:
                feishu.sent.clear()
                dispatcher.handle_card_action(_card_action(
                    {"action": "report_error", "msg_key": msg_key}, "smoke-fb"))
                if feishu.sent:
                    chat_id, ticket = feishu.sent[-1]
                    print(f"    运营群（{chat_id}）收到工单：{_summarize(ticket)}")
                    path = _save(out_dir, "4_feedback_ticket", ticket)
                    print(f"    已存：{path}")
                else:
                    print("    未投递（检查 FEISHU_OPERATORS_CHAT_ID）")
                row = session.get_feedback(1)
                if row:
                    print(f"    feedback 表：id={row['id']} status={row['status']} "
                          f"question={row['question']!r} answer={len(row['answer_md'])} 字符")
                else:
                    print("    feedback 表里没有记录（异常，应有一条 open 记录）")

        # 5) 去重（同一 event_id 投递两次：入队 1 次、丢弃 1 次）
        #    走 on_message（SDK 回调的真实入口），因此喂**原始**事件对象
        before = dict(dispatcher.stats)
        dispatcher.on_message(_raw_message_event("重复投递测试", "smoke-dup"))
        dispatcher.on_message(_raw_message_event("重复投递测试", "smoke-dup"))
        enqueued = dispatcher.stats["enqueued"] - before["enqueued"]
        duplicate = dispatcher.stats["duplicate"] - before["duplicate"]
        print(f"\n[5] 去重：同一 event_id 投递两次 → 入队 {enqueued} 次、丢弃 {duplicate} 次"
              f"（期望 1 / 1）")

        print(f"\n完成。卡片 JSON 在 {out_dir}，"
              f"可贴进飞书开放平台「卡片搭建工具」预览渲染效果。")
        print("接真实飞书前的下一步见 feishu-bot/README.md 的「验收清单」。")
    except Exception as e:  # noqa: BLE001 - 冒烟工具要把异常说清楚而不是吞掉
        print(f"\n[失败] {type(e).__name__}: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        rag.close()
        db.close()
        if temp_dir is not None:
            temp_dir.cleanup()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
