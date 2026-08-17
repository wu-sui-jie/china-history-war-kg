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
            }
        }
    }
});