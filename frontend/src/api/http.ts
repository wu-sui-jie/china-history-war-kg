import axios, {AxiosRequestHeaders, AxiosResponse, InternalAxiosRequestConfig} from 'axios';
import {useUserStore} from "../store/user";
import router from '../router'
import config from '@/config'

type TAxiosOption = {
    timeout: number;
    baseURL: string;
}

const axiosConfig: TAxiosOption = {
    timeout: config.timeout,
    baseURL: config.baseURL
}

// 未登录/登录过期时的统一处理：清掉本地凭据并回到登录页。
// 加锁避免并发请求同时触发多次跳转。
let redirectingToLogin = false;
function handleUnauthorized() {
    const userInfoStore = useUserStore();
    userInfoStore.clearSession();
    if (redirectingToLogin) return;
    if (router.currentRoute.value.path === '/login') return;
    redirectingToLogin = true;
    router.push('/login').finally(() => {
        redirectingToLogin = false;
    });
}

class Http {
    service;

    constructor(config: TAxiosOption) {
        this.service = axios.create(config)

        /* 请求拦截 */
        this.service.interceptors.request.use((config: InternalAxiosRequestConfig) => {
            const userInfoStore = useUserStore();
            if (userInfoStore.token) {
                (config.headers as AxiosRequestHeaders).token = userInfoStore.token
            }
            return config
        }, error => {
            return Promise.reject(error);
        })

        /* 响应拦截 */
        this.service.interceptors.response.use((response: AxiosResponse<any>) => {
            const data = response.data;
            // 后端未登录/登录过期：HTTP 401（也兼容响应体里的 code 401）
            if (response.status === 401 || data?.code === 401) {
                handleUnauthorized();
                return Promise.reject(new Error(data?.msg || '登录已过期，请重新登录'));
            }
            // 统一返回数据，不执行其他操作
            return data;
        }, error => {
            if (error?.response?.status === 401) {
                handleUnauthorized();
            }
            console.error('API请求错误:', error);
            return Promise.reject(error)
        })
    }

    /* GET 方法 */
    get<T>(url: string, params?: object, _object = {}): Promise<any> {
        return this.service.get(url, {params, ..._object})
    }

    /* POST 方法 */
    post<T>(url: string, params?: object, _object = {}): Promise<any> {
        return this.service.post(url, params, _object)
    }

    /* PUT 方法 */
    put<T>(url: string, params?: object, _object = {}): Promise<any> {
        return this.service.put(url, params, _object)
    }

    /* DELETE 方法 */
    delete<T>(url: string, params?: any, _object = {}): Promise<any> {
        return this.service.delete(url, {params, ..._object})
    }
}

export default new Http(axiosConfig)
