"""稳定图谱键（graph_key）——SQLite 行在图谱里的唯一身份（文档第六节第 5 条）。

## 为什么不用 Neo4j 内部 id

`id(n)` 是 Neo4j 自己分配的数字，**重建图谱（全量重导、恢复备份、换库）后会变**。
把它当作"这个 SQLite 行对应哪个图谱节点"的长期依据，会在这几件事上出问题：

- 重放补偿任务时 id 已经指向别的节点，改到错误的对象上；
- 数据重导后旧 `neo4j_id` 全部失效，但补偿任务还在，于是把上一批数据又写一遍；
- 关系同步按 id 拼两端，图谱一旦重建，关系就指到不存在或错误的节点上。

## 用什么

    graph_key = "<NodeType>:<SQLite 主键>"      # 例如 "Event:123"

它是**由主存储决定的**（SQLite 主键稳定、可预测），因此重放、重建、改名都不影响定位：
Cypher 侧一律 `MERGE (n:Label {graph_key: $graph_key}) SET n += $props`，
命中已有节点还是新建由 Neo4j 自己判定，我们不再依赖"按名字猜"。

按名字去重是不行的：`MERGE (n:Label {name: $name})` 会把**同名节点合成一个**
（"赤壁之战"在不同朝代/不同来源里存在多个），而改名又会被当成新建
（留下旧节点 + 多出一个新节点）。按 graph_key 定位后，
"改名字"就是一次属性更新，不会留下影子节点。

`neo4j_id` 仍然保留并回写，但它降级为**观测信息**（日志、可视化里对不上时排查用），
不再是任何写入路径的定位依据。
"""

from __future__ import annotations

KEY_SEPARATOR = ":"


def graph_key_for(node_type: str, node_id) -> str:
    """拼一个稳定图谱键。"""
    return f"{node_type}{KEY_SEPARATOR}{node_id}"


def parse_graph_key(graph_key: str) -> tuple[str, str]:
    """拆开图谱键，返回 `(node_type, node_id)`。

    按**最后一个**分隔符切分：节点类型里不含冒号，但主键的字符串形式将来若带上
    冒号（复合键），右切分仍然给出正确结果。
    """
    raw = str(graph_key or "")
    node_type, _, node_id = raw.rpartition(KEY_SEPARATOR)
    return node_type, node_id


def is_graph_key(value: str) -> bool:
    """是否是形如 `<Type>:<id>` 的图谱键（两侧都非空）。"""
    node_type, node_id = parse_graph_key(value)
    return bool(node_type and node_id)
