/// <reference types="vite/client" />

// 并入旧系统时由 .env.integration（或同名 shell 环境变量）注入；独立部署不设，
// 走 src/api/base.ts 与 vite.config.ts 里的默认值。
interface ImportMetaEnv {
  /** 前端基路径：独立部署 `/`，并入旧系统 `/rag/` */
  readonly VITE_BASE_PATH?: string
  /** 接口前缀：独立部署 `/api`，并入旧系统 `/rag/api` */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>
  export default component
}
