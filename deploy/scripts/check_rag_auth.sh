#!/usr/bin/env bash
# RAG 鉴权配置门禁（第 13 轮复核第五节）
#
# 用法（服务器上，root）：
#   bash deploy/scripts/check_rag_auth.sh
#
# 退出码：0 = 全部通过（允许有告警）；1 = 有失败项。install_services.sh 在启动服务
# 之前调用它，失败即终止安装——"配置说配了、实际没生效"的组合不能进入运行态。
#
# ## 为什么需要这个脚本
#
# 第 13 轮复核发现的缺陷：部署模板写 RAG_AUTH_MODE=nginx，而 nginx 站点配置里的
# auth_basic 是**注释状态**。照着两份模板部署的人会得到：
#
#     RAG 认为"nginx 在鉴权"    → 自己不验签，正常启动
#     nginx 其实没配认证        → 配置语法正确，正常启动
#
# 两边都健康、日志里没有任何异常，唯一后果是公网 RAG 没有访问控制。这类缺陷
# 靠注释提醒拦不住（"配了两处、其实少配一处"永远看不出少的是哪一处），
# 只能在安装路径上做**机器可判定**的校验。
#
# ## 检查项
#
# 1. 鉴权模式取值合法，且生产档下不是 disabled；
# 2. jwt 档：RAG 与 backend 两侧的 JWT 密钥都存在且**同值**（不同值的表现是问答全 401）；
# 3. jwt 档：撤销查询要么两边都配齐，要么明确告警（不阻断，但要让人看见"停用后
#    在 token 到期前仍可用"这条边界）；
# 4. nginx 档：.htpasswd 存在、站点配置里 auth_basic 是**启用状态**、nginx -T
#    的实际生效配置里 /rag/ 带认证——三件缺一不可；
# 5. 两种档位都要求：RAG 只监听回环，且 nginx 对 /api/internal/ 返回 404。

set -uo pipefail

APP_DIR=${APP_DIR:-/opt/china-war}
RAG_ENV=${RAG_ENV:-${APP_DIR}/RAG/.env}
BACKEND_ENV=${BACKEND_ENV:-${APP_DIR}/backend/.env}
NGINX_SITE=${NGINX_SITE:-/etc/nginx/sites-enabled/china-war.conf}
HTPASSWD=${HTPASSWD:-/etc/nginx/.htpasswd}

if [[ $# -gt 0 ]]; then
    echo "本脚本不接受参数（收到：$*）" >&2
    exit 2
fi

pass=0
fail=0
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; pass=$((pass+1)); }
bad()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*"; fail=$((fail+1)); }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
head_() { printf '\n\033[1;34m【%s】\033[0m\n' "$*"; }

# 读 .env 里的值：忽略注释行，去掉引号与首尾空白；键不存在时输出空串。
# 用 grep 而不是 source：这些文件里的值可能含引号与特殊字符，且我们**不能**执行它们。
env_value() {
    local file=$1 key=$2
    [[ -f "${file}" ]] || { echo ""; return; }
    grep -E "^[[:space:]]*${key}[[:space:]]*=" "${file}" 2>/dev/null \
        | tail -1 \
        | cut -d= -f2- \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
              -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || echo ""
}

[[ -f "${RAG_ENV}" ]] || bad "找不到 ${RAG_ENV}（RAG 配置缺失，无法判定鉴权模式）"
[[ -f "${BACKEND_ENV}" ]] || warn "找不到 ${BACKEND_ENV}（无法比对两侧密钥）"

# ---------------------------------------------------------------- 1. 模式
head_ "鉴权模式"

mode=$(env_value "${RAG_ENV}" RAG_AUTH_MODE)
if [[ -z "${mode}" ]]; then
    # 兼容旧开关：RAG_REQUIRE_AUTH=true 等价于 jwt
    if [[ "$(env_value "${RAG_ENV}" RAG_REQUIRE_AUTH | tr '[:upper:]' '[:lower:]')" == "true" ]]; then
        mode=jwt
        warn "RAG/.env 用旧开关 RAG_REQUIRE_AUTH=true（等价于 jwt 档）；建议改成 RAG_AUTH_MODE=jwt"
    else
        mode=disabled
    fi
fi

case "${mode}" in
    jwt|nginx) ok "RAG_AUTH_MODE=${mode}" ;;
    disabled)
        production=$(env_value "${RAG_ENV}" RAG_REQUIRE_ACTIVE_VERSION | tr '[:upper:]' '[:lower:]')
        if [[ "${production}" == "true" ]]; then
            bad "生产档（RAG_REQUIRE_ACTIVE_VERSION=true）下 RAG_AUTH_MODE=disabled：知道 /rag/ 地址的人都能调用问答接口（服务本身也会拒绝启动）"
        else
            warn "RAG_AUTH_MODE=disabled：仅限开发/内网；对外部署请改成 jwt 或 nginx"
        fi
        ;;
    *)
        bad "RAG_AUTH_MODE 取值非法：'${mode}'（只允许 jwt / nginx / disabled）"
        ;;
