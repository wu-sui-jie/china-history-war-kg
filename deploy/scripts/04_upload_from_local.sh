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

# 排除项**一律不带尾斜杠**（第 14 轮审计 P1-1）。
#
# 为什么这件事必须写清楚：rsync 与 GNU tar 对尾斜杠的处理不同——
#   rsync：`--exclude 'node_modules/'` 正常（匹配目录）
#   tar：  `--exclude 'node_modules/'` **完全不生效**（tar 把它当成"名字以 / 结尾"）
# 实测（本机无 rsync，正好走 tar 分支）：
#
#   带尾斜杠  --exclude '.git/' --exclude 'node_modules/'
#     →  ./.git/ ./.git/config ./sub/node_modules/x.js 全都进了包
#   不带尾斜杠 --exclude '.git' --exclude 'node_modules'
#     →  ./ ./keep/ ./keep/a.txt ./sub/
#
# 后果不是"多传几个文件"：`.git`（约 108 MB，含历史里的明文口令）与两个
# `node_modules`（各约 300 MB）会被整包上传，而文件头写的意图正好相反。
# 回归用例见 backend/tests/test_upload_excludes.py（它直接从本数组里读模式喂给 tar）。
EXCLUDES=(
    --exclude '.git'
    --exclude 'node_modules'
    --exclude '__pycache__'
    --exclude '.pytest_cache'
    --exclude '.ruff_cache'
    --exclude '.mypy_cache'
    --exclude '.idea'
    --exclude '.vscode'
    --exclude '.zcode'
    --exclude '*.pyc'
    --exclude 'logs'
    --exclude 'RAG/data/cache'
    # 抽取链的切片缓存（197 个文件 / 22 MB）：`.gitignore` 里它一直是忽略项，这里漏了。
    # 上传会把整个目录搬到服务器覆盖同名文件——缓存是纯派生数据，服务器上那份还能用，
    # 没有理由每次都传一遍。
    --exclude 'entity-event-relation/cache'
    # 审计/验证过程留下的临时目录：可能带受限权限或独占锁，
    # 打包时会报 "Cannot open: Permission denied" 并因 set -o pipefail 中止整个上传
    --exclude '.audit-tmp'
    --exclude '.verify-tmp'
    # 服务器上的私密配置与运行期数据：不要覆盖也不要删除
    --exclude '.env'
    --exclude 'feishu-bot/data'
)

DATA_PATHS=(
    "backend/database"
    "backend/data"
    "RAG/data"
)

have_rsync=0
command -v rsync >/dev/null 2>&1 && have_rsync=1

# ---------------------------------------------------------------- 换行符防线
#
# `.gitattributes`（`* text=auto eol=lf`）保证正常检出是 LF。但如果本机曾经在
# `core.autocrlf=true` 下检出过工作区，文件在工作区里就是 CRLF，打包上传会**原样**
# 把它们带到 Linux——实测的后果见 `.gitattributes` 里的记录：`deploy/scripts/*.sh`
# 在服务器上语法错误、一行都跑不了，而 `install_services.sh` 依赖的鉴权门禁脚本
# 正是其中之一，于是"该拦的没拦"，却没有任何提示。
#
# 上传是最后一个能拦住它的地方，所以这里对**在 Linux 上必须为 LF** 的文件兜一次底。
# 自动转换而不是报错退出：这些文件带 CR 在任何场景下都是错的（不存在"我就想传
# CRLF 的 shell 脚本"这种合理需求），拦在这里让人手忙脚乱地改本机文件没有意义。
enforce_lf() {
    local offenders=() f rel
    while IFS= read -r -d '' f; do
        [[ -f "${f}" ]] || continue
        if LC_ALL=C grep -q $'\r' "${f}" 2>/dev/null; then
            sed -i 's/\r$//' "${f}"
            rel="${f#"${LOCAL_ROOT}/"}"
            offenders+=("${rel}")
        fi
    done < <(find "${LOCAL_ROOT}" \
                \( -name '*.sh' -o -name '*.service' -o -name '*.timer' \
                   -o -name '*.conf' -o -name '*.env.example' -o -name 'env.example' \) \
                -not -path '*/.git/*' -not -path '*/node_modules/*' \
                -not -path '*/RAG/data/*' -not -path '*/backend/data/*' \
                -print0 2>/dev/null || true)
    if (( ${#offenders[@]} > 0 )); then
        warn "以下文件带 CRLF，已就地转换为 LF（带 CR 的 shell/systemd 配置在 Linux 上无法执行）："
        printf '      %s\n' "${offenders[@]}"
    fi
}
enforce_lf

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
        # tar 只会"覆盖同名文件"，不会删除服务器上多出来的文件——`--mirror` 的语义
        # 在这一点上做不到。以前这里静默忽略该参数（用户以为同步是镜像式的，
        # 实际是叠加式的），现在直接拒绝：宁可让他装 rsync 或去掉参数。
        [[ "${MIRROR}" -eq 1 ]] && die "本机没有 rsync，tar 回退分支无法实现 --mirror（删除服务器上多余的文件）。请先安装 rsync，或去掉 --mirror 重跑。"
        echo "（本机没有 rsync，改用 tar over ssh）"
        # 与上面的 rsync 分支保持一致：单文件（如 backend/database）只需建它的父目录。
        # 直接 mkdir 目标路径时，若目标已存在且是文件，会以 "File exists" 失败并中止整个上传。
        ssh "${REMOTE}" "mkdir -p '$(dirname "${APP_DIR}/${rel}")'"
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
            --exclude 'backend/data' \
            --exclude 'RAG/data' \
            "${LOCAL_ROOT}/" "${REMOTE}:${APP_DIR}/"
    else
        [[ "${MIRROR}" -eq 1 ]] && die "本机没有 rsync，tar 回退分支无法实现 --mirror（删除服务器上多余的文件）。请先安装 rsync，或去掉 --mirror 重跑。"
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
