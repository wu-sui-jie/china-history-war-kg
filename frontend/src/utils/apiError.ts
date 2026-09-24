/** 从 axios 失败响应里取后端给的文案。
 *
 * 后端的失败响应是「HTTP 4xx/5xx + body `{code, msg}`」，axios 会走 reject 分支，
 * 此时 `error.message` 只有 "Request failed with status code 403" 这种状态码描述——
 * 服务端真正写清楚的原因（"不能修改自己的角色，请让另一位管理员操作"）在
 * `error.response.data.msg` 里。直接用 error.message 等于把后端文案丢掉，
 * 界面上永远看不到"为什么被拒"。
 *
 * 取不到（网络错误、超时、后端没给 msg）时回落到调用方给的兜底文案。
 */
export function apiErrorMessage(error: unknown, fallback: string): string {
    const data = (error as { response?: { data?: { msg?: unknown } } })?.response?.data;
    const msg = data && typeof data.msg === 'string' ? data.msg.trim() : '';
    return msg || fallback;
}
