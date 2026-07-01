#!/usr/bin/env bash
# episode 删除功能 —— 一键前端视觉验证（本机手动核对用，不进正式验收流程）。
#
# 和 scripts/dev-local.sh 的关键区别：这里不用 CLI 起 uvicorn，而是生成一个独立
# 的运行时启动器，给 MockEverOSGateway.list_episodes 打个内存补丁（monkeypatch），
# 让它吐出一行合成 episode——只在这一次性子进程里生效，backend/app 下的源文件
# 从头到尾不会被改动。所以不需要手动"回档"：Ctrl-C 退出时自动清理临时文件即可。
#
# 用法（必须 Git Bash，不能是 PowerShell/cmd）：
#   bash scripts/verify-episode-delete.sh          启动测试会话
#   bash scripts/verify-episode-delete.sh --clean  仅清理上次遗留的临时文件（不启动）
#
# 已知平台限制（非本脚本 bug）：Windows 没有 systemctl，点「确认删除」后端会在
# Palimpsest 的停机检查那步返回 409（"cannot determine everos status"）——这正是
# 二期真机验证时发现并修复的那个 bug 的效果（以前是裸 500，现在是干净的 409）。
# 要看到"真正删除成功、entry 从列表消失"，需要在有 systemd 的 Linux 环境上测。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PY="$BACKEND/.venv/Scripts/python.exe"
DATA_DIR="$BACKEND/data/local-dev"
MEMROOT="$DATA_DIR/episode-verify-memroot"
LAUNCHER_REL="data/local-dev/_verify_episode_delete_launcher.py"
LAUNCHER="$BACKEND/$LAUNCHER_REL"
TEST_CFG="$DATA_DIR/episode-verify-plugin-config.json"
PORT=8761
TEST_UID=9999999999
TEST_ENTRY=ep_20260101_00000001

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

cleanup() {
  warn "清理临时文件…"
  [ -n "${BE_PID:-}" ] && kill "$BE_PID" 2>/dev/null || true
  [ -n "${FE_PID:-}" ] && kill "$FE_PID" 2>/dev/null || true
  rm -rf "$MEMROOT" "$LAUNCHER" "$TEST_CFG" "$DATA_DIR/__pycache__"
  say "已清理。源码从头到尾零改动，无需手动回档。"
}

if [ "${1:-}" = "--clean" ]; then
  cleanup
  exit 0
fi

trap cleanup EXIT INT TERM

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
if netstat -ano 2>/dev/null | grep -q ":$PORT .*LISTENING"; then
  warn "端口 $PORT 已被占用（前端 vite 代理写死指向这个端口，换不了）。"
  echo "  用 netstat -ano | grep :$PORT 查是哪个进程占用，处理掉再重跑本脚本。"
  exit 1
fi

# ── 1. 合成测试数据（纯本机临时文件，QQ 号是假的）────────────────────
mkdir -p "$MEMROOT/astrbot/default_project/users/$TEST_UID/episodes"
cat > "$MEMROOT/astrbot/default_project/users/$TEST_UID/episodes/episode-2026-01-01.md" <<EOF
---
entry_count: 1
---
<!-- entry:$TEST_ENTRY -->
## $TEST_ENTRY

**owner_id**: $TEST_UID
**timestamp**: 2026-01-01T10:00:00+00:00

视觉验证用的合成 episode，可以随便删。
<!-- /entry:$TEST_ENTRY -->
EOF
say "合成测试数据已写：$MEMROOT"

# ── 2. 生成运行时补丁启动器（monkeypatch，不改仓内任何源文件）────────
mkdir -p "$DATA_DIR"
cat > "$LAUNCHER" <<PYEOF
"""运行时补丁：MockEverOSGateway.list_episodes 吐一行合成数据，供前端视觉验证。
只在这个独立进程里生效——backend/app 下的源文件从未被修改。用完随脚本清理。"""
import uvicorn
from app.modules.everos_gateway import EpisodeDTO, MockEverOSGateway


async def _patched_list_episodes(self, *, user_id, app_id, project_id, page=1, page_size=20):
    return [
        EpisodeDTO(
            entry_id=f"{user_id}_$TEST_ENTRY",
            summary="视觉验证用的合成 episode",
            subject="测试标题",
            timestamp="2026-01-01T10:00:00+00:00",
            raw={},
        )
    ], 1


MockEverOSGateway.list_episodes = _patched_list_episodes

from app.main import app

uvicorn.run(app, host="127.0.0.1", port=$PORT)
PYEOF

# ── 3. 最简插件配置（covenant_store 构造要一个路径，本次验证不会真的读它）──
cat > "$TEST_CFG" <<'EOF'
{"everos_base_url": "http://127.0.0.1:8596", "project_id": "default", "eternal_covenant": "{}"}
EOF

# ── 4. 起后端（走运行时补丁启动器，不走 uvicorn CLI）─────────────────
SECRET="$("$PY" -c 'import secrets;print(secrets.token_urlsafe(48))')"
export WEBUI_HOST=127.0.0.1
export WEBUI_PORT=$PORT
export WEBUI_AUTH_SECRET="$SECRET"
export EVEROS_MEMORY_ROOT="$MEMROOT"
export PLUGIN_CONFIG_PATH="$TEST_CFG"
export WEBUI_AUDIT_LOG="$DATA_DIR/audit.jsonl"
export WEBUI_DATA_DIR="$DATA_DIR"
export WEBUI_COVENANT_READONLY=1

say "起后端（带合成 episode 补丁）127.0.0.1:$PORT …"
( cd "$BACKEND" && "$PY" "$LAUNCHER_REL" >"$DATA_DIR/backend.log" 2>&1 ) &
BE_PID=$!

ok=0
for _ in $(seq 1 30); do
  if "$PY" -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$PORT/health',timeout=1).status==200 else 1)" 2>/dev/null; then
    ok=1; break
  fi
  if ! kill -0 $BE_PID 2>/dev/null; then
    warn "后端启动失败，日志："; tail -30 "$DATA_DIR/backend.log"; exit 1
  fi
  sleep 0.5
done
if [ $ok -ne 1 ]; then warn "后端 15s 内未就绪，日志："; tail -30 "$DATA_DIR/backend.log"; exit 1; fi
say "后端就绪：http://127.0.0.1:$PORT/health"

# ── 5. 起前端 ──────────────────────────────────────────────────────
say "起前端 dev server 127.0.0.1:5173 …"
( cd "$FRONTEND" && npm run dev >"$DATA_DIR/frontend.log" 2>&1 ) &
FE_PID=$!

echo
say "================================================================"
say "  打开浏览器： http://127.0.0.1:5173"
say "  登录密钥：   $SECRET"
say "  测试步骤："
say "    1. 登录"
say "    2. 进「画像/情景只读」页，user_id 改成 $TEST_UID"
say "    3. 点「查 episode」→ 应该看到一行「测试标题」，行尾有删除按钮"
say "    4. 点删除 → 弹出确认浮层，应显示 Palimpsest 的 plan diff"
say "    5. 点确认删除 → 预期弹出红色 409 提示（见下方平台限制说明）"
say "  停：Ctrl-C（自动清理临时文件 + 杀两端进程，源码零改动无需回档）"
say "================================================================"
echo
warn "本机（Windows）没有 systemctl，点确认删除后必然停在 409，不会真正删除文件。"
warn "要看到真正删除成功，需要在有 systemd 的 Linux 环境（如生产服务器）上测。"
echo

wait -n $BE_PID $FE_PID
warn "某端进程已退出，停止另一端…"
