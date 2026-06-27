#!/usr/bin/env bash
# 本地一键起 —— ReadingSteiner WebUI 前后端联调。
#
# 做什么：造一份『本地』测试插件 config.json（双层结构，零生产耦合）+ 起后端 uvicorn
# （临时密钥 + 指向本地副本，绝不碰生产路径 ~/qqbot/.../config.json）+ 起前端 dev server，
# 两端就绪后打印访问地址与登录密钥。Ctrl-C 优雅停两进程并清理本地副本。
#
# ⚠️ 仅本地联调用：密钥是随机生成的一次性值，不进 git、不复用、不粘贴到聊天/在线工具。
# ⚠️ 真机/服务器部署见 deploy/ 与 docs/04-deployment.md，不由本脚本负责。
#
# 依赖：backend/.venv（已建 Python 3.12）、frontend/node_modules（已 npm install）。
# 用法：bash scripts/dev-local.sh   （Git Bash；停：Ctrl-C）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PY="$BACKEND/.venv/Scripts/python.exe"
DATA_DIR="$BACKEND/data/local-dev"
TEST_CFG="$DATA_DIR/astrbot_plugin_readingsteiner_config.json"
PORT=8761

# 颜色提示
say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

# ── 0. 前置检查 ──────────────────────────────────────────────────────
if [ ! -x "$PY" ]; then
  warn "找不到后端 venv python：$PY"
  echo "  请先建：cd backend && py -3.12 -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt"
  exit 1
fi
if [ ! -d "$FRONTEND/node_modules" ]; then
  warn "前端未装依赖：$FRONTEND/node_modules"
  echo "  请先：cd frontend && npm install"
  exit 1
fi

# ── 1. 一次性强随机密钥（本地联调级，必足 32 字节随机）──────────────
# 用 venv python 取 secrets.token_urlsafe，比 bash $RANDOM 强。
SECRET="$("$PY" -c 'import secrets;print(secrets.token_urlsafe(48))')"
say "本地联调密钥（一次性、不进 git）：  $SECRET"

# ── 2. 造本地测试插件 config.json（双层结构，与真机 0.B 契约一致）─────
mkdir -p "$DATA_DIR"
# 外层含其它字段（坐实不被破坏），eternal_covenant 是内层 JSON 字符串。
"$PY" - "$TEST_CFG" <<'PYEOF'
import json, sys
outer = {
    "everos_base_url": "http://127.0.0.1:8596",   # 其它字段，测不被破坏
    "project_id": "default",
    "eternal_covenant": json.dumps(
        {"10001": "示例铭契：把固定核心设定文本写在这里。"},
        ensure_ascii=False,
    ),
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(outer, f, ensure_ascii=False, indent=2)
print(f"  本地测试配置已写：{sys.argv[1]}")
PYEOF

# ── 3. 起后端（后台）+ 等 /health 就绪 ────────────────────────────────
export WEBUI_HOST=127.0.0.1
export WEBUI_PORT=$PORT
export WEBUI_AUTH_SECRET="$SECRET"
export PLUGIN_CONFIG_PATH="$TEST_CFG"
export WEBUI_AUDIT_LOG="$DATA_DIR/audit.jsonl"
export WEBUI_DATA_DIR="$DATA_DIR"
export WEBUI_COVENANT_READONLY=0

say "起后端 uvicorn 127.0.0.1:$PORT …"
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port $PORT --app-dir "$BACKEND" \
  >"$DATA_DIR/backend.log" 2>&1 &
BE_PID=$!
trap 'kill $BE_PID 2>/dev/null || true; [ -n "${FE_PID:-}" ] && kill $FE_PID 2>/dev/null || true' EXIT

# 轮询 /health（最多 ~15s）
ok=0
for _ in $(seq 1 30); do
  if "$PY" -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$PORT/health',timeout=1).status==200 else 1)" 2>/dev/null; then
    ok=1; break
  fi
  if ! kill -0 $BE_PID 2>/dev/null; then
    warn "后端启动失败，日志："; tail -20 "$DATA_DIR/backend.log"; exit 1
  fi
  sleep 0.5
done
if [ $ok -ne 1 ]; then warn "后端 15s 内未就绪，日志："; tail -20 "$DATA_DIR/backend.log"; exit 1; fi
say "后端就绪：http://127.0.0.1:$PORT/health"

# ── 4. 起前端 dev（后台）────────────────────────────────────────────
say "起前端 dev server 127.0.0.1:5173 …"
( cd "$FRONTEND" && npm run dev >"$DATA_DIR/frontend.log" 2>&1 ) &
FE_PID=$!

# ── 5. 打印使用说明 ──────────────────────────────────────────────────
echo
say "================================================================"
say "  打开浏览器： http://127.0.0.1:5173"
say "  登录密钥：   $SECRET"
say "  本地测试配置：$TEST_CFG  （改完不影响生产；停后保留可复查）"
say "  后端日志：   $DATA_DIR/backend.log"
say "  前端日志：   $DATA_DIR/frontend.log"
say "  验证要点：登录→cookie 跨 Vite 代理生效→铭契列表/编辑/删除往返"
say "  停：Ctrl-C（此终端；会清前后端进程，保留 data 便于复查）"
say "================================================================"
echo

# 等任一进程退出则停另一个
wait -n $BE_PID $FE_PID
warn "某端进程已退出，停止另一端…"
kill $BE_PID 2>/dev/null || true
kill $FE_PID 2>/dev/null || true
exit 0