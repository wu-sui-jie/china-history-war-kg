#!/usr/bin/env bash
# 备份脚本（在服务器上执行，root）
#
# 用法：
#   sudo bash deploy/scripts/05_backup.sh                 # 全量：配置 + 密钥 + SQLite + 环境清单
#   sudo bash deploy/scripts/05_backup.sh --db-only       # 只备份数据库（体量小，适合跑得很勤）
#
# 不备份代码：代码在 git 里，服务器上应当是"只部署不改"。若有人直接在服务器上改了
# 文件，那是版本控制之外的东西，本脚本不负责——要么改回仓库，要么另做代码快照。
#
# ## 为什么需要它
#
# `backend/database` 是 SQLite 单文件主库：账号、密码哈希、你人工改过的全部节点与关系、
# 以及 Neo4j 写失败后的补偿队列都在里面。它是**不可再生**的那一份——磁盘损坏、误删、
# 或一次写坏的迁移都会让数据永久消失，而 Neo4j 只是它的派生副本（丢了能从主库重建，
# 反过来不行）。
#
# ## 备份什么、放哪里
#
#   /var/backups/china-war/<时间戳>/     ← 放在应用目录之外：
#                                          代码上传是叠加式的，放里面可能被覆盖
#     backend.env / rag.env / feishu-bot.env   ← 600，含 JWT 密钥与 Neo4j 口令
#     rag-secrets.env                          ← /etc/china-war/ 下的密钥文件
#     database.sqlite                          ← 在线备份（sqlite3 的 .backup，带 WAL 安全）
#     systemd-*.service / nginx-china-war.conf ← 部署形态（改坏了能对照）
#     packages-*.txt                           ← 两个 conda 环境的包清单（能重建环境）
#     manifest.txt                             ← 每份文件的大小与 sha256（恢复时校验）
#
# 保留策略：**最近 7 天的每日备份 + 最近 4 个每周备份**（每周日的那份不删）。
#
# ## 恢复（演练步骤，务必真跑过一次）
#
#   1. 停服务：   systemctl stop china-war-backend china-war-outbox-retry.timer
#   2. 换数据库： cp /var/backups/china-war/<ts>/database.sqlite /opt/china-war/backend/database
#                 chown chinawar:chinawar /opt/china-war/backend/database
#   3. 起服务：   systemctl start china-war-backend
#   4. 对账：     bash deploy/scripts/selfcheck.sh
#                 并跑一次全量同步让 Neo4j 追平（见 deploy/README 的"重建图谱"）
#   配置类文件同理直接覆盖回去，注意保持 600 与 chinawar 属主。

set -euo pipefail

APP_DIR=${APP_DIR:-/opt/china-war}
CONDA_DIR=${CONDA_DIR:-/opt/miniconda3}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/china-war}
KEEP_DAILY=${KEEP_DAILY:-7}
KEEP_WEEKLY=${KEEP_WEEKLY:-4}
DB_PATH=${DB_PATH:-${APP_DIR}/backend/database}

MODE=full
[[ "${1:-}" == "--db-only" ]] && MODE=db

TS=$(date +%Y%m%d-%H%M%S)
DEST="${BACKUP_ROOT}/${TS}"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[提示] %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 root 或 sudo 执行：sudo bash $0"
[[ -f "${DB_PATH}" ]] || die "找不到 ${DB_PATH}"

mkdir -p "${DEST}"
chmod 700 "${BACKUP_ROOT}" "${DEST}"

# ---------------------------------------------------------------- 数据库
log "1/3 备份 SQLite 主库（在线备份，服务不必停）"

# 用 Python 的 sqlite3 backup API 而不是 cp：数据库开着 WAL 时直接复制文件会漏掉
# -wal 里尚未合并的事务（拿到的是一份"看起来正常、实际少了最近若干次写入"的旧库，
# 这种备份最危险——真出事时才发现少数据）。backup API 会取一致的快照。
# 选解释器：优先 RAG 环境（3.11），退回系统 python3。两者都自带 sqlite3。
PY_BIN=""
for candidate in "${CONDA_DIR}/envs/china-war-backend/bin/python" \
                 "${CONDA_DIR}/envs/china-war-rag/bin/python" \
                 /usr/bin/python3; do
    [[ -x "${candidate}" ]] && PY_BIN="${candidate}" && break
done
[[ -n "${PY_BIN}" ]] || die "找不到可用的 python 解释器"

"${PY_BIN}" - "${DB_PATH}" "${DEST}/database.sqlite" <<'PY'
import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)          # 一致快照，含 WAL 中未合并的事务
dst.execute("PRAGMA integrity_check").fetchone()
# 源库是 WAL 模式，快照会继承该模式并被后续只读打开时留下 -wal/-shm 空文件；
# 备份是"归档件"，转成 DELETE 模式让它永远只是一个文件，不会有一半事务在旁挂文件里。
dst.execute("PRAGMA journal_mode=DELETE")
dst.close(); src.close()
print("  数据库快照完成")
PY

chmod 600 "${DEST}/database.sqlite"

