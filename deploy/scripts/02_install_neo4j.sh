#!/usr/bin/env bash
# 安装 Neo4j 5.x（图数据库，后端图谱可视化与问答依赖）
#
# 用法（在服务器上，root）：sudo NEO4J_PASSWORD='你的口令' bash deploy/scripts/02_install_neo4j.sh
#   不传 NEO4J_PASSWORD 时会随机生成一个口令并打印出来，记得抄到 backend/.env
#
# 说明：
#   - Neo4j 5.x 需要 JDK 17，apt 会自动装
#   - 装好后默认只监听 localhost:7687，不对外暴露，符合本项目"两个后端只开回环"的约定
#   - 图数据不必从本机迁移：SQLite 是主存储，用 sync_sqlite_to_neo4j.py 全量重建即可（见第五节）

set -euo pipefail

APP_DIR=${APP_DIR:-/opt/china-war}
APP_USER=${APP_USER:-chinawar}

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 root 或 sudo 执行：sudo bash $0"

if command -v neo4j >/dev/null 2>&1; then
    log "Neo4j 已安装：$(neo4j version 2>/dev/null || echo '版本未知')，跳过安装"
else
    log "1/4 添加 Neo4j 官方 apt 源"
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y -qq curl gnupg lsb-release openjdk-17-jre-headless

    curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/neo4j.gpg
    echo "deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable 5" \
        > /etc/apt/sources.list.d/neo4j.list

    log "2/4 安装 Neo4j 5"
    apt-get update -qq
    apt-get install -y -qq neo4j
fi

log "3/4 设置初始口令"
NEO4J_PASSWORD=${NEO4J_PASSWORD:-$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 20)}
if systemctl is-active --quiet neo4j; then
    systemctl stop neo4j
fi
# 仅在数据库尚未初始化时有效；已初始化过会提示并跳过
if neo4j-admin dbms set-initial-password "${NEO4J_PASSWORD}" 2>/dev/null; then
    echo "初始口令已设置为：${NEO4J_PASSWORD}"
    echo "→ 请把它填进 ${APP_DIR}/backend/.env 的 NEO4J_PASSWORD"
else
    echo "数据库已初始化过，口令未改动（仍是之前设置的那个）"
    echo "→ 如果忘了口令：sudo neo4j-admin dbms set-initial-password '新口令' 需先清空 data/databases"
fi

log "4/4 启动并验证"
systemctl enable --now neo4j
sleep 8

# 内存建议
mem_mb=$(free -m | awk '/^Mem:/{print $2}')
if [[ "${mem_mb}" -lt 3800 ]]; then
    printf '\033[1;33m[提示] 本机内存 %s MB，Neo4j 默认堆可能偏大。\033[0m\n' "${mem_mb}"
    echo "  可在 /etc/neo4j/neo4j.conf 加：dbms.memory.heap.max_size=1g"
else
    echo "本机内存 ${mem_mb} MB，Neo4j 默认堆配置可用"
fi

if command -v cypher-shell >/dev/null 2>&1; then
    if cypher-shell -u neo4j -p "${NEO4J_PASSWORD}" "RETURN 1 AS ok" >/dev/null 2>&1; then
        echo "Neo4j 连接正常（bolt://localhost:7687）"
    else
        echo "cypher-shell 暂时连不上，稍等几秒再试：cypher-shell -u neo4j -p '口令' 'RETURN 1'"
    fi
fi

cat <<EOF

下一步：把口令填进 ${APP_DIR}/backend/.env 的 NEO4J_PASSWORD，然后重建图数据：

  cd ${APP_DIR}/backend
  sudo -u ${APP_USER} /opt/miniconda3/envs/china-war-backend/bin/python sync_sqlite_to_neo4j.py

（该脚本把 SQLite 全量同步到 Neo4j，所以图数据不需要从本机迁移）
EOF