esac

# ---------------------------------------------------------------- 2. jwt 档：两侧密钥同值
if [[ "${mode}" == "jwt" ]]; then
    head_ "jwt 档：密钥一致性"

    rag_secret=$(env_value "${RAG_ENV}" RAG_JWT_SECRET)
    [[ -z "${rag_secret}" ]] && rag_secret=$(env_value "${RAG_ENV}" JWT_SECRET)
    backend_secret=$(env_value "${BACKEND_ENV}" JWT_SECRET)

    if [[ -z "${rag_secret}" ]]; then
        bad "RAG/.env 未配置 RAG_JWT_SECRET —— jwt 档下服务会拒绝启动（缺密钥等于每个请求都 401）"
    elif [[ -z "${backend_secret}" ]]; then
        warn "backend/.env 里读不到 JWT_SECRET，无法比对（若两边不同值，表现是问答全部 401）"
    elif [[ "${rag_secret}" != "${backend_secret}" ]]; then
        # 这是这套校验最容易踩的坑：两侧都配了、语法都对，就是值不一样
        bad "两侧 JWT 密钥不同值：RAG 的 RAG_JWT_SECRET 与 backend 的 JWT_SECRET 必须完全一致"
    else
        ok "JWT 密钥两侧同值"
    fi
fi

# ---------------------------------------------------------------- 3. 撤销查询
if [[ "${mode}" == "jwt" ]]; then
    head_ "jwt 档：凭证撤销查询"

    introspect_url=$(env_value "${RAG_ENV}" RAG_INTROSPECT_URL)
    rag_key=$(env_value "${RAG_ENV}" RAG_INTERNAL_SERVICE_KEY)
    [[ -z "${rag_key}" ]] && rag_key=$(env_value "${RAG_ENV}" INTERNAL_SERVICE_KEY)
    backend_key=$(env_value "${BACKEND_ENV}" INTERNAL_SERVICE_KEY)
    fail_mode=$(env_value "${RAG_ENV}" RAG_INTROSPECT_FAIL_MODE)
    [[ -z "${fail_mode}" ]] && fail_mode=closed

    if [[ -z "${introspect_url}" && -z "${rag_key}" ]]; then
        warn "未启用凭证撤销查询：**账号被停用或改密码后，旧 token 在自然过期（默认 7 天）前仍可调用 RAG 问答接口**（旧后端已立刻拒绝，两侧口径不同）"
    elif [[ -z "${introspect_url}" || -z "${rag_key}" ]]; then
        bad "撤销查询只配了一半（RAG_INTROSPECT_URL / RAG_INTERNAL_SERVICE_KEY 缺一）：RAG 会按未启用处理，撤销延迟边界依然存在"
    elif [[ -z "${backend_key}" ]]; then
        bad "backend/.env 未配置 INTERNAL_SERVICE_KEY：内部接口会返回 503，RAG 的撤销查询全部失败（fail_mode=${fail_mode}）"
    elif [[ "${rag_key}" != "${backend_key}" ]]; then
        bad "两侧服务间密钥不同值：RAG 的 RAG_INTERNAL_SERVICE_KEY 与 backend 的 INTERNAL_SERVICE_KEY 必须完全一致"
    elif [[ "${fail_mode}" != "closed" && "${fail_mode}" != "open" ]]; then
        bad "RAG_INTROSPECT_FAIL_MODE 取值非法：'${fail_mode}'（只允许 closed / open）"
    else
        ok "撤销查询已配置（fail_mode=${fail_mode}，撤销生效延迟上界 = RAG_INTROSPECT_TTL_SECONDS）"
    fi
fi

