"""Neo4j 连接的单例持有处。

`neo4j_db_handle` 原先定义在 `app.py`。报表构建器拆到 `report_builders.py` 之后，
两边都要用它；放在这里让 `app` 与 `report_builders` 各自 import，避免循环依赖。

注意：导入本模块即建立 Neo4j 连接（与原先 app.py 的行为一致），
口令缺失时会在启动阶段直接报出配置指引。
"""

from model_search import neo4j_db

neo4j_db_handle = neo4j_db()
