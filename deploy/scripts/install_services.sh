#!/usr/bin/env bash
# 安装 systemd 服务与 nginx 站点（在服务器上执行）
#
# 用法：
#   sudo bash deploy/scripts/install_services.sh
#   sudo SERVER_NAME=your.domain bash deploy/scripts/install_services.sh   # 绑域名
#
# 执行前请先准备好这三个配置文件（脚本会检查并在缺失时提醒）：
#   ${APP_DIR}/backend/.env       ← deploy/env/backend.env
#   ${APP_DIR}/RAG/.env           ← deploy/env/rag.env
#   ${APP_DIR}/feishu-bot/.env    ← 只用飞书机器人时才需要（缺失时该服务保持禁用）

set -euo pipefail

APP_DIR=${APP_DIR:-/opt/china-war}
CONDA_DIR=${CONDA_DIR:-/opt/miniconda3}
APP_USER=${APP_USER:-chinawar}
SERVER_NAME=${SERVER_NAME:-_}

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[提示] %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 root 或 sudo 执行：sudo bash $0"
[[ -d "${APP_DIR}/deploy" ]] || die "找不到 ${APP_DIR}/deploy —— 请先上传代码"

# 把模板里的占位路径/用户替换成本机实际值
render() {
    sed \
        -e "s#/opt/china-war#${APP_DIR}#g" \
        -e "s#/opt/miniconda3#${CONDA_DIR}#g" \
        -e "s/^User=chinawar/User=${APP_USER}/" \
        -e "s/^Group=chinawar/Group=${APP_USER}/"
}

# ---------------------------------------------------------------- 配置检查
log "1/5 检查配置文件"
missing=0
if [[ -f "${APP_DIR}/backend/.env" ]]; then
    echo "✓ backend/.env"
else
    warn "缺少 ${APP_DIR}/backend/.env（模板：deploy/env/backend.env）"
    missing=1
fi
if [[ -f "${APP_DIR}/RAG/.env" ]]; then
    echo "✓ RAG/.env"
else
    warn "缺少 ${APP_DIR}/RAG/.env（模板：deploy/env/rag.env）"
    missing=1
fi
if [[ -f "${APP_DIR}/feishu-bot/.env" ]]; then
    echo "✓ feishu-bot/.env"
else
    warn "缺少 ${APP_DIR}/feishu-bot/.env —— 飞书机器人将不启用"
fi
[[ "${missing}" -eq 0 ]] || warn "缺配置也能装服务，但服务会启动失败；建议先补齐再继续"

# 权限收紧：.env 里是 Neo4j 口令与 JWT 密钥
chmod 600 "${APP_DIR}/backend/.env" 2>/dev/null || true
chmod 600 "${APP_DIR}/RAG/.env" 2>/dev/null || true
chmod 600 "${APP_DIR}/feishu-bot/.env" 2>/dev/null || true
chown "${APP_USER}:${APP_USER}" "${APP_DIR}/backend/.env" "${APP_DIR}/RAG/.env" 2>/dev/null || true
chown "${APP_USER}:${APP_USER}" "${APP_DIR}/feishu-bot/.env" 2>/dev/null || true

# 上传过来的文件属主可能是 root，而服务以 ${APP_USER} 运行（要写 logs/ 与 SQLite）
for d in backend RAG feishu-bot; do
    if [[ -e "${APP_DIR}/${d}" ]]; then
        chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/${d}"
    fi
done
chmod 755 "${APP_DIR}"

# ---------------------------------------------------------------- systemd
log "2/5 安装 systemd 服务单元"
# 补偿队列的两个单元一起装：它们不是"可选附件"——漏装则 Neo4j 写失败的自动重放
# 不会发生，而 selfcheck.sh 会因此报失败（不靠"照 README 手工 cp"来保证）。
# 注意单元名必须带 `.service` 后缀：这里拼的是 `deploy/systemd/${unit}` 与
# `/etc/systemd/system/${unit}` 两个路径，名字写错一个字就会走到
# `die "缺少 .../systemd/china-war-backend"`、卡在 2/5，而 CI 只看脚本语法
# （`bash -n` 不会发现名字写错）。
# 回归见 scripts/check_deploy_config.py 的 check_install_services_unit_list。
for unit in china-war-backend.service china-war-rag.service china-war-bot.service \
            china-war-outbox-retry.service china-war-outbox-retry.timer \
            china-war-backup.service china-war-backup.timer; do
    src="${APP_DIR}/deploy/systemd/${unit}"
    if [[ ! -f "${src}" ]]; then
        # 只有可选的 bot 允许缺失；其它缺了就是部署包不完整
        if [[ "${unit}" == "china-war-bot.service" ]]; then
            warn "缺少 ${src}（不用飞书机器人可忽略）"
            continue
        fi
        die "缺少 ${src}"
    fi
    render < "${src}" > "/etc/systemd/system/${unit}"
    echo "已写入 /etc/systemd/system/${unit}"