# ---------------------------------------------------------------- 4. nginx 档：认证必须真的生效
if [[ "${mode}" == "nginx" ]]; then
    head_ "nginx 档：反代层认证"

    if [[ -f "${HTPASSWD}" ]]; then
        if [[ -s "${HTPASSWD}" ]]; then
            ok "${HTPASSWD} 存在且非空"
        else
            bad "${HTPASSWD} 是空文件 —— auth_basic 会把所有请求都拒掉（含正常用户）"
        fi
    else
        bad "缺少 ${HTPASSWD}：先执行 sudo htpasswd -c ${HTPASSWD} china-war"
    fi

    if [[ -f "${NGINX_SITE}" ]]; then
        # 只看**未注释**的 auth_basic（行首可有空白，但不能是 #）
        if grep -Eq '^[[:space:]]*auth_basic[[:space:]]' "${NGINX_SITE}"; then
            ok "站点配置里 auth_basic 处于启用状态"
        else
            bad "站点配置里 auth_basic 仍是注释状态：RAG 不会验签，nginx 也不认证 —— 公网可匿名调用问答接口"
        fi
        if grep -Eq '^[[:space:]]*auth_basic_user_file[[:space:]]' "${NGINX_SITE}"; then
            ok "auth_basic_user_file 已启用"
        else
            bad "站点配置里 auth_basic_user_file 仍是注释状态（有 auth_basic 而没有口令文件，nginx -t 会失败）"
        fi
    else
        bad "找不到站点配置 ${NGINX_SITE}（先跑 install_services.sh）"
    fi

    # 真正生效的配置以 nginx -T 为准：站点文件写对了但没被 include、或另有 location
    # 覆盖了认证，都只有在合并后的配置里才看得见。
    if command -v nginx >/dev/null 2>&1; then
        merged=$(nginx -T 2>/dev/null || true)
        if [[ -z "${merged}" ]]; then
            bad "nginx -T 无输出（配置有语法错误？先跑 nginx -t）"
        else
            # /rag/ 的 location 块内应出现未注释的 auth_basic。用 awk 提取该 location
            # 到下一个 location 之间的片段，避免"别的 location 配了认证"造成假通过。
            rag_block=$(printf '%s\n' "${merged}" | awk '
                /location[[:space:]]+[=~^]*[[:space:]]*\/rag\// {inside=1}
                inside && /location[[:space:]]/ && $0 !~ /\/rag\// {inside=0}
                inside {print}
            ')
            if printf '%s\n' "${rag_block}" | grep -Eq '^[[:space:]]*auth_basic[[:space:]]'; then
                ok "nginx 实际生效的 /rag/ 配置带 auth_basic"
            else
                bad "nginx 实际生效的 /rag/ 配置里没有 auth_basic（认证没有作用在 /rag/ 上）"
            fi
        fi
    else
        warn "未安装 nginx，跳过 nginx -T 校验"
    fi
fi

# ---------------------------------------------------------------- 5. 两种档位都要满足
head_ "回环监听与内部接口不可达"

RAG_UNIT=${RAG_UNIT:-/etc/systemd/system/china-war-rag.service}
if [[ -f "${RAG_UNIT}" ]]; then
    if grep -Eq '\-\-host[[:space:]]+(127\.0\.0\.1|localhost|::1)' "${RAG_UNIT}"; then
        ok "RAG 只监听回环（systemd 单元）"
    elif grep -Eq '\-\-host[[:space:]]+0\.0\.0\.0' "${RAG_UNIT}"; then
        bad "RAG 监听 0.0.0.0：同网段可直连 8000 绕过 nginx（nginx 档下服务本身也会拒绝启动）"
    else
        warn "无法从 ${RAG_UNIT} 判定监听地址（请确认 --host 是回环地址）"
    fi
else
    warn "找不到 ${RAG_UNIT}（先跑 install_services.sh）"
fi

if [[ -f "${NGINX_SITE}" ]] && grep -Eq '^[[:space:]]*location[[:space:]]+/api/internal/' "${NGINX_SITE}"; then
    ok "nginx 已屏蔽 /api/internal/（内部接口公网不可达）"
else
    bad "站点配置缺少 location /api/internal/ —— 内部接口可能可被公网访问（该接口由服务间密钥保护，但不应暴露）"
fi

# ---------------------------------------------------------------- 汇总
printf '\n\033[1m鉴权门禁：%d 项通过，%d 项失败\033[0m\n' "${pass}" "${fail}"
if [[ "${fail}" -gt 0 ]]; then
    cat <<'EOF'

常见成因：
  · jwt 档问答全 401        → 两侧 JWT_SECRET 不同值（重新执行本脚本比对）
  · nginx 档公网可匿名访问   → auth_basic 仍是注释状态
  · 停用账号后 RAG 仍可用    → 撤销查询未启用（RAG_INTROSPECT_URL + 服务间密钥）
EOF
    exit 1
fi
exit 0
