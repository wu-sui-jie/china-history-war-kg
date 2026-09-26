"""按业务分组的路由蓝图（app.py 只留应用工厂、钩子与启动）。

`app.py` 在模块末尾统一 `register_blueprint`，**不加 url_prefix**——URL 完全由蓝图里的
路由装饰器决定，多一层前缀会让前端调用全部失配。

分组与依赖方向：

    auth      认证 / 账号 / 用户管理 / 菜单权限   ← roles, db_utils, jwt_util
    node      节点增删改查与节点查询              ← db_utils, db_handle, roles
    graph     图谱可视化与检索                    ← db_handle, report_builders
    workspace 数据运营与只读报表                  ← report_builders
    llm       旧问答与文本抽取                    ← llm_pipeline, roles, common_utils
    internal  服务间内部接口（RAG 查询凭证状态）   ← db_utils, jwt_util

`internal` 与前五组不同：它不面向前端用户，而是面向上游的 RAG 服务，
鉴权用服务间共享密钥而不是用户 JWT（见 internal.py 的模块文档）。

蓝图**不得 import app**（会形成循环，与 report_builders / llm_pipeline 同一条规矩）。
"""

# 统一在这里导出，app.py 只写一行 register_blueprint。
# 顺序即注册顺序（Flask 按注册顺序匹配，但各组之间没有重叠的 URL 规则，顺序只影响可读性）。
from blueprints.auth import auth_bp  # noqa: E402
from blueprints.graph import graph_bp  # noqa: E402
from blueprints.internal import internal_bp  # noqa: E402
from blueprints.llm import llm_bp  # noqa: E402
from blueprints.node import node_bp  # noqa: E402
from blueprints.workspace import workspace_bp  # noqa: E402

__all__ = ["auth_bp", "graph_bp", "internal_bp", "llm_bp", "node_bp", "workspace_bp"]
