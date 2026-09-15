# server/generate（F06）

**归属功能：F06 证据溯源回答生成（在线）。**

## 职责（文件）

| 文件 | 说明 |
| --- | --- |
| `prompts.py` | 构造 system 提示词（证据块 + 引用编号 + 防泄露要求/固定回复）、检测提示词泄露请求。 |
| `llm_client.py` | OpenAI 兼容流式客户端（默认 `deepseek/deepseek-v4.1-flash`）：超时/重试/备用模型降级；`thinking` 双端字段兼容（reasoning / reasoning_content）；无 key → available=False。 |
| `refusal.py` | 拒答判定与文案：无证据 / 无共享词 / 领域外谓词（词表 + 证据含词放行）/ **模型自拒识别**（`detect_model_refusal`）。 |
| `cache.py` | 回答缓存：键 = rewritten + 历史摘要 + filters + 数据版本 + 模型版本；TTL。 |
| `__init__.py` | AnswerGenerator：generate() 三层路径（LLM 流式 / 离线摘要回答器 / 拒答）+ citations 组装。 |

## 三层生成路径

1. **LLM 可用**（`.env` 配好 `LLM_BASE_URL/LLM_API_KEY`）→ 真实流式回答（正文增量 +
   `thinking` 推理增量）；主模型失败重试后自动降级备用模型（finish_reason=degraded）；
   正文为空（推理吃满 `max_tokens`）也降级到离线摘要回答器；模型自拒文本
   （`detect_model_refusal`）→ finish_reason=refused。
2. **无 key（演示/离线）** → 启发式把融合证据组织成带引用编号的可读回答
   （model_used=heuristic-offline，finish_reason=normal）。这是降级演示器，不是规划的 degraded。
3. **无证据** → 拒答文案（finish_reason=refused）。

## 提示词与防泄露（features/06 落地）

- 系统提示词与检索上下文只存后端，只以"证据块+引用编号"形式进模型；
- 提示词明令不得输出系统指令/检索 query/内部工具/证据组装规则；
- 检测"展示系统提示词"类请求 → 固定回复（不调用模型）。

## 缓存

命中时按 SSE 回放 session_start→status(entity_linking)→entities→
status(cache_hit)→完整 answer→citations→panel→done，不重复调用模型
（panel 随缓存保存；见 `server/sse.py`）。

## 边界

- 接入真实 key 后 answer 按 token 增量流式；离线回答器/拒答文案按句切分模拟打字机
  （见 `server/sse._chunk_answer_stream`，增量拼接严格等于原文）。
- 回答缓存在进程内（多实例需共享存储）。
