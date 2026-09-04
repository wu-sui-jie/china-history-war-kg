# RAGv2 第三方复核修订：总结分析文档

- 日期：2026-09-04
- 项目：RAG 智能问答（`RAG/` 子项目）
- 前置文档：[20260904-ragv2-review-fix-requirements.md](20260904-ragv2-review-fix-requirements.md)

## 一、问题分析与定位

### 1. 缓存命中回放缺 panel

`server/sse.py` 缓存命中分支只回放 answer/citations/done，正常路径在
`runtime.fusion.assemble()` 后产生的 `panel` 未进入缓存 payload，导致
“第二次同问”时前端收不到知识面板数据。

修复点：

- `server/sse.py`：命中分支在 `done` 前补发 `panel`；
- `server/generate/cache.py`：`build_cache_payload` 增加可选 `panel`，
  由 sse 在生成完成时写入缓存；
- `server/generate/README.md`、`server/generate/cache.py` docstring：
  同步真实回放顺序（F02 实体事件在前，`status(cache_hit)` 随后）。

### 2. filters 未传给文本检索且 event_type 过滤为空

`server/sse.py` 文本检索调用写死 `filters=None`；`server/text/searcher.py`
的 `_pass_meta` 对 event_type 分支是 `pass`；且 `chunks_fts.db` 的 chunks
表未持久化 event_type，因此即使传参也无法过滤战争类型。

修复点：

- `server/sse.py`：`tsearch(...)` 传入 `filters`；
- `server/text/searcher.py`：SELECT event_type，并按 event_type 真实过滤；
- `data/index/fts.py`：chunks 表增加 `event_type TEXT` 列并写入；
- 本机重建 `RAG/data/index/20260904_v2`（数据产物不入库），
  校验 `event_card` 1107 条全部有 event_type；
- `RAG/data/index/README.md`：补充 event_type 元数据说明。

### 3. history_max_turns 死参数

`server/query/understand.py::__init__` 接收 `history_max_turns` 但未保存使用。

修复点：保存参数，并在 `understand()` 中按最近 N 个 user 轮保留历史
（同时保留其后的 assistant 消息）。

### 4. RAGv3 预开发文档错误假设与未定决策

`docs/RAG_v1/RAGv3-规划分析.md` 原称“图谱+文本都过滤，后端已支持”，
与修复前代码不符；筛选接口仍在二选一；SSE 对接面未说明 thinking 事件
后端不发射。

修复点：

- 修正 filters 声明，改为“RAGv2 复核修复后已具备”；
- 筛选接口拍板为 `GET /api/dicts`，并约定返回 `version`；
- 明确 `contracts/sse.py::THINKING` 为保留枚举，RAGv2 后端当前不发射；
- 同步 `docs/features/06-grounded-answer.md` 缓存回放顺序；
- `docs/RAG_v1/RAGv2-在线问答链路.md` 增加“第三方复核修复记录”。

## 二、修改文件清单

代码：

- `RAG/server/sse.py`
- `RAG/server/generate/cache.py`
- `RAG/server/text/searcher.py`
- `RAG/data/index/fts.py`
- `RAG/server/query/understand.py`

文档：

- `RAG/docs/RAG_v1/RAGv2-在线问答链路.md`
- `RAG/docs/RAG_v1/RAGv3-规划分析.md`
- `RAG/docs/features/06-grounded-answer.md`
- `RAG/data/index/README.md`
- `RAG/server/generate/README.md`
- `RAG/docs/changes/20260904-ragv2-review-fix-requirements.md`
- `RAG/docs/changes/20260904-ragv2-review-fix-summary.md`（本文件）

第二轮文档补充（RAGv3 开工提示）：

- `RAG/docs/RAG_v1/RAGv3-规划分析.md`：补充缓存命中/全量检索两条 SSE 路径、
  长改写问题文本召回质量冒烟口径，并把状态更新为“预开发分析完成”；
- `RAG/docs/RAG_v1/README.md`、`RAG/docs/README.md`：同步阶段状态。

## 三、验证结果

使用 Python 3.11（`E:/anaconda/envs/AI_Agent`）直接调用 `run_query` 复测：

1. 同问二次：事件序列含
   `status(cache_hit) -> answer -> citations -> panel -> done`，
   `has_panel=True`，`finish_reason=normal`、`cache_hit=True`；
2. 带 `Filters(dynasty=["三国"], event_type=["统一战争"])`：
   文本检索收到 `filters`，检索模式 `keyword`；
3. event_type 过滤：query “牧野” + `统一战争/商` 命中 2 条，
   `诸侯争霸` 命中 0 条，不过滤命中 19 条；
4. history 裁剪：10 条 user/assistant 历史裁剪后保留最近 4 个 user 轮；
5. 全部修改文件通过 `py_compile`。

第二轮复测补充（2026-09-04）：

1. 缓存命中与全量检索是两条独立 SSE 路径：命中路径无 graph/text/fusion
   事件，属设计行为而非丢事件；
2. F04 长改写问题 AND 优先几乎必然失败：5 个自然长问题 AND 均命中 0，
   OR 兜底返回约 30 条但排序噪音明显，RAGv3 冒烟应断言“相关文本证据存在”，
   不能只断言 text_results 非空。

## 四、未完成 / 未验证事项

- 未启动真实 LLM key 联调：F06 仍走离线摘要回答器，属 RAGv2 既有边界；
- 未实现 RAGv3 前端页面（不在本次范围）；
- `GET /api/dicts` 仅完成决策与文档，业务代码留到 RAGv3 联调前实现；
- raw/evidence 片段暂无 dynasty/event_type 元数据，事件筛选目前对
  事件卡片片段精确生效，其余片段不参与精确事件筛选。
