#!/usr/bin/env bash
# 部署自检：把所有对外入口按用户真实访问路径检查一遍
#
# 用法（服务器上，普通用户即可）：bash deploy/scripts/selfcheck.sh
# 退出码非 0 表示有检查项失败。

set -uo pipefail

APP_DIR=${APP_DIR:-/opt/china-war}
PUBLIC_HOST=${PUBLIC_HOST:-127.0.0.1}   # 想验证 nginx 对外入口就填域名或公网 IP

pass=0
fail=0

ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; pass=$((pass+1)); }
bad()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*"; fail=$((fail+1)); }
head_() { printf '\n\033[1;34m【%s】\033[0m\n' "$*"; }

# 取 URL 的内容；用完就丢，只看是否命中期望字符串
fetch() { curl -sS --max-time 20 "$@" 2>/dev/null; }

# ---------------------------------------------------------------- 服务进程
head_ "systemd 服务"
for unit in china-war-backend china-war-rag china-war-bot; do
    if ! systemctl cat "${unit}.service" >/dev/null 2>&1; then
        echo "  - ${unit}：未安装（不用飞书机器人可忽略）"
        continue
    fi
    state=$(systemctl is-active "${unit}" 2>/dev/null || true)
    if [[ "${state}" == "active" ]]; then
        ok "${unit}：active"
    else
        bad "${unit}：${state}（看日志：journalctl -u ${unit} -n 50）"
    fi
done

# ---------------------------------------------------------------- 补偿队列定时器
head_ "补偿队列（outbox 自动重放）"
if ! systemctl cat china-war-outbox-retry.timer >/dev/null 2>&1; then
    # 这两个单元由 install_services.sh 一起装；漏装之后
    # "Neo4j 写失败会自动补"就成了一句没有执行者的承诺。
    bad "未安装 china-war-outbox-retry.timer —— Neo4j 写失败后的自动重放不会发生（install_services.sh 会装它）"
elif systemctl is-active china-war-outbox-retry.timer | grep -q '^active$'; then
    ok "china-war-outbox-retry.timer 已启用"
    # 下次触发时间是"它真的在跑"的直接证据（未启用时这一行是空的）
    next_run=$(systemctl list-timers --no-pager china-war-outbox-retry.timer 2>/dev/null | sed -n 2p || true)
    [[ -n "${next_run}" ]] && echo "  - 下次触发：$(echo "${next_run}" | awk '{print $1, $2, $3}')"
else
    bad "china-war-outbox-retry.timer 未运行：systemctl enable --now china-war-outbox-retry.timer"
fi

# ---------------------------------------------------------------- 备份
head_ "备份（SQLite 主库）"
# 与补偿队列同样的道理：没有执行者的备份只是磁盘上的一个脚本。
# 这里查三件事——定时器装了、在跑、**并且真的产出过备份目录**（仅有定时器不算数）。
if ! systemctl cat china-war-backup.timer >/dev/null 2>&1; then
    bad "未安装 china-war-backup.timer —— SQLite 主库没有任何自动备份（install_services.sh 会装它）"
