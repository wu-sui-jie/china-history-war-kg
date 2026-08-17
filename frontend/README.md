# 中国历史战争知识图谱 - 前端

基于 `Vue3`、`TypeScript`、`Layui Vue` 搭建的中国历史战争事件知识图谱可视化系统。

## 技术栈

- **Vue 3**: 用于构建用户界面的渐进式JavaScript框架
- **TypeScript**: 提供类型安全的JavaScript超集
- **Layui Vue**: UI组件库，提供丰富的界面组件
- **Vue Router**: Vue.js官方的路由管理器
- **Pinia**: 状态管理库
- **Vite**: 前端构建工具
- **Axios**: HTTP请求库
- **ECharts**: 知识图谱可视化
- **RelationGraph**: 关系图谱组件
- **Markdown-it**: Markdown渲染
- **highlight.js**: 代码高亮

## 一、安装依赖

### 1.1 切换华为镜像源（推荐）
```bash
npm config set registry https://mirrors.huaweicloud.com/repository/npm/
```

### 1.2 安装pnpm
```bash
npm install -g pnpm
```

### 1.3 安装项目依赖
```bash
# 在frontend目录下执行
pnpm install
```

## 二、启动项目

```bash
# 开发模式启动
pnpm dev

# 或使用npm
npm run dev
```

访问地址: http://localhost:5173

## 三、打包项目

```bash
# 生产环境打包
pnpm build

# 或使用npm
npm run build
```

打包后的文件位于 `dist/` 目录。

## 四、项目结构

```
frontend/
│── node_modules/           # 项目依赖（自动生成）
|── public/                 # 静态资源
|   └── images/             # 图片资源
|── src/                    # 源代码主目录
│   ├── api/                # API 接口封装
│   │   └── http.ts         # Axios配置和请求方法
│   ├── assets/             # 资产文件（图片、样式等）
│   │   └── login/          # 登录页面资源
│   ├── components/         # 通用Vue组件
│   ├── config/             # 应用配置文件
│   ├── directives/         # 自定义指令
│   ├── lang/               # 国际化语言文件
│   ├── layouts/            # 布局组件
│   │   ├── BasicLayout.vue # 基础布局
│   │   ├── BlankLayout.vue # 空白布局
│   │   └── global/         # 全局布局组件
│   ├── library/            # 业务组件库（二次封装）
│   ├── mockjs/             # Mock 数据模拟
│   ├── router/             # Vue Router 路由配置
│   │   ├── index.ts        # 路由入口
│   │   └── module/         # 路由模块
│   ├── store/              # 状态管理（Pinia）
│   ├── styles/             # 全局样式（CSS/SCSS）
│   ├── types/              # TypeScript 类型定义
│   ├── utils/              # 通用工具函数
│   ├── views/              # 页面视图组件
│   │   ├── component/      # 通用组件页面
│   │   ├── error/          # 错误页面（401/403/404/500）
│   │   ├── inference/      # 智能问答页面
│   │   │   ├── index.vue   # 问答主页面
│   │   │   └── components/ # 问答相关组件
│   │   ├── knowledge/      # 知识图谱页面
│   │   │   └── graph/      # 图谱可视化
│   │   │       ├── OverviewGraph.vue      # 总览图谱
│   │   │       ├── event/                 # 关联战争子页面
│   │   │       ├── organization/          # 参战势力子页面
│   │   │       ├── person/                # 相关人物子页面
│   │   │       └── place/                 # 发生地点子页面
│   │   ├── knowledge-list/ # 节点关系管理
│   │   │   ├── NodeLayout.vue
│   │   │   ├── event/      # 战争事件管理
│   │   │   ├── organization/ # 组织管理
│   │   │   ├── person/     # 人物管理
│   │   │   └── place/      # 地点管理
│   │   └── login/          # 登录页面
│   ├── App.vue             # 根组件
│   ├── main.ts             # 应用入口文件
│   └── shims-vue.d.ts      # Vue 模块类型声明
│── .gitignore              # Git 忽略配置
│── .npmrc                  # npm 配置文件
│── index.html              # HTML 模板
│── package.json            # 项目依赖和脚本
│── pnpm-lock.yaml          # pnpm 依赖锁定文件
│── README.md               # 项目说明文档
│── tsconfig.json           # TypeScript 编译配置
└── vite.config.ts          # Vite 构建配置文件
```

