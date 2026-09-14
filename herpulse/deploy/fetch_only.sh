#!/usr/bin/env bash
# HerPulse 海外采集（只采集，不跑抽取/聚合/渲染）
# 产物：data/corpus_bluesky_<时间戳>.json + data/fanwork_ao3_<时间戳>.json + data/search_google_<时间戳>.json
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
# 若显式设置 HERPULSE_AO3_TAGS，则用指定 tag；否则从词表自动生成 high_signal 英文别名
# HERPULSE_AO3_LIMIT：AO3 tag 数量上限（0=不限，默认全部 high_signal）
# HERPULSE_TRENDS_TIMEFRAME：Google Trends 时间范围（默认 today 3-m）

STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="${1:-all}"   # all | bluesky | ao3 | trends

run_bluesky() {
  echo "[bluesky] 采集 ..."
  python src/fetchers.py --source bluesky --limit 30 \
    --out "data/corpus_bluesky_$STAMP.json"
}

run_ao3() {
  echo "[ao3] 采集 fanwork 信号 ..."
  if [ -n "${HERPULSE_AO3_TAGS:-}" ]; then
    python src/fetchers.py --source ao3 --tags "$HERPULSE_AO3_TAGS" \
      --out "data/fanwork_ao3_$STAMP.json"
  else
    python src/fetchers.py --source ao3 --tags-from-vocab data/seed_vocabulary.json \
      --ao3-limit "${HERPULSE_AO3_LIMIT:-0}" \
      --out "data/fanwork_ao3_$STAMP.json"
  fi
}

run_trends() {
  echo "[trends] 采集 Google Trends 搜索热度信号 ..."
  python src/fetchers.py --source trends --tags-from-vocab data/seed_vocabulary.json \
    --ao3-limit "${HERPULSE_TRENDS_LIMIT:-0}" \
    --trends-timeframe "${HERPULSE_TRENDS_TIMEFRAME:-today 3-m}" \
    --out "data/search_google_$STAMP.json"
}

echo "===== HerPulse 海外采集 $STAMP (mode=$TARGET) ====="

if [ "$TARGET" = "all" ]; then
  run_bluesky || echo "WARN: Bluesky 采集失败"
  run_ao3     || echo "WARN: AO3 采集失败"
  run_trends  || echo "WARN: Google Trends 采集失败"
elif [ "$TARGET" = "bluesky" ]; then
  run_bluesky
elif [ "$TARGET" = "ao3" ]; then
  run_ao3
elif [ "$TARGET" = "trends" ]; then
  run_trends
else
  echo "用法: $0 [all|bluesky|ao3|trends]" >&2
  exit 1
fi

echo "===== 采集完成 ====="
ls -lh data/corpus_*.json data/fanwork_ao3_*.json data/search_google_*.json 2>/dev/null || true
