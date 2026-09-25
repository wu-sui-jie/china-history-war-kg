import Http from '../http'

// 用户管理接口（仅管理员）。角色白名单与防呆都在服务端，这里只负责发请求。

export type UserRole = 'admin' | 'editor' | 'viewer'

export interface AdminUser {
  id: number
  account: string
  name: string
  role: UserRole
  /**
   * 账号是否已停用（第 14 轮审计 P2-18）。
   *
   * 后端早就返回这个字段（`UserInfo.to_dict()`），停用接口也早就有了
   * （`POST /api/admin/users/<id>/status`）——但类型里没有它，页面也就没有状态列，
   * 于是**已停用账号与正常账号在列表里长得一样**：管理员停用了某人，回头看列表
   * 却分不出来，只能靠"他还登录得了吗"去猜。
   */
  disabled: boolean
}

/** 用户列表（id / 账号 / 昵称 / 角色）。后端已过滤口令字段。 */
export const listUsers = () => Http.get('/api/admin/users')

/** 修改用户角色。服务端拒绝：改自己、非法角色值、用户不存在。 */
export const updateUserRole = (userId: number | string, role: UserRole) =>
  Http.post(`/api/admin/users/${userId}/role`, { role })

/**
 * 停用 / 启用账号。服务端拒绝：停用自己、用户不存在。
 *
 * 停用会同时把 `token_version + 1`（见 backend/db_utils.set_user_disabled），
 * 所以该账号在其它设备上的在途 token 会立刻失效——RAG 侧则要等撤销查询的 TTL
 * （见 RAG/docs/deploy.md 的"身份校验与凭证撤销"一节）。
 */
export const setUserDisabled = (userId: number | string, disabled: boolean) =>
  Http.post(`/api/admin/users/${userId}/status`, { disabled })
