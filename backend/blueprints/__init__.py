"""按业务分组的路由蓝图（P2-1 收官：app.py 只留应用工厂、钩子与启动）。

`app.py` 在模块末尾统一 `register_blueprint`，**不加 url_prefix**——URL 必须与拆分前逐字相同。

分组与依赖方向：

    auth      认证 / 账号 / 用户管理 / 菜单权限   ← roles, db_utils, jwt_util
    node      节点增删改查与节点查询              ← db_utils, db_handle, roles
    graph     图谱可视化与检索                    ← db_handle, report_builders
    workspace 数据运营与只读报表                  ← report_builders
    llm       旧问答与文本抽取                    ← llm_pipeline, roles, common_utils

蓝图**不得 import app**（会形成循环，与 report_builders / llm_pipeline 同一条规矩）。
"""

# 统一在这里导出，app.py 只写一行 register_blueprint。
# 顺序即注册顺序（Flask 按注册顺序匹配，但五组之间没有重叠的 URL 规则，顺序只影响可读性）。
from blueprints.auth import auth_bp  # noqa: E402
from blueprints.graph import graph_bp  # noqa: E402
from blueprints.llm import llm_bp  # noqa: E402
from blueprints.node import node_bp  # noqa: E402
from blueprints.workspace import workspace_bp  # noqa: E402

__all__ = ["auth_bp", "graph_bp", "llm_bp", "node_bp", "workspace_bp"]

