#!/usr/bin/env bash
# 服务器一次性初始化：系统包 → Node 20 → Miniconda → 两个 Python 环境 → 依赖
#
# 用法（在服务器上，用 root 或 sudo）：
#   cd /opt/china-war
#   sudo bash deploy/scripts/01_setup_server.sh
#
# 幂等：可以重复执行，已存在的环境/软件包会跳过。
#
# 目标系统：Ubuntu 22.04 / 24.04（其他 Debian 系大同小异）

set -euo pipefail

APP_DIR=${APP_DIR:-/opt/china-war}
CONDA_DIR=${CONDA_DIR:-/opt/miniconda3}
APP_USER=${APP_USER:-chinawar}
BACKEND_ENV=china-war-backend      # Python 3.11：旧后端（与 RAG 同主版本）
RAG_ENV=china-war-rag              # Python 3.11：RAG 服务 + 飞书机器人
NODE_MAJOR=20

# 国内服务器可换成镜像加速（可选）：
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
PIP_INDEX_URL=${PIP_INDEX_URL:-}

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[提示] %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 root 或 sudo 执行：sudo bash $0"

pip_install() {
    local python_bin="$1"; shift
    if [[ -n "${PIP_INDEX_URL}" ]]; then
        "${python_bin}" -m pip install -i "${PIP_INDEX_URL}" "$@"
    else
        "${python_bin}" -m pip install "$@"
    fi
}

# ---------------------------------------------------------------- 1. 系统包
log "1/6 安装系统基础软件包"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    curl wget git unzip ca-certificates gnupg lsb-release \
    build-essential python3-dev pkg-config \
    sqlite3 rsync

# ---------------------------------------------------------------- 2. Node 20
log "2/6 检查 Node.js（前端构建与飞书子图出图需要 ≥ 18）"
need_node=1
if command -v node >/dev/null 2>&1; then
    current_major=$(node -v | sed 's/^v\([0-9]*\).*/\1/')
    if [[ "${current_major}" -ge 18 ]]; then
        echo "已安装 Node $(node -v)，跳过"
        need_node=0
    fi
fi
if [[ "${need_node}" -eq 1 ]]; then
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
    apt-get install -y -qq nodejs
    echo "已安装 Node $(node -v) / npm $(npm -v)"
fi

# ---------------------------------------------------------------- 3. 运行用户与目录
log "3/6 创建运行用户 ${APP_USER} 与目录 ${APP_DIR}"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --shell /bin/bash "${APP_USER}"
    echo "已创建用户 ${APP_USER}"
else
    echo "用户 ${APP_USER} 已存在"
fi
mkdir -p "${APP_DIR}" /var/log/china-war
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" /var/log/china-war

# ---------------------------------------------------------------- 4. Miniconda
log "4/6 检查 Miniconda（四个模块统一 3.11，系统源版本不够新）"
if [[ ! -x "${CONDA_DIR}/bin/conda" ]]; then
    case "$(uname -m)" in
        x86_64)  CONDA_ARCH=x86_64 ;;
        aarch64) CONDA_ARCH=aarch64 ;;
        *) die "不支持的 CPU 架构：$(uname -m)" ;;
    esac
    installer=/tmp/miniconda.sh
    curl -fsSL -o "${installer}" \
        "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-${CONDA_ARCH}.sh"
    bash "${installer}" -b -p "${CONDA_DIR}"
    rm -f "${installer}"
    echo "已安装 Miniconda 到 ${CONDA_DIR}"
else
    echo "Miniconda 已存在：$(${CONDA_DIR}/bin/conda --version)"
fi
"${CONDA_DIR}/bin/conda" config --set always_yes true
"${CONDA_DIR}/bin/conda" config --set auto_activate_base false

# conda 环境的目录权限交给运行用户（应用需要写 logs/）
chown -R "${APP_USER}:${APP_USER}" "${CONDA_DIR}/envs" 2>/dev/null || true

# ---------------------------------------------------------------- 5. Python 环境
log "5/6 创建两个 Python 环境"
# **两个环境同为 Python 3.11**：chromadb 要求 ≥3.10，而旧后端那套 (Flask + py2neo)
# 也已在 3.11 上验证通过（四套测试与线上接口全过），版本不再是分成两个环境的理由。
# 仍然分两个环境是**隔离选择**（按服务拆环境/容器，其中一个的依赖升级不会波及另一个），
# 不是版本要求——本地开发共用一个 china-war-py311 即可（见根 README）。
conda_create() {
    local env_name="$1" py_ver="$2"
    if "${CONDA_DIR}/bin/conda" env list | awk '{print $1}' | grep -qx "${env_name}"; then
        echo "环境 ${env_name} 已存在，跳过"
    else
        "${CONDA_DIR}/bin/conda" create -y -n "${env_name}" "python=${py_ver}"
    fi
}
conda_create "${BACKEND_ENV}" "3.11"
conda_create "${RAG_ENV}" "3.11"

BACKEND_PY="${CONDA_DIR}/envs/${BACKEND_ENV}/bin/python"
RAG_PY="${CONDA_DIR}/envs/${RAG_ENV}/bin/python"

# ---------------------------------------------------------------- 6. 依赖
log "6/6 安装 Python 依赖"
[[ -f "${APP_DIR}/requirements.txt" ]] \
    || die "找不到 ${APP_DIR}/requirements.txt —— 请先用 04_upload_from_local.sh 把代码传上来"
