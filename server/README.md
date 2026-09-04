# server

**归属功能：F02–F06 在线问答链路（FastAPI，请求时运行）。**

> ⚠️ 当前为离线阶段，本层尚未实现。目录结构与边界已按 architecture.md 与
> data-contract.md 定好，后续按功能阶段逐个填充。每个子目录会自带 README。

## 分层（子目录）

| 目录 | 功能 | 职责（规划） |
| --- | --- | --- |
| `query/` | F02 | 问题类型判定、词典/LLM 实体识别、指代消解、查询改写（`F02Output`）。 |
| `graph/` | F03 | 加载治理快照（内存图谱），按问题类型执行 1~2 跳查询，输出 `graph_triple` 证据。 |
| `text/` | F04 | 读 data/index FTS5/向量索引，关键词/向量/混合三种检索，分数归一化。 |
| `fusion/` | F05 | 证据合并去重、按问题类型加权排序、分配 citation_index、冲突判定、panel 装配。 |
| `generate/` | F06 | 构造提示词、SSE 流式回答、拒答、缓存、主备模型降级。 |
| `api.py` | 入口 | FastAPI app：`POST /api/query`（SSE 响应），编排 query→graph+text→fusion→generate。 |

## 关键约定

1. 所有请求/响应字段引用 `contracts/`（不自行另造）。
2. SSE 事件顺序与 payload 严格按 data-contract.md。
3. panel 数据只由 fusion(F05) 装配，graph/text 不重复拼 subgraph。
4. 图谱检索在服务启动时加载快照（`data/snapshot/<v>/`），文本检索读 `data/index/<v>/`，
   两者版本号一致（manifest 校验）。

## 依赖与配置

- FastAPI / uvicorn / openai 客户端；LLM 与向量模型密钥走 `.env`（见 `.env.example`）。
- 主模型 deepseek-v4-flash；备用模型降级；回答缓存（键见 F06 文档）。
- 无登录公开接口需基础限流。

## 状态

- [ ] F02 query 层
- [ ] F03 graph 层
- [ ] F04 text 层
- [ ] F05 fusion 层
- [ ] F06 generate 层
- [ ] SSE 编排 + 限流 + 缓存
