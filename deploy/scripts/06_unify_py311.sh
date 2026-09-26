#!/usr/bin/env bash
# 服务器 Python 统一到 3.11（在服务器上执行，root）
#
#   bash /root/env-switch.sh
#
# 做什么：
#   1. 把 3.8 环境 china-war-backend 改名成 china-war-backend-py38（**不删**，留作回滚）
#   2. 新建 china-war-backend（python=3.11，与 china-war-rag 同版本）
#   3. 按仓库统一 lock（版本 + 哈希）装依赖
#   4. 装抽取链包 war_extraction（仓库内包，不在 lock 内）
#   5. 装完自检：解释器版本、关键库、后端包能否导入
#
# 为什么"改名"而不是"直接删"：
#   systemd 单元写的是 /opt/miniconda3/envs/china-war-backend/bin/python，改名后这个路径
#   正好空出来给新的 3.11 环境，单元文件一个字都不用改；而旧环境整目录还在，
#   回滚就是 `mv` 回来——连环境内部的绝对路径（pip 脚本的 shebang 等）都自动恢复正确。
#
# 回滚：
#   mv /opt/miniconda3/envs/china-war-backend /opt/miniconda3/envs/china-war-backend-py311
#   mv /opt/miniconda3/envs/china-war-backend-py38 /opt/miniconda3/envs/china-war-backend
#   systemctl restart china-war-backend china-war-outbox-retry.timer

set -uo pipefail

CONDA=/opt/miniconda3/bin/conda
ENVS=/opt/miniconda3/envs
LOCK=/opt/china-war/requirements.lock
APP_DIR=/opt/china-war

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 root 执行"
[[ -f "${LOCK}" ]] || die "找不到 ${LOCK}"

# ---------------------------------------------------------------- 0. 前置
log "0/5 前置检查"
[[ -x "${ENVS}/china-war-backend/bin/python" ]] || die "找不到 ${ENVS}/china-war-backend"
"${ENVS}/china-war-backend/bin/python" -V
if [[ -e "${ENVS}/china-war-backend-py38" ]]; then
    die "${ENVS}/china-war-backend-py38 已存在——上次切换没走完？先人工确认再重跑"
fi

# 记录切换前的状态（回滚时要对照）
systemctl is-active china-war-backend china-war-rag > /root/env-switch-before.txt 2>&1 || true

# ---------------------------------------------------------------- 1. 改名
log "1/5 把 3.8 环境改名保留（回滚点）"
mv "${ENVS}/china-war-backend" "${ENVS}/china-war-backend-py38"
echo "  ${ENVS}/china-war-backend → china-war-backend-py38"
"${ENVS}/china-war-backend-py38/bin/python" -V

# ---------------------------------------------------------------- 2. 新建 3.11
log "2/5 新建 3.11 环境（与 china-war-rag 同一主版本）"
"${CONDA}" create -y -n china-war-backend python=3.11 || die "conda create 失败"
PY="${ENVS}/china-war-backend/bin/python"
"${PY}" -V || die "新环境的 python 不可用"

# ---------------------------------------------------------------- 3. 依赖
log "3/5 按统一 lock 安装依赖（--require-hashes：版本与哈希双固定）"
# 不用 pipe 接 tail：pipefail 下管道退出码会骗人，这里直接落日志
if "${PY}" -m pip install --require-hashes -r "${LOCK}"; then
    echo "  LOCK_OK"
else
    echo "  LOCK_FAIL —— 回退到阿里云公网镜像重试一次"
    if "${PY}" -m pip install --require-hashes -r "${LOCK}" \
            -i https://mirrors.aliyun.com/pypi/simple/ \
            --trusted-host mirrors.aliyun.com; then
        echo "  LOCK_OK（公网镜像）"
    else
        die "lock 装不上：先看上面是哪个包/哪个哈希对不上（镜像缺版本时换镜像，不要去掉 --require-hashes）"
    fi
fi

# ---------------------------------------------------------------- 4. 抽取链包
log "4/5 安装抽取链包 war_extraction（仓库内包）"
# --no-deps：它的依赖已被 lock 覆盖，让 pip 再解析一次会装上与 lock 不同的版本
if "${PY}" -m pip install --no-deps -e "${APP_DIR}/entity-event-relation"; then
    echo "  WAR_EXTRACTION_OK"
else
    die "war_extraction 安装失败（后端 app.py 里的文本抽取接口会 ImportError）"
fi

# ---------------------------------------------------------------- 5. 自检
log "5/5 自检"
cd "${APP_DIR}/backend" || die "进不去 ${APP_DIR}/backend"

echo "--- 解释器 ---"
"${PY}" -c "import sys; print(sys.version)"

echo "--- 关键库版本 ---"
"${PY}" - <<'PYEOF'
import importlib
for name in ("flask", "flask_sqlalchemy", "sqlalchemy", "py2neo", "pandas", "numpy", "openai", "requests"):
    try:
        mod = importlib.import_module(name)
        print(f"  {name:18s} {getattr(mod, '__version__', '?')}")
    except Exception as exc:
        print(f"  {name:18s} 导入失败：{type(exc).__name__}: {exc}")
PYEOF

echo "--- 后端包导入 ---"
"${PY}" - <<'PYEOF'
import importlib, sys
for name in ("app", "db_utils", "logging_util", "api_errors", "login_guard",
             "password_policy", "blueprints.internal", "sync_compensation",
             "war_extraction", "entity_extract", "inference"):
    try:
        importlib.import_module(name)
        print(f"  OK   {name}")
    except Exception as exc:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
PYEOF

echo
echo "== 完成 $(date) =="
echo "下一步（人工确认自检全 OK 后再执行）："
echo "  systemctl restart china-war-backend china-war-outbox-retry.timer"
