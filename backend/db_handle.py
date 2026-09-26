"""Neo4j 连接的单例持有处。

`neo4j_db_handle` 由 `app` 与 `report_builders` 共用；集中在这里让两边各自 import，
避免循环依赖。

注意：导入本模块即建立 Neo4j 连接，
口令缺失时会在启动阶段直接报出配置指引。
"""

from model_search import neo4j_db

neo4j_db_handle = neo4j_db()