done

# 非密钥的 RAG 配置由应用自读；密钥单独放 /etc/china-war/rag-secrets.env
mkdir -p /etc/china-war
if [[ ! -f /etc/china-war/rag-secrets.env ]]; then
    cp "${APP_DIR}/deploy/env/rag-secrets.env" /etc/china-war/rag-secrets.env
    chmod 600 /etc/china-war/rag-secrets.env
    chown root:root /etc/china-war/rag-secrets.env
    warn "已创建 /etc/china-war/rag-secrets.env，请填入 LLM_API_KEY 与 DASHSCOPE_API_KEY"
fi

systemctl daemon-reload
systemctl enable china-war-backend china-war-rag
if [[ -f "${APP_DIR}/feishu-bot/.env" ]]; then
    systemctl enable china-war-bot
fi
# 定时器用 --now 立刻生效：它每分钟跑一次重放，不启动的话"自动重放"只是文件躺在磁盘上
if [[ -f /etc/systemd/system/china-war-outbox-retry.timer ]]; then
    systemctl enable --now china-war-outbox-retry.timer
    systemctl list-timers --no-pager china-war-outbox-retry.timer || true
fi
# 备份定时器同理：`enable` 不等于"现在开始生效"，而"没有执行者的备份"是最危险的
# 那种安心——磁盘上躺着脚本、出事时才发现一次都没跑过。这里立刻跑一次并打印结果。
if [[ -f /etc/systemd/system/china-war-backup.timer ]]; then
    systemctl enable --now china-war-backup.timer
    systemctl list-timers --no-pager china-war-backup.timer || true
    echo "先跑一次备份（验证它真的能跑通，而不是等到 03:20 才发现失败）："
    systemctl start china-war-backup.service || warn "首次备份执行失败，看 journalctl -u china-war-backup -n 50"
    ls -1 /var/backups/china-war/ 2>/dev/null | tail -3 || warn "备份目录还是空的"
fi

# ---------------------------------------------------------------- nginx
log "3/5 安装 nginx 站点"
command -v nginx >/dev/null 2>&1 || {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq nginx
}
render < "${APP_DIR}/deploy/nginx/china-war.conf" \
    | sed "s/^\( *\)server_name your.domain.or.ip;/\1server_name ${SERVER_NAME};/" \
    > /etc/nginx/sites-available/china-war.conf
ln -sf /etc/nginx/sites-available/china-war.conf /etc/nginx/sites-enabled/china-war.conf
rm -f /etc/nginx/sites-enabled/default
echo "站点已启用：server_name ${SERVER_NAME}"

log "4/5 校验配置"
nginx -t

# 鉴权门禁：必须在**启动服务之前**拦住
# "RAG 以为 nginx 在鉴权、nginx 实际没配认证"这类组合——那种部署两边都能正常启动、
# 日志里没有任何异常，唯一的后果是公网 RAG 没有访问控制。脚本会校验：
# 鉴权模式取值、两侧 JWT 密钥是否同值、nginx 档下 auth_basic 是否真的生效
# （含 nginx -T 的实际生效配置）、RAG 是否只监听回环、内部接口是否被屏蔽。
#
# 用 `|| true` 包一层只为打印得更清楚：失败原因是"配置项不对"而不是"脚本坏了"，
# 说清楚之后仍然拒绝继续。
if ! bash "${APP_DIR}/deploy/scripts/check_rag_auth.sh"; then
    die "鉴权配置未通过门禁（原因见上）。修好后再执行本脚本；服务未启动。"
fi

# ---------------------------------------------------------------- 启动
log "5/5 启动服务"
systemctl restart china-war-backend china-war-rag
if [[ -f "${APP_DIR}/feishu-bot/.env" ]]; then
    systemctl restart china-war-bot
fi
systemctl reload nginx
sleep 4

systemctl --no-pager --lines=0 status china-war-backend china-war-rag || true

cat <<EOF

安装完成。自检：
  bash ${APP_DIR}/deploy/scripts/selfcheck.sh

看日志：
  journalctl -u china-war-backend -f
  journalctl -u china-war-rag -f

防火墙/云安全组记得放行 80（用 HTTPS 再放行 443）；5000/8000 不需要对外开。
EOF
