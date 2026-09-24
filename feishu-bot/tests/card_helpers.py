"""卡片结构的测试辅助（按卡片 JSON 2.0 的真实形态取值）。

为什么单独抽出来：按钮在 2.0 里是 `elements` 的直接成员、回传参数在
`behaviors[type=callback].value`（没有 1.0 的 `action` 容器与顶层 `value`）。
多个测试文件都要按这个规则取值，散落各处就会在结构变更时一起失效。
"""

from __future__ import annotations

from typing import Iterator


def iter_elements(card: dict) -> Iterator[dict]:
    """深度遍历卡片元素（含折叠面板内的子元素）。"""
    stack = list((card.get("body") or {}).get("elements") or [])
    while stack:
        element = stack.pop(0)
        yield element
        stack = list(element.get("elements") or []) + stack


def elements(card: dict) -> list[dict]:
    return list(iter_elements(card))


def top_elements(card: dict) -> list[dict]:
    """只取 body 的直接子元素（不含折叠面板内部的）。"""
    return list((card.get("body") or {}).get("elements") or [])


def buttons(card: dict) -> list[dict]:
    return [e for e in iter_elements(card) if e.get("tag") == "button"]


def button_value(button: dict) -> dict:
    """取按钮的回传参数（2.0：behaviors 里的 callback value）。"""
    for behavior in button.get("behaviors") or []:
        if isinstance(behavior, dict) and behavior.get("type") == "callback":
            value = behavior.get("value")
            return value if isinstance(value, dict) else {}
    return {}


def button_values(card: dict) -> list[dict]:
    return [button_value(b) for b in buttons(card)]


def feedback_msg_key(card: dict) -> str | None:
    """取「反馈有误」按钮携带的 msg_key。"""
    for value in button_values(card):
        if value.get("action") == "report_error":
            return str(value.get("msg_key"))
    return None


def markdown_text(card: dict) -> str:
    """卡片里所有 markdown 元素的文本（断言文案用）。"""
    return "\n".join(e.get("content", "") for e in iter_elements(card)
                     if e.get("tag") == "markdown")
