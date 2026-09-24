# contracts

共享数据契约层（对应 docs/data-contract.md）。

实现：全部跨层共享的数据结构与枚举的**唯一权威定义**。F03/F04/F05/F06/F07、
离线快照与索引、SSE 事件都引用这里，不在各层复制字段。

## 本层是什么

data-contract.md 规定了一组统一结构。本层把它们落成 Python dataclass + 枚举：

| 结构 | 归属 | 文件 |
| --- | --- | --- |
| 证据对象（evidence）与 kind/source_type/confidence | F03/F04/F05/F06 | `evidence.py` |
| 问题类型枚举 | F02 判定、F03/F04/F05 消费 | `question.py` |
| 查询请求 / F02 内部输出 / 纠正实体 | F01→F06 链路 | `request.py` |
| 检索通道输出信封（GraphResult/TextResult/FusionOutput 等） | F03/F04/F05 | `retrieval.py` |
| SSE 事件协议与事件类型 | F01/F06 | `sse.py` |
| 非流式问答结果（SSE 帧聚合后的完整结果） | `POST /api/query/json` | `query_json.py` |
| 知识面板数据（entity_cards/subgraph/timeline/map_points） | F05 装配 / F07 展示 | `panel.py` |
| 冲突对象与 conflict_type | F05 | `conflict.py` |
| 治理输出：词典 / 版本 / 战争类型映射 / 关系-卡片字段映射 | F09 离线 | `governance.py` |
| 索引输出：片段 / 片段类型 | F11 离线 | `index.py` |

## 为什么这里用 dataclass 而不是 dict

- 离线脚本与后续在线接口共享同一套字段名，杜绝“字典键拼错、两边对不上”。
- `.to_dict()` 统一序列化；缺失可选字段不落盘，保证与 data-contract JSON 示例一致。
- 枚举把 kind/source_type/confidence/question_type 的取值约束在文档范围内。

## 边界 / 约定

- contracts **只定义结构与枚举，不承载逻辑**（不做检索/打分/序列化之外的业务）。
- 任何字段调整：先改 `docs/data-contract.md`，再同步本层与受影响功能文档（项目级约定）。
- 各数据层/在线层 `import` 这里，不得自行另造一套字段。
