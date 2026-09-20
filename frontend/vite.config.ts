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
        strictPort: false,
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
            // RAG 智能问答（并入模式）：/rag/* 整体转发给 RAG 服务（FastAPI :8000）。
            // 去掉前缀后正好对上 RAG 自己的路径约定——页面在 /*、接口在 /api/*，
            // 所以一条规则同时覆盖页面与接口。
            // 前置条件：RAG 前端必须以并入模式构建（npm run build:integration，
            // base=/rag/、VITE_API_BASE=/rag/api），否则它的请求会打到旧 Flask 的 /api 上。
            // 详见 docs/RAG集成-Web入口合并.md。
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