# server/graph（F03）

**归属功能：F03 图谱检索通道（在线 GraphRAG）。**

## 职责（文件）

| 文件 | 说明 |
| --- | --- |
| `graph_index.py` | 启动加载快照 entities.json + relations.json 为内存图（entity by id/name、邻接出/入边、pending_review 边排除）。 |
| `query_strategies.py` | 问题类型 → 查询策略映射 + 事件-事件关系名集合。 |
| `search.py` | 按问题类型执行查询：single_entity 属性+1 跳 / relation 邻接 / event_event 事件关系+两跳路径 / comparison / timeline / background，输出 graph_triple 证据 + 命中实体。 |
| `__init__.py` | 对外 load_graph() / search()。 |

## 设计要点（RAGv2 规划第 3 节落地）

1. 服务启动时加载为内存图（不在请求时建图），版本与索引一致（runtime 校验）。
2. 孤立节点不返回虚构关系（只进 hit_entities 由 F05 做实体卡）。
3. 同名多实体全部参与匹配；filters（朝代/事件类型）作为图谱节点过滤条件。
4. event_event：直接事件关系（因果关系/顺承关系/并列关系/包含关系/条件关系）+ 双事件两跳路径。

## 输入 / 输出

- 输入：F02 标准实体名列表 + QuestionType + filters
- 输出：GraphResult{evidence: graph_triple[], hit_entities, related_event_ids}

## 边界

- 图谱检索深度默认 1~2 跳（路径上限），更深待 F10 评测后调整。
- 不做跨实体多跳泛化推理，只做图谱既有事实检索。
