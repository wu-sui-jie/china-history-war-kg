# RAGv2 第三方复核修订：需求分析文档

- 日期：2026-09-04
- 项目：RAG 智能问答（`RAG/` 子项目）
- 状态：已确认并执行（2026-09-04 完成代码修复、文档修订、验证与提交）
- 触发来源：RAGv2 完成度与 RAGv3 预开发文档分析报告（其他模型复核结果）

## 一、复核结论（先全量总结问题）

经对照 `RAG/server` 实际代码并在本机直接调用 `run_query` 复现，第三方报告中的结论总体可信：

1. **回答缓存命中回放丢失 panel 事件**
   - `RAG/server/sse.py` 缓存命中分支（约 110-125 行）只回放
     `answer -> citations -> done`；
   - 缓存 payload（约 216-222 行）没有保存 `panel`；
   - 实测第二次同问时事件序列为：
     `session_start -> status -> entities -> status(cache_hit) -> answer -> citations -> done`，
     **没有 `panel`**；
   - 与 [RAGv2-在线问答链路.md](../../RAG_v1/RAGv2-在线问答链路.md) 第 61/103 行、
     [F06](../../features/06-grounded-answer.md) 中“缓存命中含 panel”的声明不符，
     也与 RAGv3 的 F07“面板不空白”验收冲突。

2. **filters 未传给文本检索，且 event_type 过滤目前是 no-op**
   - `RAG/server/sse.py` 第 146 行调用 `tsearch(...)` 时硬编码 `filters=None`；
   - `RAG/server/text/searcher.py::_pass_meta` 只实现了 dynasty/chunk_type，
     `event_type` 分支实际是 `pass`；
   - 当前 `chunks_fts.db` 的 `chunks` 表没有 `event_type` 列，即便 sse 传了 filters，
     事件卡片片段也无法按战争类型过滤；
   - 实测带 `Filters(dynasty=["三国"], event_type=["统一战争"])` 请求时，
     文本检索收到 `filters=None`；
   - 与 [RAGv3-规划分析.md](../../RAG_v1/RAGv3-规划分析.md) 第 119 行
     “图谱+文本都过滤，后端已支持”的声明不符。

3. **`history_max_turns` 配置项是死参数**
   - `RAG/server/query/understand.py::__init__` 接收参数但未保存；
   - 当前不影响核心问答，但配置声明与实际行为不一致。

4. **RAG 工作区尚未提交固化**
   - `RAG` 是独立 Git 仓库，当前只有 RAGv1 初始提交；
   - RAGv2 新增的 `server/`、脚本、契约与 v2/v3 文档均处于 modified/untracked 状态。

5. **RAGv3 文档需要小幅修正**
   - 第 119 行“文本通道后端已支持 filters”不成立；
   - SSE 事件表未说明 `contracts/sse.py` 中的 `thinking` 枚举后端当前不发射；
   - “GET /api/dicts 或前端内置清单”二选一，建议按报告拍板为
     “后端补 `GET /api/dicts` 并返回 `version`”，避免 RAGv3 实现时再选择困难。

## 二、修改方案

### 1. 后端修复：缓存 panel（高优先级）

- `RAG/server/sse.py`：
  - 生成完成路径把 `panel` 序列化结果写入缓存 payload；
  - 缓存命中分支补发 `panel` 事件，再发 `done`。
- `RAG/server/generate/cache.py`：
  - 同步更新 `build_cache_payload` 签名/文档，支持可选的 `panel`，
    并让 sse.py 使用该 helper，避免“helper 未使用”的漂移。

预期结果：二次同问的事件序列与正常路径一致，前端可收到 panel，F07 不空白。

### 2. 后端修复：filters 传递 + event_type 真实过滤（高优先级）

- `RAG/server/sse.py`：`tsearch` 调用改为传入 `filters`。
- `RAG/data/index/fts.py`：`chunks` 表增加 `event_type TEXT` 列，
  建库时从 chunk dict 写入该字段。
- `RAG/server/text/searcher.py`：
  - `_fetch_chunks` SELECT 增加 `event_type`；
  - `_pass_meta` 补真实 `event_type` 过滤（不再 `pass`）。
- 本机重建 `20260904_v2` 索引（`data/` 不入库，仅本地运行产物），
  保证当前数据库与新代码匹配。

说明：raw/evidence 片段当前没有 dynasty/event_type 元数据，
筛选时按“无匹配元数据则不过滤该片段”处理只对事件卡片类片段精确生效；
如需 raw/evidence 也按事件筛选，需要更完整的元数据映射，列入后续 F10/数据治理范围，
不在本次扩大。

### 3. 后端清理：history_max_turns 接线（低优先级）

- `RAG/server/query/understand.py`：保存该参数并在 `understand()` 中对
  history 做最近 N 轮裁剪（以最近 user 轮为界，保留其后的 assistant 消息）。
- 不改现有契约字段，仅让配置真正生效。

### 4. 文档修订

- `RAG/docs/RAG_v1/RAGv2-在线问答链路.md`：修正缓存命中事件说明，
  在验证表增加“复核修复后二次同问含 panel”结果。
- `RAG/docs/RAG_v1/RAGv3-规划分析.md`：
  - 修正第 119 行 filters 声明（文本过滤以修复后的后端为准）；
  - 对接面表格或说明处补一句 `thinking` 事件后端暂不发射；
  - 筛选接口拍板为 `GET /api/dicts`，并约定返回 `version`。
- `RAG/docs/features/06-grounded-answer.md`：如事件顺序表述与实际实现有出入，
  同步为修复后的真实顺序。
- `RAG/data/index/README.md`：补充 `chunks` 表含 `event_type` 列用于筛选的说明。

### 5. 验证

- 直接调用 `run_query` 做两个冒烟用例：
  1. 同问题二次：断言事件序列含 `panel`；
  2. 带 filters 请求：断言文本检索收到 filters，且 event_type 过滤生效。
- 健康检查可启动即可；不接 LLM key，F06 走离线摘要回答器。

### 6. Git 固化

- 在 `RAG` 独立仓库新建分支：`codex/ragv2-review-fixes`；
- 提交 RAGv2 完整交付物 + 本次修复；
- 推送 `origin`（`https://github.com/wu-sui-jie/china-history-war-kg.git`）；
- 不推送到 main 分支，不包含 `data/`、`logs/`、`.env` 等被忽略内容。

## 三、本次不执行 / 不采纳项

- 不实现 RAGv3 前端（任务尚未开工）；
- 暂不在本次实现 `GET /api/dicts` 的业务代码，只把文档决策定死；
- 不修改旧 `backend/`、`frontend/`、`entity-event-relation/` 代码；
- 第三方报告中的“95% 完成度”仅作参考，不作为验收结论。