# 校验：备份出来的库能打开、表齐全、每张表行数与原库一致
# （表名取自实际库结构：UserInfo / places / persons / organizations / events /
#   event_*_relations / neo4j_sync_jobs / login_attempts / app_meta）
"${PY_BIN}" - "${DB_PATH}" "${DEST}/database.sqlite" <<'PY'
import sqlite3, sys
def stats(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        return set(tables), counts
    finally:
        con.close()
src_tables, src_counts = stats(sys.argv[1])
dst_tables, dst_counts = stats(sys.argv[2])
missing = src_tables - dst_tables
if missing:
    sys.exit(f"备份缺少表：{sorted(missing)}")
print(f"  表数一致：{len(src_tables)} 张")
print("  行数：" + "  ".join(f"{t}={src_counts[t]}" for t in sorted(src_counts)))
diff = {t: (src_counts[t], dst_counts[t]) for t in src_counts if src_counts[t] != dst_counts[t]}
if diff:
    sys.exit(f"行数不一致（表：原库 vs 备份）：{diff}")
if sum(src_counts.values()) == 0:
    sys.exit("原库所有表都是空的——这不是一次有效备份，请人工确认数据库状态")
PY

if [[ "${MODE}" == "db" ]]; then
    log "只备份数据库模式：跳过配置与环境清单"
else
    # ------------------------------------------------------------ 配置
    log "2/3 备份配置、密钥与部署形态"
    # 逐份**显式指定目标文件名**：若用 $(basename "${f}")，三份 .env 会落到
    # 同名 `.env` 上互相覆盖（实测：备份目录里只剩最后一份飞书的），而脚本不报错——
    # 真出事时才发现另外两份密钥没了。
    copy_config() {
        local src=$1 name=$2
        if [[ -f "${src}" ]]; then
            cp -a "${src}" "${DEST}/${name}"
            echo "  已保存 ${src} → ${name}"
        else
            warn "跳过不存在的 ${src}"
        fi
    }
    copy_config "${APP_DIR}/backend/.env"    "backend.env"
    copy_config "${APP_DIR}/RAG/.env"        "rag.env"
    copy_config "${APP_DIR}/feishu-bot/.env" "feishu-bot.env"
    copy_config "/etc/china-war/rag-secrets.env" "rag-secrets.env"
    cp -a /etc/nginx/sites-available/china-war.conf "${DEST}/nginx-china-war.conf" 2>/dev/null \
        || warn "没有 nginx 站点配置"
    cp -a /etc/systemd/system/china-war-*.service /etc/systemd/system/china-war-*.timer "${DEST}/" 2>/dev/null \
        || warn "没有 china-war 的 systemd 单元"
    # 运行用户的家目录 crontab（如果将来有人加了定时任务）
    crontab -l > "${DEST}/root-crontab.txt" 2>/dev/null || true

    # ------------------------------------------------------------ 环境清单
    log "3/3 记录环境与包清单（用于重建环境）"
    for env_name in china-war-backend china-war-rag; do
        py="${CONDA_DIR}/envs/${env_name}/bin/python"
        [[ -x "${py}" ]] || continue
        "${py}" -V > "${DEST}/python-${env_name}.txt" 2>&1
        "${py}" -m pip freeze >> "${DEST}/python-${env_name}.txt" 2>&1 || true
        echo "  已记录 ${env_name}"
    done
    "${CONDA_DIR}/bin/conda" env list > "${DEST}/conda-envs.txt" 2>&1 || true
    # Neo4j 侧：版本 + 数据目录位置（不导出数据，体量大；图谱可从主库重建）
    systemctl status neo4j --no-pager --lines=0 > "${DEST}/neo4j-status.txt" 2>&1 || true
    grep -E "^(dbms\.(directories\.data|default_database|mode)|server\.bolt)" \
        /etc/neo4j/neo4j.conf > "${DEST}/neo4j-conf-subset.txt" 2>/dev/null || true
fi

# ---------------------------------------------------------------- 清单
{
    echo "# china-war 备份清单 ${TS}"
    echo "# 来源：$(hostname)  模式：${MODE}"
    echo
    cd "${DEST}"
    # 用 find 而不是 `for f in *`：后者不展开隐藏文件，而备份里最要紧的恰恰是
    # `.env` 那几份（改了名字的 backend.env 等虽已不是隐藏文件，但别留下这个坑），
    # 于是清单会静默漏掉它们——"有清单"不等于"清单是完整的"。
    find . -maxdepth 1 -type f ! -name manifest.txt -printf '%P\0' \
        | sort -z \
        | while IFS= read -r -d '' f; do
            printf '%s  %10s  %s\n' "$(sha256sum "${f}" | cut -d' ' -f1)" "$(stat -c %s "${f}")" "${f}"
        done
} > "${DEST}/manifest.txt"

# ---------------------------------------------------------------- 保留策略
# 每周日的那份保留 4 周，其余保留最近 7 天。
log "清理过期备份（保留最近 ${KEEP_DAILY} 天 + 最近 ${KEEP_WEEKLY} 个周日）"
if command -v python3 >/dev/null 2>&1; then
    python3 - "${BACKUP_ROOT}" "${KEEP_DAILY}" "${KEEP_WEEKLY}" <<'PY'
import os, shutil, sys, datetime
root, keep_daily, keep_weekly = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
entries = sorted(d for d in os.listdir(root)
                 if len(d) == 15 and d[8] == "-" and os.path.isdir(os.path.join(root, d)))
daily = entries[-keep_daily:]
weekly = [d for d in entries if datetime.datetime.strptime(d, "%Y%m%d-%H%M%S").weekday() == 6][-keep_weekly:]
keep = set(daily) | set(weekly)
for d in entries:
    if d not in keep:
        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        print(f"  已删除过期备份 {d}")
PY
fi

echo
echo "备份完成：${DEST}"
echo "总大小：$(du -sh "${DEST}" | cut -f1)"
echo "目录内容："
ls -la "${DEST}"
