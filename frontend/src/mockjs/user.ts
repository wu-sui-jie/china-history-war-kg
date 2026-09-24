import {Result} from "../types/result";
import {User} from "../types/user";

let user: User = {
    'userId': '1992',
    'username': 'admin',
}

// 注意：这份 mock 只在开发态启用（见 src/main.ts 的 import.meta.env.DEV 守卫），
// mockjs 在 XHR 层拦下 `/user/menu`、`/user/permission`，**开发时侧边栏渲染的是这份数据**。
// 新增菜单项要改这里；另需同步 store/user.ts 的 id 白名单，否则会被过滤掉。
// 生产构建不打包 mockjs，菜单与权限来自 backend/app.py 的 get_menu()。
//
// 这里不做鉴权判定：token 放在请求头（见 api/http.ts），mock 的 req 只带 url/type/body，
// 取不到请求头。要验证真实登录态请用 VITE_ENABLE_MOCK=false 连后端。
const menus = [
    {
        id: '/workspace/dashboard',
        icon: 'layui-icon-home',
        title: '首页仪表盘'
    },
    {
        id: '/knowledge/timeline',
        icon: 'layui-icon-date',
        title: '历史时间轴'
    },
    {
        id: '/knowledge/map',
        icon: 'layui-icon-location',
        title: '历史地图视图'
    },
    {
        id: '/knowledge',
        icon: 'layui-icon-set',
        title: '知识图谱',
        children: [
            {
                id: '/knowledge/graph',
                icon: 'layui-icon-find-fill',
                title: '战争关系图'
            },
            {
                id: '/knowledge/graph/event',
                icon: 'layui-icon-flag',
                title: '历史战争'
            },
            {
                id: '/knowledge/graph/organization',
                icon: 'layui-icon-group',
                title: '参战势力'
            },
            {
                id: '/knowledge/graph/place',
                icon: 'layui-icon-location',
                title: '战争地点'
            },
            {
                id: '/knowledge/graph/person',
                icon: 'layui-icon-user',
                title: '历史人物'
            },
            {
                id: '/knowledge/inference',
                icon: 'layui-icon-engine',
                title: '历史问答助手'
            },
            {
                id: '/knowledge/rag',
                icon: 'layui-icon-chat',
                title: 'RAG 智能问答'
            },
            {
                id: '/knowledge/text-extract',
                icon: 'layui-icon-read',
                title: '文本实体识别'
            },
            {
                id: '/knowledge/relation-analysis',
                icon: 'layui-icon-tabs',
                title: '关系分析'
            }
        ]
    },
    {
        id: '/workspace/manage',
        icon: 'layui-icon-console',
        title: '数据运营',
        children: [
            {
                id: '/workspace/dataset',
                icon: 'layui-icon-template-1',
                title: '数据集中心'
            },
            {
                id: '/knowledge-list',
                icon: 'layui-icon-fonts-code',
                title: '数据维护'
            },
            {
                id: '/workspace/quality',
                icon: 'layui-icon-vercode',
                title: '图谱质检'
            },
            {
                id: '/workspace/dataset-versions',
                icon: 'layui-icon-date',
                title: '数据版本管理'
            },
        ]
    }
]
const graph = {}

const getInfo = (req: any, res: any) => {
    let result: Result = {
        code: 200,
        msg: "操作成功",
        data: user,
        success: true
    }
    return result;
}

const getPermission = (req: any, res: any) => {
    let result: Result = {
        code: 200,
        msg: "操作成功",
        data: ['sys:user:add', 'sys:user:edit', 'sys:user:delete', 'sys:user:import', 'sys:user:export'],
        success: true
    }
    return result;
}

const getMenu = (req: any, res: any) => {
    let result: Result = {
        code: 200,
        msg: "操作成功",
        data: menus,
        success: true
    }
    return result;
}

// 开发态专用的假登录（账号 admin / 123456）。注意前端登录实际调的是
// `/api/login`（真后端），本函数只在这个 mock 被单独使用时才生效。
const getLogin = (req: any, res: any) => {
    let item = JSON.parse(req.body);
    let account = item.account;
    let password = item.password;
    if (account === 'admin' && password === '123456') {
        return {
            'code': 200,
            'msg': '登陆成功',
            'data': {
                'userId': '35002',
                'token': 'eyJhbGciOiJIUzUxMiJ9.eyJ1c2VySWQiOiJhZG1pbiIsInVzZXJOYW1lIjoiYWRtaW4iLCJvcmdDb2RlIjoiMzUwMDAiLCJkZXB0Q29kZSI6IjM1MDAwIiwiYXVkIjoiYWRtaW4iLCJpc3MiOiJhZG1pbiIsImV4cCI6MTU5MzUzNTU5OH0.0pJAojRtT5lx6PS2gH_Q9BmBxeNlgBL37ABX22HyDlebbr66cCjVYZ0v0zbLO_9241FX9-FZpCkEqE98MQOyWw',
            }
        }
    } else {
        return {
            'code': 500,
            'msg': '登陆失败,账号密码不正确'
        }
    }
}

const getUpload = (req: any, res: any) => {
    return {
        'code': 200,
        'msg': '上传成功',
        'success': true
    }
}

const getGraph = (req: any, res: any) => {
    let result: Result = {
        code: 200,
        msg: "查询成功",
        data: graph,
        success: true
    }
    return result;
}

export default {
    getInfo, getMenu, getLogin, getPermission, getUpload, getGraph
}
