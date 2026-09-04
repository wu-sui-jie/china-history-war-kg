# frontend

**归属功能：F01 智能问答主界面 + F07 可视化知识面板（单页）。**

> ⚠️ 本层为 RAGv3（前端），尚未开始实现。**任务分析见
> [../docs/RAG_v1/RAGv3-规划分析.md](../docs/RAG_v1/RAGv3-规划分析.md)**（范围、模块拆解、
> SSE 对接、验收、风险、实施顺序）。需求见 docs/features/01-qa-main.md 与 07-knowledge-panel.md。
> 技术选型：Vue 3 + TypeScript + Vite（架构文档）。

## 关键结论（来自 RAGv3 规划分析）

1. **新建独立前端**（RAG/frontend），不复用旧 frontend 的 layui-vue-admin 后台模板壳
   （登录/菜单/mock/多页后台与 F01 验收冲突），依赖选型沿用已验证的 Vue3/TS/pinia/
   echarts/relation-graph-vue3。
2. **SSE 用 fetch 流**（EventSource 不支持 POST body）；事件顺序与负载见 data-contract.md，
   后端真实负载已核对（见 RAGv3 规划分析"四、后端已就绪的对接面"）。
3. 知识面板数据全部来自 SSE panel 事件，前端不自行拼第二套 subgraph；
   map_points 现为 0 → 地图降级为地点列表（RAGv1 坐标覆盖率为 0）。
4. 引用 `[n]` 在整段累积后统一高亮（answer 按句增量可能跨 delta）。
5. 需后端补 `GET /api/dicts`（朝代/战争类型清单供筛选下拉），属低风险小增强。

## 规划模块

| 模块 | 功能 | 说明 |
| --- | --- | --- |
| `api/sse.ts` | F01 | fetch 流式 SSE 客户端、事件分发、AbortController 取消。 |
| `stores/session.ts` | F01 | 会话本地持久化（localStorage，刷新保留）。 |
| `views/QaPage.vue` | F01 | 单页布局：左对话 + 右面板 + 顶筛选。 |
| `components/chat/` | F01 | 输入框、消息流、状态条、引用气泡、实体纠正。 |
| `components/panel/` | F07 | EntityCard / SubGraph / Timeline / Map(降级地点) / Evidence。 |
| `api/dicts.ts` | F01 | 筛选选项（朝代/战争类型，来源后端 /api/dicts）。 |

## 状态

- [ ] F01 主界面 + 会话
- [ ] F07 知识面板
- [ ] SSE 流式 + 纠正交互

规划进度以 [RAGv3-规划分析.md](../docs/RAG_v1/RAGv3-规划分析.md) 为准。
