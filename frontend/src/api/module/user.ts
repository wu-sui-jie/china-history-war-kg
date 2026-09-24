import Http from '../http';

// 用户与登录接口（FE-6 统一入口）。

export const menu = function() {
    return Http.get('/user/menu')
}

export const permission = function() {
    return Http.get('/user/permission')
}

// 当前登录账号信息（account/name/role），供界面显示角色徽标
export const userInfo = function() {
    return Http.get('/api/userinfo')
}

export interface LoginPayload {
    account: string
    password: string
}

export const login = (payload: LoginPayload) => Http.post('/api/login', payload)

export const signIn = (payload: LoginPayload) => Http.post('/api/sign_in', payload)
