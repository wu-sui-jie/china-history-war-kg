# server

**归属功能：F02–F06 在线问答链路（FastAPI，请求时运行）。**

RAGv2 已实现：SSE 问答接口 `POST /api/query` 打通
F02 问题理解 → F03 图谱检索 + F04 文本检索 → F05 融合重排（含冲突与 panel 装配）→
F06 回答生成，事件序列严格按 `docs/data-contract.md`；RAGv3 追加
`GET /api/dicts` 供前端筛选下拉使用。

## 分层（子目录）

| 目录 | 功能 | 职责 |
| --- | --- | --- |
| `query/` | F02 | 词典/规则优先实体识别、问题类型判定、多轮指代消解、查询改写（`F02Output`）、同名歧义降级 candidates、corrected_entities 纠正。 |
| `graph/` | F03 | 启动加载治理快照为内存图（entities+relations），按问题类型执行 1 跳/两跳查询，输出 graph_triple + 命中实体。 |
| `text/` | F04 | 读 F11 FTS5（关键词 AND/OR），分数 min-max 归一，向量可用性校验（不可用自动关键词）。 |
| `fusion/` | F05 | 证据合并去重、按问题类型加权排序、分配 citation_index、结构化冲突判定（读 relation_card_field_map.json）、panel 数据装配（唯一装配方）。 |
| `generate/` | F06 | 提示词构造、LLM 流式回答（超时/重试/备用降级）、无 key 离线摘要回答器、拒答、回答缓存。 |
| `api.py` | 入口 | FastAPI app：`POST /api/query`（SSE），`GET /api/health`，`GET /api/dicts`，基础限流。 |
| `runtime.py` | 装配 | 启动加载快照/索引并校验版本一致，组装各层。 |
| `sse.py` | 编排 | `run_query()` SSE 事件序列编排（事件顺序见 data-contract）。 |

## 关键约定

1. 所有请求/响应字段引用 `contracts/`（不自行另造）。
2. SSE 事件顺序与 payload 严格按 data-contract.md。
3. panel 数据只由 fusion(F05) 装配，graph/text 不重复拼 subgraph。
4. 图谱检索在服务启动时加载快照（`data/snapshot/<v>/`），文本检索读 `data/index/<v>/`，
   两者版本号一致（manifest 校验，见 runtime.resolve_version）。
5. 事件 type/stage 枚举定义在 `contracts/sse.py`，不在编排层硬编码字符串。

## 运行

```bash
# 从 RAG/ 根（Python 3.11；依赖见 ../requirements.txt，含 fastapi/uvicorn/openai）
python scripts/run_server.py --port 8000

# 冒烟（无 LLM key 也能跑检索链，F06 走离线摘要回答器）
curl -N -X POST http://127.0.0.1:8000/api/query \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"session_id":"s1","question":"赤壁之战的主帅是谁？"}'
```

配置（`.env`）：`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`（真实生成）、
`FALLBACK_LLM_*`（备用模型）、`RATE_LIMIT_PER_MINUTE`、`CACHE_TTL_SECONDS`、
`QUERY_TOP_K_GRAPH/TEXT`、`HISTORY_MAX_TURNS`。

## 边界 / 不做什么

- F02 LLM 兜底默认关闭（词典优先默认路径；`enable_llm` 开关预留）。
- F04 向量模式已接入（Chroma + 云端 embedding）；集合缺失/条数不一致/无密钥时
  `vector_available=False` 自动降级关键词并在 `text_results.mode` 上报实际模式。
- F06 无 key 时用离线摘要回答器（model_used=heuristic-offline），非规划定义 degraded。
- 不包含前端页面（RAGv3）；不包含 F10 评测。
- 实现细节与阶段边界详见 [../docs/CHANGELOG.md](../docs/CHANGELOG.md)。

## 状态

- [x] F02 query 层
- [x] F03 graph 层
- [x] F04 text 层（关键词 + 向量 + hybrid/rrf，向量不可用自动降级）
- [x] F05 fusion 层
- [x] F06 generate 层（无 key 离线回答器 + LLM 接入点）
- [x] SSE 编排 + 限流 + 缓存
