#!/usr/bin/env bash
# HerPulse 海外采集（只采集，不跑抽取/聚合/渲染）
# 产物：data/corpus_bluesky_<时间戳>.json + data/fanwork_ao3_<时间戳>.json
set -uo pipefail

# ---- 定位项目根（含 src/ 的目录），兼容本地(herpulse/deploy/)与容器(/app/) ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/src" ]; then
  ROOT="$SCRIPT_DIR"
elif [ -d "$SCRIPT_DIR/../src" ]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  echo "错误：找不到 src/ 目录（当前 $SCRIPT_DIR）" >&2
  exit 1
fi
cd "$ROOT"

# ---- 配置（环境变量优先）----
AO3_TAGS="${HERPULSE_AO3_TAGS:-Enemies to Lovers,Slow Burn,Yandere,Boss and Employee,Office Romance,Reincarnation}"

STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="${1:-all}"   # all | bluesky | ao3

run_bluesky() {
  echo "[bluesky] 采集 ..."
  python src/fetchers.py --source bluesky --limit 30 \
    --out "data/corpus_bluesky_$STAMP.json"
}

run_ao3() {
  echo "[ao3] 采集 fanwork 信号 ..."
  python src/fetchers.py --source ao3 --tags "$AO3_TAGS" \
    --out "data/fanwork_ao3_$STAMP.json"
}

echo "===== HerPulse 海外采集 $STAMP (mode=$TARGET) ====="

if [ "$TARGET" = "all" ]; then
  run_bluesky || echo "WARN: Bluesky 采集失败"
  run_ao3     || echo "WARN: AO3 采集失败"
elif [ "$TARGET" = "bluesky" ]; then
  run_bluesky
elif [ "$TARGET" = "ao3" ]; then
  run_ao3
else
  echo "用法: $0 [all|bluesky|ao3]" >&2
  exit 1
fi

echo "===== 采集完成 ====="
ls -lh data/corpus_*.json data/fanwork_ao3_*.json 2>/dev/null || true
