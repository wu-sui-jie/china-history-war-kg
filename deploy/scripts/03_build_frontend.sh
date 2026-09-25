#!/usr/bin/env bash
# 构建两个前端产物（在服务器上执行）
#
# 用法：sudo bash deploy/scripts/03_build_frontend.sh
#
# 两个产物的模式不能弄反（docs/集成与入口约定.md 第三节）：
#   旧前端      frontend/       npm run build            → base=/static/，由 nginx 提供
#   RAG 前端    RAG/frontend/   npm run build:integration → base=/rag/，由 RAG 服务同源托管
#
# 用了错模式的表现：页面白屏、Network 面板一堆 404。重建即可。

set -euo pipefail

APP_DIR=${APP_DIR:-/opt/china-war}
APP_USER=${APP_USER:-chinawar}

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 root 或 sudo 执行：sudo bash $0"
command -v node >/dev/null 2>&1 || die "未找到 node，请先执行 01_setup_server.sh"

as_app_user() {
    sudo -u "${APP_USER}" -H bash -lc "cd $1 && $2"
}

# ---------------------------------------------------------------- 旧前端
log "1/3 旧前端（frontend/ → dist，base=/static/）"
cd "${APP_DIR}/frontend"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/frontend"
# 用 pnpm + 入库的锁文件（第 14 轮审计 P2-21）：仓库入库的是 frontend/pnpm-lock.yaml，
# CI 也是 `pnpm install --frozen-lockfile`——原先这里用 `npm install`，会按 package.json
# 重新解析版本树、并生成一份没人看过也没入库的 package-lock.json。结果就是
# **服务器上构建出来的产物与 CI 验证过的那份不是同一个版本组合**，而且谁都没发现。
# corepack 随 Node 18+ 提供，用它激活 packageManager 字段里钉的 pnpm 版本。
if [[ -f pnpm-lock.yaml ]]; then
    as_app_user "${APP_DIR}/frontend" "corepack enable && corepack prepare --activate >/dev/null 2>&1 || true; pnpm install --frozen-lockfile"
    as_app_user "${APP_DIR}/frontend" "pnpm run build"
else
    echo "（未发现 pnpm-lock.yaml，退回 npm）"
    as_app_user "${APP_DIR}/frontend" "npm install --no-audit --no-fund"
    as_app_user "${APP_DIR}/frontend" "npm run build"
fi
[[ -f "${APP_DIR}/frontend/dist/index.html" ]] || die "旧前端构建失败：frontend/dist/index.html 不存在"
echo "产物：${APP_DIR}/frontend/dist"

# ---------------------------------------------------------------- RAG 前端
log "2/3 RAG 前端（RAG/frontend/ → dist，base=/rag/，接口前缀 /rag/api）"
cd "${APP_DIR}/RAG/frontend"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/RAG/frontend"
if [[ -f package-lock.json ]]; then
    as_app_user "${APP_DIR}/RAG/frontend" "npm ci --no-audit --no-fund"
else
    as_app_user "${APP_DIR}/RAG/frontend" "npm install --no-audit --no-fund"
fi
as_app_user "${APP_DIR}/RAG/frontend" "npm run build:integration"
[[ -f "${APP_DIR}/RAG/frontend/dist/index.html" ]] || die "RAG 前端构建失败"

# 校验产物确实是并入模式：引用必须是 /rag/assets/... 而不是 /assets/...
if grep -q 'src="/rag/assets/' "${APP_DIR}/RAG/frontend/dist/index.html"; then
    echo "产物前缀校验通过：/rag/assets/"
else
    die "RAG 前端产物不是并入模式（index.html 里没有 /rag/assets/ 前缀），检查是否误用了 npm run build"
fi

# ---------------------------------------------------------------- 出图依赖
log "3/3 飞书子图出图依赖（feishu-bot/render）"
if [[ -f "${APP_DIR}/feishu-bot/render/package.json" ]]; then
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/feishu-bot/render"
    as_app_user "${APP_DIR}/feishu-bot/render" "npm ci --no-audit --no-fund"
    echo "出图依赖已就绪（不需要飞书机器人可忽略）"
else
    echo "未找到 feishu-bot/render/package.json，跳过"
fi

log "前端构建完成"
