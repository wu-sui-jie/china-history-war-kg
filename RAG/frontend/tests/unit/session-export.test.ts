/** 会话导出 Markdown 的用例。
 *
 * 验收口径：导出的 .md 里，回答正文的 `[n]` 引用编号必须能与
 * 来源清单逐条对上；失败/取消轮次如实标注且不留空正文段落。
 */

import assert from 'node:assert/strict'
import { test } from 'vitest'

import type { AssistantMessage, ChatMessage, UserMessage } from '@/stores/session'
import {
  buildSessionMarkdown,
  exportFilename,
  formatStamp,
  sanitizeFilename,
} from '@/utils/sessionExport'

function userMsg(question: string, at: number): UserMessage {
  return {
    id: `u-${at}`, role: 'user', question, filters: { dynasty: [], event_type: [] }, createdAt: at,
  }
}

function assistantMsg(over: Partial<AssistantMessage>): AssistantMessage {
  return {
    id: 'a1', role: 'assistant', question: '', answer: '', citations: [], conflicts: [],
    entities: [], candidates: [], entityCards: [], panel: null, status: [],
    turnStatus: 'completed', streaming: false, cancelled: false, finished: true,
    correctedEntities: [], createdAt: 0,
    ...over,
  } as AssistantMessage
}

const AT = new Date('2026-09-20T20:05:00').getTime()

test('正文引用编号与来源清单逐条对上', () => {
  const messages: ChatMessage[] = [
    userMsg('介绍一下赤壁之战。', AT),
    assistantMsg({
      question: '介绍一下赤壁之战。', createdAt: AT,
      answer: '赤壁之战是东汉末年的关键战役[1]，主战场在赤壁[2]。',
      citations: [
        { index: 1, evidence_id: 'e1', kind: 'raw_text', title: '三国志·吴书', snippet: '建安十三年' },
        { index: 2, evidence_id: 'e2', kind: 'graph_triple', title: '赤壁之战 — 主战场 → 赤壁', snippet: '' },
      ],
    }),
  ]
  const md = buildSessionMarkdown(messages, { sessionId: 'session-1', exportedAt: AT })
  assert.match(md, /^# 中国历代战争史问答 · 会话导出/)
  assert.match(md, /赤壁之战是东汉末年的关键战役\[1\]/)
  assert.match(md, /\*\*\[1\]\*\* 三国志·吴书 · raw_text/)
  assert.match(md, /\*\*\[2\]\*\* 赤壁之战 — 主战场 → 赤壁 · graph_triple/)
  assert.match(md, /`session-1`/)
  assert.match(md, /- 轮次：1 轮/)
  // 正文里的 [1]/[2] 与清单条目一一对应（不悬空、不重号）
  for (const n of [1, 2]) assert.match(md, new RegExp(`\\*\\*\\[${n}\\]\\*\\* `))
})

test('失败轮如实标注状态且不留空正文段落', () => {
  const messages: ChatMessage[] = [
    userMsg('介绍一下官渡之战。', AT),
    assistantMsg({ question: '介绍一下官渡之战。', turnStatus: 'failed', finished: false, createdAt: AT }),
  ]
  const md = buildSessionMarkdown(messages, { exportedAt: AT })
  assert.match(md, /本轮生成失败，没有回答。/)
  assert.ok(!md.includes('**回答**\n\n\n'), '空回答不应产生连续空行')
})

test('被取代的轮次标注来源，正文仍导出', () => {
  const messages: ChatMessage[] = [
    userMsg('介绍一下井陉之战。', AT),
    assistantMsg({
      id: 'a1', question: '介绍一下井陉之战。', answer: '旧回答', createdAt: AT, supersededBy: 'a2',
    }),
    userMsg('介绍一下井陉之战。', AT),
    assistantMsg({ id: 'a2', question: '介绍一下井陉之战。', answer: '新回答', createdAt: AT }),
  ]
  const md = buildSessionMarkdown(messages, { exportedAt: AT })
  assert.match(md, /已被后续的纠正重查取代/)
  assert.match(md, /旧回答/)
  assert.match(md, /新回答/)
  assert.match(md, /- 轮次：2 轮/)
})

test('空会话导出仍然是一份合法文档', () => {
  const md = buildSessionMarkdown([], { exportedAt: AT })
  assert.match(md, /- 轮次：0 轮/)
  assert.ok(!md.includes('## 第 1 轮'))
})

test('引用摘要单行化并截断', () => {
  const messages: ChatMessage[] = [
    assistantMsg({
      question: 'q', answer: 'a', createdAt: AT,
      citations: [{ index: 1, evidence_id: 'e1', kind: 'raw_text', title: '史料',
                    snippet: `第一行\n第二行 ${'长'.repeat(200)}` }],
    }),
  ]
  const md = buildSessionMarkdown(messages, { exportedAt: AT })
  assert.match(md, /> 第一行 第二行 长+/)
  assert.ok(!md.includes('\n第二行'), '换行必须被压平，否则引用块会被打断')
  assert.ok(md.includes('…'), '超长摘要应截断')
})

test('文件名做安全化并带时间戳', () => {
  assert.equal(sanitizeFilename('赤壁/之战:上'), '赤壁-之战-上')
  assert.equal(sanitizeFilename('   '), '会话')
  assert.equal(formatStamp(AT), '2026-09-20 20:05')
  assert.equal(exportFilename('赤壁之战', AT), '赤壁之战-20260920-2005.md')
  assert.equal(exportFilename(undefined, AT), '中国历代战争史问答-20260920-2005.md')
})
