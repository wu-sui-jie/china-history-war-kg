#!/usr/bin/env bash
# 【在你自己电脑上执行】把代码与数据制品上传到服务器
#
# 用法（Windows 的 Git Bash / macOS / Linux 均可）：
#   bash deploy/scripts/04_upload_from_local.sh root@1.2.3.4
#   bash deploy/scripts/04_upload_from_local.sh root@1.2.3.4 --code-only    # 只传代码
#   bash deploy/scripts/04_upload_from_local.sh root@1.2.3.4 --data-only    # 只传数据
#   bash deploy/scripts/04_upload_from_local.sh root@1.2.3.4 --mirror       # 删除服务器上多余文件
#
# 为什么必须单独传数据：以下内容体积大或含敏感信息，都在 .gitignore 里，不在 git 仓库中
#   backend/database      SQLite 主库（约 13 MB）
#   backend/data/         raw 原始文本（受版权约束，按需）
#   RAG/data/             snapshot + index + eval（约 400 MB，RAG 问答的检索数据）
#
# 代码同步时排除：node_modules、.git、日志、缓存；保留 dist（本机构建好的前端产物）。
# 服务器上若没有 Node，直接用它就能跑；前端改了代码就重新构建（03_build_frontend.sh）。

set -euo pipefail

REMOTE=${1:-}
[[ -n "${REMOTE}" ]] || { echo "用法：bash $0 user@server [--code-only|--data-only|--mirror]"; exit 1; }
shift || true

MODE=all
MIRROR=0
for arg in "$@"; do
    case "${arg}" in
        --code-only) MODE=code ;;
        --data-only) MODE=data ;;
        --mirror)    MIRROR=1 ;;
        *) echo "未知参数：${arg}"; exit 1 ;;
    esac
done

LOCAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_DIR=${APP_DIR:-/opt/china-war}

# Windows 的 Git Bash 会把 /opt/... 当成本地路径改写，这里关掉转换
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) export MSYS_NO_PATHCONV=1 ;;
esac

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[提示] %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

EXCLUDES=(
    --exclude '.git/'
    --exclude 'node_modules/'
    --exclude '__pycache__/'
    --exclude '.pytest_cache/'
    --exclude '.ruff_cache/'
    --exclude '.mypy_cache/'
    --exclude '.idea/'
    --exclude '.vscode/'
    --exclude '.zcode/'
    --exclude '*.pyc'
    --exclude 'logs/'
    --exclude 'RAG/data/cache/'
    # 服务器上的私密配置与运行期数据：不要覆盖也不要删除
    --exclude '.env'
    --exclude 'feishu-bot/data/'
)

DATA_PATHS=(
    "backend/database"
    "backend/data"
    "RAG/data"
)

have_rsync=0
command -v rsync >/dev/null 2>&1 && have_rsync=1

# 用 rsync 同步一个子路径；没有 rsync 时退化为 tar over ssh
sync_path() {
    local rel="$1"
    [[ -e "${LOCAL_ROOT}/${rel}" ]] || { warn "本机不存在 ${rel}，跳过"; return 0; }
    if [[ "${have_rsync}" -eq 1 ]]; then
        local flags=(-az --info=progress2)
        if [[ "${MIRROR}" -eq 1 ]]; then flags+=(--delete); fi
        if [[ -d "${LOCAL_ROOT}/${rel}" ]]; then
            rsync "${flags[@]}" "${EXCLUDES[@]}" \
                "${LOCAL_ROOT}/${rel}/" "${REMOTE}:${APP_DIR}/${rel}/"
        else
            ssh "${REMOTE}" "mkdir -p '$(dirname "${APP_DIR}/${rel}")'"
            rsync "${flags[@]}" "${LOCAL_ROOT}/${rel}" "${REMOTE}:${APP_DIR}/${rel}"
        fi
    else
        echo "（本机没有 rsync，改用 tar over ssh）"
        ssh "${REMOTE}" "mkdir -p '${APP_DIR}/${rel}'"
        tar czf - "${EXCLUDES[@]}" -C "${LOCAL_ROOT}" "${rel}" \
            | ssh "${REMOTE}" "tar xzf - -C '${APP_DIR}'"
    fi
}

if [[ "${MODE}" == "all" || "${MODE}" == "code" ]]; then
    log "1/2 同步代码到 ${REMOTE}:${APP_DIR}"
    ssh "${REMOTE}" "mkdir -p '${APP_DIR}'"
    if [[ "${have_rsync}" -eq 1 ]]; then
        flags=(-az --info=progress2)
        if [[ "${MIRROR}" -eq 1 ]]; then flags+=(--delete); fi
        rsync "${flags[@]}" "${EXCLUDES[@]}" \
            --exclude 'backend/database' \
            --exclude 'backend/data/' \
            --exclude 'RAG/data/' \
            "${LOCAL_ROOT}/" "${REMOTE}:${APP_DIR}/"
    else
        # tar 回退：先整体打包，再由服务器解开；数据和私密文件在下面的步骤单独处理
        tar czf - "${EXCLUDES[@]}" \
            --exclude 'backend/database' --exclude 'backend/data' --exclude 'RAG/data' \
            -C "${LOCAL_ROOT}" . \
            | ssh "${REMOTE}" "tar xzf - -C '${APP_DIR}'"
    fi
fi

if [[ "${MODE}" == "all" || "${MODE}" == "data" ]]; then
    log "2/2 同步数据制品（体积较大，耐心等）"
    for p in "${DATA_PATHS[@]}"; do
        echo "--- ${p} ---"
        sync_path "${p}"
    done
fi

log "上传完成。接下来在服务器上执行："
cat <<EOF
  sudo bash ${APP_DIR}/deploy/scripts/01_setup_server.sh     # 首次：装环境与依赖
  sudo bash ${APP_DIR}/deploy/scripts/02_install_neo4j.sh    # 首次：装图数据库
  sudo bash ${APP_DIR}/deploy/scripts/03_build_frontend.sh   # 需要在服务器上重建前端时
  sudo bash ${APP_DIR}/deploy/scripts/install_services.sh    # 装 systemd 服务与 nginx
EOF
