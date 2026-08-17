import {Result} from "../types/result";
import {User} from "../types/user";

let user: User = {
    'userId': '1992',
    'username': 'admin',
}

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
                id: '/knowledge/text-extract',
                icon: 'layui-icon-read',
                title: '文本实体识别'
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
        ]
    }
]
const graph = {}

const getInfo = (req: any, res: any) => {
    let item = JSON.parse(req.body);
    let token = item ? item.token : null;
    let result: Result = {
        code: 200,
        msg: "操作成功",
        data: user,
        success: true
    }
    if (item || token) {
        result.code = 99998;
        result.msg = "请重新登录";
        result.success = false;
    }
    return result;
}

const getPermission = (req: any, res: any) => {
    let item = JSON.parse(req.body);
    let token = item ? item.token : null;
    let result: Result = {
        code: 200,
        msg: "操作成功",
        data: ['sys:user:add', 'sys:user:edit', 'sys:user:delete', 'sys:user:import', 'sys:user:export'],
        success: true
    }
    if (item || token) {
        result.code = 99998;
        result.msg = "请重新登录";
        result.success = false;
    }
    return result;
}

const getMenu = (req: any, res: any) => {
    let item = JSON.parse(req.body);
    let token = item ? item.token : null;
    let result: Result = {
        code: 200,
        msg: "操作成功",
        data: menus,
        success: true
    }
    if (item || token) {
        result.code = 99998;
        result.msg = "请重新登录";
        result.success = false;
    }
    return result;
}

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
