"""口令强度的**唯一**判定口径（第 13 轮复核整改 §2.1）。

## 为什么要有这个模块

这条策略原先有两份实现、两套数值：

| 入口 | 长度 |
| --- | --- |
| 注册（`DbUtil.add_user`） | 10–64 |
| 修改密码（`DbUtil.change_password`） | 10–64 |
| 首个管理员（`create_admin.py`） | **6–20** |
| 前端注册表单 | 10–64 |

两个方向的坏处都真实存在：6–9 位的弱口令可以经首管命令设进来（而它恰恰是权限最高的
那个账号）；21–64 位的强口令反而**设不上**（首管命令直接拒绝）。这不是"数值不统一"
这种洁癖问题，而是"最强的账号允许最弱的密码"。

所以把数值与判定收进这里，上面四处一律 `from password_policy import password_problem`。
前端的数值仍写在 `frontend/src/views/login/index.vue`（跨语言无法共享常量），
它的注释指明了这个模块——改数值时两处一起改，且**用例会在两侧同时报警**
（`backend/tests/test_create_admin.py` 与 `frontend/tests/component/login-failure.test.ts`）。

## 策略内容

- **长度 10–64 位**：下限决定在线爆破的搜索空间（限流只把速率压到不划算，挡不住离线撞库）；
  上限是为了不让一次登录请求变成一次 PBKDF2 长跑。
- **首尾不得有空白字符**：这里刻意**判为错误**而不是"自动去掉"。前端曾经在登录时
  `password.trim()`，那等于静默改了用户输入——历史口令若带尾随空格，用户按原样输入
  反而登录不上，而错误提示是"用户名或密码错误"。现在两侧都不改用户输入，
  由本模块对"首尾空格"给出明确文案。
- **不改用户输入**：本模块只回答"合不合规"，不做任何规范化。规范化放在这里就会
  又变成"静默改口令"，而那正是上面那条要消掉的行为。

## 登录路径不查强度

`DbUtil.authentication` **不调用**本模块：长度策略的语义是"不许设置弱口令"，
不是"不许用弱口令登录"。提高策略之前建的账号（当年允许 6 位）必须继续能登录，
否则一次策略调整就把老用户全锁在门外。这一点前端也一致（见 login/index.vue 的
`validateLoginForm` 注释），并且有 `test_create_admin.py` 的用例钉住。
"""

from __future__ import annotations

from typing import Optional

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 64


def password_problem(password: str) -> Optional[str]:
    """口令是否可用；合规返回 `None`，否则返回给用户看的中文原因。

    返回"原因字符串"而不是布尔值：调用方（注册、改密码、首管命令）都要把原因回给用户，
    让每条分支自己拼一遍文案必然会出现两套措辞（"密码长度需在…"与"口令长度需为…"），
    而这正是本模块要消掉的那类不一致。
    """
    text = "" if password is None else str(password)
    if text != text.strip():
        return "密码不能以空格、制表符等空白字符开头或结尾"
    length = len(text)
    if length < MIN_PASSWORD_LENGTH or length > MAX_PASSWORD_LENGTH:
        return (f"密码长度需在 {MIN_PASSWORD_LENGTH}~{MAX_PASSWORD_LENGTH} 位之间"
                f"（当前 {length} 位）")
    return None
