import Http from '../http'

// 用户管理接口（仅管理员）。角色白名单与防呆都在服务端，这里只负责发请求。

export type UserRole = 'admin' | 'editor' | 'viewer'

export interface AdminUser {
  id: number
  account: string
  name: string
  role: UserRole
}

/** 用户列表（id / 账号 / 昵称 / 角色）。后端已过滤口令字段。 */
export const listUsers = () => Http.get('/api/admin/users')

/** 修改用户角色。服务端拒绝：改自己、非法角色值、用户不存在。 */
export const updateUserRole = (userId: number | string, role: UserRole) =>
  Http.post(`/api/admin/users/${userId}/role`, { role })
