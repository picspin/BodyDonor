#!/usr/bin/env bash
# verify-loop 入口（M7 verify-loop skill）：起 FastAPI（独立端口，非 8000）
# → 跑链路 12 步 → 断言日志无 ERROR → 收尾。任一红步 exit 非零。
#
# 用法：run_chain.sh [--keep-server] [--skip-cdp]
#   --keep-server  链路结束后不关 uvicorn（调试用；默认收尾杀掉）
#   --skip-cdp     跳过 Step 6 CDP 渲染确认（仅调试；正式链路不许跳）
#
# 临时产物（scratch DB、合成 DICOM、SEG 导出）全在仓外 tmp（DESIGN.md 决定 3）。
# Step 12 git-status-clean 断言在 chain_steps.py（白名单见 DESIGN.md 决定 3）。
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
MAIN_VENV="/home/gogvf/work/BodyDonor/.venv/bin/python"
PORT=8765
APP_URL="http://127.0.0.1:$PORT"

KEEP_SERVER=0
SKIP_CDP=0
for arg in "$@"; do
  case "$arg" in
    --keep-server) KEEP_SERVER=1 ;;
    --skip-cdp) SKIP_CDP=1 ;;
    *) echo "unknown arg: $arg"; exit 2 ;;
  esac
done

# 主 checkout venv + worktree src（NOT -I：-I 忽略 PYTHONPATH，pytest 导旧代码坑）
export PYTHONPATH="$REPO/src"
export BODYDONOR_DB=""

TMPD="$(mktemp -d /tmp/verify-loop.XXXXXX)"
LOG="$TMPD/uvicorn.log"
trap 'rm -rf "$TMPD"' EXIT

echo "=== verify-loop start $(date -Is) ==="
echo "repo=$REPO tmp=$TMPD port=$PORT"

# scratch DB + 临时 data dir（绝不碰真实 bodydonor.db / ~/data/BodyDonor）
# settings.yaml 写进 $TMPD：chain_app.py（服务端）与 chain_steps.py（步骤端）
# 都经 VERIFY_LOOP_TMP 绑定同一份（fail-closed：未设即拒绝启动）
# data/tmp 子目录预建：segment_series 的 TS 临时目录落在 resolved_data_dir/tmp
export VERIFY_LOOP_TMP="$TMPD"
mkdir -p "$TMPD/data/tmp"
cat > "$TMPD/settings.yaml" <<YAML
deployment_mode: on_prem
data_dir: $TMPD/data
database:
  url: sqlite:///$TMPD/chain.db
YAML

# Step 1: 启动 FastAPI（独立端口，非 8000——8000 属另一并行轮次的 uvicorn）
"$MAIN_VENV" -m uvicorn --app-dir "$SCRIPT_DIR" chain_app:app --host 127.0.0.1 --port "$PORT" \
  >"$LOG" 2>&1 &
SERVER_PID=$!

cleanup() {
  if [ "$KEEP_SERVER" = "1" ]; then
    echo "=== keep-server: uvicorn PID $SERVER_PID 仍在（log=$LOG） ==="
  else
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
  fi
}
trap cleanup EXIT

# 等 /health 就绪（30s）
READY=0
for _ in $(seq 1 60); do
  sleep 0.5
  if curl -sf "$APP_URL/health" >/dev/null 2>&1; then READY=1; break; fi
done
if [ "$READY" != "1" ]; then
  echo "FAIL step1: FastAPI 未就绪（log=$LOG）"
  tail -20 "$LOG"
  exit 1
fi
echo "step1 ok: FastAPI up (pid $SERVER_PID)"

# Step 2–12: 链路步骤（chain_steps.py，VERIFY_LOOP_TMP 绑定 scratch 配置）
if ! "$MAIN_VENV" "$SCRIPT_DIR/chain_steps.py"; then
  echo "FAIL chain steps（见上方 JSON）"
  exit 1
fi
echo "steps 2-5,7-12 ok"

# Step 6: CDP 渲染确认（skip-cdp 仅调试；正式链路不许跳）
# series_id 由 chain_steps.py 落 state 文件（$TMPD/series_id）
SERIES_ID=""
if [ -f "$TMPD/series_id" ]; then
  SERIES_ID="$(cat "$TMPD/series_id")"
fi
if [ "$SKIP_CDP" != "1" ]; then
  if [ -z "${SERIES_ID:-}" ]; then
    echo "FAIL step6: 无 series_id（CDP 渲染确认需已分段 series）"
    exit 1
  fi
  if ! "$MAIN_VENV" "$SCRIPT_DIR/cdp_open_review.py" --app "$APP_URL" --series-id "$SERIES_ID"; then
    echo "FAIL step6: CDP 渲染确认红（见上方 JSON）"
    exit 1
  fi
  echo "step6 ok: CDP 渲染确认通过"
else
  echo "WARN step6 skipped (--skip-cdp，仅调试)"
fi

# Step 12b: 日志无 ERROR（uvicorn stderr + 应用日志 grep）
if grep -q "ERROR" "$LOG"; then
  echo "FAIL step12b: uvicorn 日志有 ERROR"
  grep "ERROR" "$LOG" | head -5
  exit 1
fi
echo "step12b ok: 日志无 ERROR"

echo "=== verify-loop PASS $(date -Is) ==="
