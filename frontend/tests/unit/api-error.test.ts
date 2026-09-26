/** 后端错误文案的提取。
 *
 * 后端的失败响应是「HTTP 4xx/5xx + body {code, msg}」，axios 走 reject，`error.message`
 * 只有 "Request failed with status code 403"——直接用会把后端写清楚的原因丢掉。
 */

import { describe, expect, test } from 'vitest'

import { apiErrorMessage } from '@/utils/apiError'

describe('apiErrorMessage', () => {
  test('优先取后端 msg（真实 wire 形态：error.response.data.msg）', () => {
    const error = {
      message: 'Request failed with status code 403',
      response: { status: 403, data: { code: 403, msg: '不能修改自己的角色，请让另一位管理员操作' } },
    }
    expect(apiErrorMessage(error, '保存失败')).toBe('不能修改自己的角色，请让另一位管理员操作')
  })

  test('后端没给 msg（或不是字符串）时回落到调用方的兜底文案', () => {
    expect(apiErrorMessage({ response: { status: 500, data: {} } }, '保存失败')).toBe('保存失败')
    expect(apiErrorMessage({ response: { status: 500, data: { msg: 123 } } }, '保存失败')).toBe('保存失败')
    expect(apiErrorMessage({ response: { status: 500, data: { msg: '   ' } } }, '保存失败')).toBe('保存失败')
  })

  test('网络错误/超时（没有 response）时不炸，也不显示 axios 文案', () => {
    expect(apiErrorMessage(new Error('Network Error'), '识别请求失败，请稍后重试'))
      .toBe('识别请求失败，请稍后重试')
    expect(apiErrorMessage(undefined, '兜底')).toBe('兜底')
    expect(apiErrorMessage(null, '兜底')).toBe('兜底')
  })

  test('msg 前后的空白被去掉', () => {
    expect(apiErrorMessage({ response: { data: { msg: '  仅管理员可执行该操作  ' } } }, '兜底'))
      .toBe('仅管理员可执行该操作')
  })
})