## 五、功能模块

### 1. 战争关系图
知识图谱可视化展示，包含以下子页面：

| 子页面 | 展示内容 | 关系筛选选项 |
|--------|----------|--------------|
| **关联战争** | 事件与事件之间的关系 | 因果关系、顺承关系、并列关系、包含关系、条件关系 |
| **参战势力** | 组织与事件之间的关系 | 发起方、防守方、支援方、同盟方、投降方、被俘方、议和方、调停方 |
| **相关人物** | 人物与事件之间的关系 | 统帅、将领、谋士、使者、君主、参与者、俘虏、阵亡、投降、叛变、可汗 |
| **发生地点** | 地点与事件之间的关系 | 主战场、次要战场、出发地、目的地、途经地、驻防地、指挥所、补给地、战略要地、议和地点 |

### 2. 节点关系管理
节点数据的增删改查管理，包含四个子页面：
- **战争事件**: 管理战争事件实体（名称、朝代、时间、地点等）
- **参战组织**: 管理势力组织实体（名称、朝代、组织类型等）
- **相关人物**: 管理历史人物实体（名称、朝代、所属势力、角色等）
- **发生地点**: 管理战争地点实体（名称、朝代、现代名称、所属省市等）

**数据操作说明**:
- 新增/修改/删除节点：通过API操作后端SQLite数据库
- 数据同步：后端自动同步SQLite变更到Neo4j
- 支持完整的CRUD操作和属性编辑

### 3. 智能问答
基于大模型的历史战争知识问答系统：
- 自然语言提问（支持中文）
- 实体识别与关系抽取
- 知识图谱数据可视化展示
- 思考过程展示
- 知识图谱参考信息查看
- 多轮对话历史管理
- 支持导出对话到Markdown

**问答功能特点**:
- 基于Ollama+deepseek-r1:7b大模型
- 规则引擎辅助推理
- 实体相关性过滤
- 美观的知识图谱展示

## 六、开发规范

### 6.1 代码风格
- 使用TypeScript进行类型定义
- 组件使用Vue3 Composition API
- 使用`setup`语法糖
- 样式使用`scoped`属性防止污染

### 6.2 目录规范
- 页面组件放在 `views/` 目录
- 通用组件放在 `components/` 目录
- API接口放在 `api/` 目录
- 类型定义放在 `types/` 目录

### 6.3 命名规范
- 组件名使用大驼峰（PascalCase）
- 文件名使用大驼峰（PascalCase）
- 变量名使用小驼峰（camelCase）
- 常量使用全大写+下划线（UPPER_SNAKE_CASE）

## 七、后端API配置

后端服务地址在 `src/api/http.ts` 中配置：

```typescript
// 开发环境
const baseURL = 'http://localhost:5000'

// 生产环境
const baseURL = 'http://your-production-server:5000'
```

## 八、注意事项

1. **跨域问题**: 开发时后端已配置CORS，如需修改请检查后端`app.py`
2. **字体图标**: 使用Layui的图标字体，确保网络正常加载
3. **浏览器兼容**: 支持Chrome、Firefox、Edge等现代浏览器
4. **响应式**: 部分页面支持移动端适配，但推荐在桌面端使用

## 九、常见问题

### Q: 启动报错找不到模块？
A: 确保已执行 `pnpm install` 安装所有依赖

### Q: 接口请求失败？
A: 检查后端服务是否已启动，并确认 `api/http.ts` 中的`baseURL`配置正确

### Q: 智能问答无响应？
A: 确保Ollama服务已启动，且已下载deepseek-r1:7b模型

### Q: 打包后资源路径错误？
A: 检查 `vite.config.ts` 中的 `base` 配置，默认为 `/`
