import {defineConfig} from "vite";
import vue from "@vitejs/plugin-vue";
import {resolve} from "path";

const excludeComponents = ['LightIcon', 'DarkIcon']

export default defineConfig({
    base: "/static/",
    resolve: {
        alias: [
            {
                find: '@',
                replacement: resolve(__dirname, './src')
            }
        ]
    },
    plugins: [
        vue(),
    ],
    server: {
        host: '0.0.0.0',
        port: 3001,
        // 端口固定为 3001：被占用时直接报错退出，而不是静默换端口
        // （hmr.clientPort 与文档都按 3001 写死，漂移会让代理假设失效）
        strictPort: true,
        cors: true,
        hmr: {
            clientPort: 3001
        },
        proxy: {
            '/api': {
                target: 'http://localhost:5000',
                changeOrigin: true,
                configure: (proxy) => {
                    proxy.on('proxyReq', (proxyReq, req) => {
                        // 为SSE请求禁用缓冲
                        if (req.url?.includes('/stream')) {
                            proxyReq.setHeader('X-Accel-Buffering', 'no');
                        }
                    });
                }
            },
            // 旧接口不带 /api 前缀（历史上直接挂在 Flask 根路径上，生产由 nginx 的 `location /` 兜底）。
            // 接口 baseURL 改成同源相对路径后，开发态必须显式转发这几条，
            // 否则请求会落到 Vite 自己身上、被 SPA fallback 返回 index.html，axios 解析 JSON 直接报错。
            // 新增这类旧路径接口时记得同步这里（或按 FE-6 统一改成 /api 前缀）。
            '^/(create_node|update_node|delete_node|search_name_kg|user/(menu|permission))$': {
                target: 'http://localhost:5000',
                changeOrigin: true,
            },
            // RAG 智能问答（并入模式）：/rag/* 整体转发给 RAG 服务（FastAPI :8000）。
            // 去掉前缀后正好对上 RAG 自己的路径约定——页面在 /*、接口在 /api/*，
            // 所以一条规则同时覆盖页面与接口。
            // 前置条件：RAG 前端必须以并入模式构建（npm run build:integration，
            // base=/rag/、VITE_API_BASE=/rag/api），否则它的请求会打到旧 Flask 的 /api 上。
            // 详见 docs/集成与入口约定.md。
            '/rag': {
                target: 'http://localhost:8000',
                changeOrigin: true,
                rewrite: (path) => path.replace(/^\/rag/, '') || '/',
                configure: (proxy) => {
                    proxy.on('proxyReq', (proxyReq, req) => {
                        // RAG 的问答是 SSE 流式响应，禁用中间层缓冲
                        if (req.url?.includes('/api/query')) {
                            proxyReq.setHeader('X-Accel-Buffering', 'no');
                        }
                    });
                }
            }
        }
    }
});