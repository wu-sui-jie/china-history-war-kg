"""契约层公共基类与序列化工具。"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class BaseModel:
    """基类：提供 to_dict()（剔除 None 的可选字段，保持 JSON 与契约一致）。"""

    def to_dict(self, skip_none: bool = True) -> dict:
        d = asdict(self)
        if skip_none:
            return {k: v for k, v in d.items() if v is not None}
        return d