elif systemctl is-active china-war-backup.timer | grep -q '^active$'; then
    ok "china-war-backup.timer 已启用"
    next_run=$(systemctl list-timers --no-pager china-war-backup.timer 2>/dev/null | sed -n 2p || true)
    [[ -n "${next_run}" ]] && echo "  - 下次触发：$(echo "${next_run}" | awk '{print $1, $2, $3}')"
    latest=$(ls -1dt /var/backups/china-war/*/ 2>/dev/null | head -1 || true)
    if [[ -n "${latest}" ]]; then
        ok "最近一份备份：$(basename "${latest}")（$(du -sh "${latest}" 2>/dev/null | cut -f1)）"
        # 备份里必须有数据库文件，否则"有目录"≠"有备份"
        if [[ -f "${latest}/database.sqlite" ]]; then
            ok "备份内含 database.sqlite"
        else
            bad "${latest} 里没有 database.sqlite（备份没跑完？journalctl -u china-war-backup -n 50）"
        fi
    else
        bad "/var/backups/china-war/ 下没有任何备份目录：定时器在跑但没有产出"
    fi
else
    bad "china-war-backup.timer 未运行：systemctl enable --now china-war-backup.timer"
fi

# ---------------------------------------------------------------- 后端直连
head_ "旧后端（:5000）"
if fetch "http://127.0.0.1:5000/api/graph/event_event" | grep -q '{'; then
    ok "图谱接口有响应"
else
    bad "图谱接口无响应（journalctl -u china-war-backend -n 50）"
fi

# ---------------------------------------------------------------- RAG 直连
head_ "RAG 服务（:8000）"
health=$(fetch "http://127.0.0.1:8000/api/health")
if [[ "${health}" == *'"status"'* && "${health}" == *'"ok"'* ]]; then
    ok "health 正常"
    for key in vector_available llm_available; do
        if [[ "${health}" == *"\"${key}\":true"* ]]; then
            ok "${key}=true"
        else
            bad "${key}=false（向量密钥/索引缺失，或 LLM 密钥没配；会降级运行）"
        fi
    done
    if [[ "${health}" == *'cli_explicit'* ]]; then
        ok "数据版本已显式固定（cli_explicit）"
    else
        echo "  - 数据版本来源：$(echo "${health}" | grep -o '"version_selection":"[^"]*"' || echo '未知')"
    fi
else
    bad "health 异常（journalctl -u china-war-rag -n 50）"
fi

if fetch "http://127.0.0.1:8000/" | grep -q '<div id="app"'; then
    ok "RAG 前端已同源托管"
else
    bad "GET / 没有返回页面（RAG 前端未构建？跑 deploy/scripts/03_build_frontend.sh）"
fi

# ---------------------------------------------------------------- 经 nginx
head_ "nginx 入口（http://${PUBLIC_HOST}/）"
home=$(fetch "http://${PUBLIC_HOST}/")
if [[ "${home}" == *'/static/assets/'* ]]; then
    ok "首页返回，且资源前缀是 /static/（并入模式正确）"
elif [[ "${home}" == *'<html'* ]]; then
    bad "首页有返回，但资源前缀不是 /static/ —— 旧前端可能用了错误的构建模式"
else
    bad "首页无响应（nginx -t 是否通过？frontend/dist 是否存在？）"
fi

if fetch "http://${PUBLIC_HOST}/rag/" | grep -q '/rag/assets/'; then
    ok "/rag/ 返回，且资源前缀是 /rag/assets/（并入模式正确）"
else
    bad "/rag/ 异常 —— RAG 前端必须是 npm run build:integration 的产物，或 nginx 的 /rag/ 转发没配好"
fi

if fetch "http://${PUBLIC_HOST}/rag/api/health" | grep -q '"status"'; then
    ok "/rag/api/health 经反代可达"
else
    bad "/rag/api/health 不可达（检查 nginx 的 proxy_pass 是否以斜杠结尾）"
fi

# 旧后端的登录接口：能返回 JSON 说明反代与 Flask 都活着（凭据是假的，返回失败码属正常）
if fetch -X POST -H 'Content-Type: application/json' -d '{"username":"__selfcheck__","password":"__selfcheck__"}' \
        "http://${PUBLIC_HOST}/api/login" | grep -q 'code'; then
    ok "旧后端登录接口经反代可达（返回体带 code 字段，即链路正常）"
else
    bad "旧后端登录接口不可达"
fi

# ---------------------------------------------------------------- Neo4j
head_ "Neo4j"
if command -v cypher-shell >/dev/null 2>&1; then
    pw=$(grep '^NEO4J_PASSWORD=' "${APP_DIR}/backend/.env" 2>/dev/null | head -1 \
         | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs || true)
    if [[ -n "${pw}" ]] && cypher-shell -u neo4j -p "${pw}" "RETURN 1" >/dev/null 2>&1; then
        ok "cypher-shell 连接正常"
    else
        bad "连不上 Neo4j（口令对不上，或服务没起：systemctl status neo4j）"
    fi
else
    if [[ -n "$(ss -lnt 2>/dev/null | grep ':7687' || true)" ]]; then
        ok "7687 端口在监听"
    else
        bad "7687 未监听（systemctl status neo4j）"
    fi
fi

# ---------------------------------------------------------------- 汇总
printf '\n\033[1m检查完成：%d 项通过，%d 项失败\033[0m\n' "${pass}" "${fail}"
if [[ "${fail}" -gt 0 ]]; then
    cat <<'EOF'

常见故障对照（完整表见 deploy/README.md 第八节）：
  · 服务起不来            → journalctl -u <服务名> -n 50
  · 页面白屏、资源 404    → 前端构建模式错了，重跑 03_build_frontend.sh
  · 回答“憋住”最后一次性出 → nginx 少了 proxy_buffering off
  · 多人同时用就 429      → RAG/.env 里 RATE_LIMIT_TRUST_FORWARDED_FOR 没开
EOF
    exit 1
fi