[[ -f "${APP_DIR}/RAG/requirements.txt" ]] \
    || die "找不到 ${APP_DIR}/RAG/requirements.txt —— 请先上传代码"
pip_install "${BACKEND_PY}" --upgrade pip
pip_install "${RAG_PY}" --upgrade pip

echo "--- 旧后端（Python 3.11）---"
# 依赖来源分两档，**默认走 requirements.txt**：
#
#   1. requirements.txt（backend/ + 仓库根）—— 默认。与既有部署一致，任何平台都能装。
#   2. requirements.lock（仓库根，119 包带 sha256）—— 显式 `USE_UNIFIED_LOCK=1` 才用。
#
# 为什么锁不是默认（2026-09-26 真机实测）：**这份锁在 Linux 上装不上**。它是在
# Windows 开发机上生成的，`uvicorn[standard]` 经 `--strip-extras` 剥掉了 extras，
# 而 Linux 专有的 uvloop 在 Windows 的 pip freeze 里根本不存在，于是
# `--require-hashes` 直接报：
#
#     ERROR: In --require-hashes mode, all requirements must have their versions
#     pinned with ==. These do not: uvloop>=0.15.1
#     (from uvicorn[standard]>=0.18.3 -> chromadb==1.5.9 -> -r requirements.lock)
#
# 本机（Windows）验证"锁可重装"时看不出来——恰好因为 Windows 不需要 uvloop。
# 修好之前默认不用它，避免每台新服务器都白等几分钟下载后再失败。
# 待办：生成器要按平台补全（或 CI 在 Linux 上真装一遍），见 docs/项目审查与修复历史.md 第 17 节。
#
# 注意锁的覆盖面（等它可用时）：它由 `[all]` 生成，因此 **RAG / 飞书侧的依赖**
# （chromadb、lark 等）也会进这个环境，多占约 200 MB。那是有意的取舍。
if [[ "${USE_UNIFIED_LOCK:-0}" == "1" ]] && [[ -f "${APP_DIR}/requirements.lock" ]] \
   && pip_install "${BACKEND_PY}" --require-hashes -r "${APP_DIR}/requirements.lock"; then
    echo "旧后端依赖已按 requirements.lock 安装（含哈希校验）"
else
    [[ "${USE_UNIFIED_LOCK:-0}" == "1" ]] \
        && warn "锁文件不可用或安装失败，退回 requirements.txt（版本可能与验证环境不同）"
    if [[ -f "${APP_DIR}/backend/requirements.txt" ]]; then
        pip_install "${BACKEND_PY}" -r "${APP_DIR}/backend/requirements.txt"
    fi
    pip_install "${BACKEND_PY}" -r "${APP_DIR}/requirements.txt"
fi

# backend 通过正式包 war_extraction 引用抽取链（同级目录 entity-event-relation）。
# 路径依赖不能写进 requirements.txt（换工作目录就失效），所以按 backend/requirements.txt
# 里写明的顺序单独做这一步——漏了它，旧后端启动会报 ModuleNotFoundError: war_extraction。
echo "--- 抽取链包 war_extraction（entity-event-relation）---"
if [[ -f "${APP_DIR}/entity-event-relation/pyproject.toml" ]]; then
    pip_install "${BACKEND_PY}" -e "${APP_DIR}/entity-event-relation"
else
    warn "未找到 ${APP_DIR}/entity-event-relation/pyproject.toml，跳过；旧后端会启动失败"
fi

echo "--- RAG（Python 3.11）---"
# 优先用带哈希校验的锁文件；若因平台差异装不上，退回未锁版本的 requirements.txt
if [[ -f "${APP_DIR}/RAG/requirements.lock" ]] \
   && pip_install "${RAG_PY}" --require-hashes -r "${APP_DIR}/RAG/requirements.lock"; then
    echo "RAG 依赖已按 requirements.lock 安装（含哈希校验）"
else
    warn "锁文件安装失败，退回 requirements.txt（版本可能与开发机不同）"
    pip_install "${RAG_PY}" -r "${APP_DIR}/RAG/requirements.txt"
fi

echo "--- 飞书机器人（与 RAG 共用 Python 3.11 环境）---"
if [[ -f "${APP_DIR}/feishu-bot/requirements.txt" ]]; then
    pip_install "${RAG_PY}" -r "${APP_DIR}/feishu-bot/requirements.txt"
else
    warn "未找到 feishu-bot/requirements.txt，跳过（不用飞书机器人可忽略）"
fi

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

log "完成。下一步："
cat <<EOF
  1) 安装图数据库：      sudo bash ${APP_DIR}/deploy/scripts/02_install_neo4j.sh
  2) 上传代码与数据制品：在**本机**执行 deploy/scripts/04_upload_from_local.sh
  3) 构建前端：          sudo bash ${APP_DIR}/deploy/scripts/03_build_frontend.sh
  4) 填写配置：          ${APP_DIR}/backend/.env、RAG/.env、feishu-bot/.env、/etc/china-war/rag-secrets.env
  5) 装服务：            sudo bash ${APP_DIR}/deploy/scripts/install_services.sh
  6) 自检：              bash ${APP_DIR}/deploy/scripts/selfcheck.sh
EOF
