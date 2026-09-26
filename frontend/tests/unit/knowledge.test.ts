/** 展示层工具函数的取值规则。
 *
 * 重点钉住两件事：
 *  1) 四种标准类型的各种写法仍能归一（这是接口、图表、详情页共用的入口）；
 *  2) 乱码不被兼容——乱码 key 映射该由后端在写库前复原，前端只做透传。
 *     这里显式断言乱码会原样透传：哪天有人又想在前端加回乱码 key，这条会提醒他
 *     去修数据源头。
 */

import { describe, expect, test } from 'vitest'

import { fieldLabel, normalizeType, problemLabel, typeLabel } from '@/utils/knowledge'

describe('normalizeType / typeLabel', () => {
  test('标准类型与常见写法都归一', () => {
    expect(normalizeType('Event')).toBe('Event')
    expect(normalizeType('event')).toBe('Event')
    expect(normalizeType('事件')).toBe('Event')
    expect(normalizeType('战争事件')).toBe('Event')
    expect(normalizeType(' 战争地点 ')).toBe('Place')
    expect(normalizeType('历史人物')).toBe('Person')
    expect(normalizeType('参战组织')).toBe('Organization')
    expect(normalizeType('势力组织')).toBe('Organization')
  })

  test('未知值与空值原样返回，不猜', () => {
    expect(normalizeType('Custom')).toBe('Custom')
    expect(normalizeType('')).toBe('')
    expect(normalizeType(undefined)).toBe('')
    expect(normalizeType(null as unknown as string)).toBe('')
  })

  test('乱码不再兼容（已在数据源头复原）', () => {
    // 鎴樹簤浜嬩欢 = "战争事件" 的 UTF-8 字节被按 GBK 读出的乱码
    expect(normalizeType('鎴樹簤浜嬩欢')).toBe('鎴樹簤浜嬩欢')
    expect(normalizeType('鎴樹簤鍦扮偣')).toBe('鎴樹簤鍦扮偣')
  })

  test('typeLabel 对已知类型给中文名，未知类型回落原值', () => {
    expect(typeLabel('Event')).toBe('战争事件')
    expect(typeLabel('Organization')).toBe('参战组织')
    expect(typeLabel('Custom')).toBe('Custom')
    expect(typeLabel()).toBe('-')
  })
})

describe('fieldLabel / problemLabel', () => {
  test('已知字段给中文名，未知字段有兜底', () => {
    expect(fieldLabel('EventName')).toBe('事件名称')
    expect(fieldLabel('not_a_field')).toBe('未命名属性')
    expect(fieldLabel('')).toBe('未命名属性')
  })

  test('质量问题码给中文名，未知码回落原值', () => {
    expect(problemLabel('missing_start_date')).toBe('缺少开始时间')
    expect(problemLabel('whatever')).toBe('whatever')
    expect(problemLabel()).toBe('-')
  })
})
