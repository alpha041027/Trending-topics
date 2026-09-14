#!/usr/bin/env bash
# HerPulse 国内后半段：抽取 → 聚合 → 渲染看板（吃海外采集回来的 corpus）
#
# 用法：
#   bash deploy/run_local.sh data/corpus_reddit_20260914_120000.json   # 指定语料
#   bash deploy/run_local.sh                                           # 自动取最新 corpus_reddit_*.json
#
# 环境变量：
#   HERPULSE_LLM_BACKEND   openai（默认，需 API key）| mock（自测，不联网）
#   HERPULSE_LLM_API_KEY   DeepSeek API key
#   HERPULSE_LLM_BASE_URL  默认 https://api.deepseek.com
#   HERPULSE_LLM_MODEL     默认 deepseek-chat
#   HERPULSE_OUT_HTML      看板输出路径（默认 ../herpulse-dashboard-live.html）
set -euo pipefail

# ---- 定位项目根 ----
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

# ---- 输入语料 ----
CORPUS="${1:-${HERPULSE_CORPUS:-}}"
if [ -z "$CORPUS" ]; then
  CORPUS="$(ls -t data/corpus_*.json 2>/dev/null | head -1 || true)"
fi
if [ -z "$CORPUS" ] || [ ! -f "$CORPUS" ]; then
  echo "错误：未找到语料文件。用法：bash deploy/run_local.sh <corpus.json>" >&2
  echo "      或先把海外采集的 corpus 放到 data/ 目录（命名为 corpus_reddit_*.json）" >&2
  exit 1
fi
echo "输入语料：$CORPUS"

# ---- 后端配置 ----
BACKEND="${HERPULSE_LLM_BACKEND:-openai}"
BASE_URL="${HERPULSE_LLM_BASE_URL:-https://api.deepseek.com}"
MODEL="${HERPULSE_LLM_MODEL:-deepseek-chat}"
API_KEY="${HERPULSE_LLM_API_KEY:-}"
EXTRA_BODY="${HERPULSE_LLM_EXTRA_BODY:-}"
if [ -z "$EXTRA_BODY" ]; then
  EXTRA_BODY='{"thinking":{"type":"disabled"}}'
fi

if [ "$BACKEND" = "openai" ] && [ -z "$API_KEY" ]; then
  echo "错误：openai 后端需要 HERPULSE_LLM_API_KEY（自测可设 HERPULSE_LLM_BACKEND=mock）" >&2
  exit 1
fi

PRED="data/predictions_local.json"
DETAIL="data/extractions_detail_local.json"
DASH="data/dashboard_data_local.json"

echo "[1/3] 题材抽取（backend=$BACKEND）..."
if [ "$BACKEND" = "mock" ]; then
  python src/extract.py --backend mock --input "$CORPUS" \
    --out-pred "$PRED" --out-detail "$DETAIL"
else
  python src/extract.py --backend openai --input "$CORPUS" \
    --base-url "$BASE_URL" --model "$MODEL" --api-key "$API_KEY" \
    --extra-body "$EXTRA_BODY" --n-runs 1 \
    --out-pred "$PRED" --out-detail "$DETAIL"
fi

echo "[2/3] 热度聚合 ..."
FANWORK="$(ls -t data/fanwork_ao3_*.json 2>/dev/null | head -1 || true)"
FANWORK_ARG=""
if [ -n "$FANWORK" ]; then
  echo "  检测到 AO3 fanwork 信号：$FANWORK"
  FANWORK_ARG="--fanwork $FANWORK"
fi
SEARCH="$(ls -t data/search_google_*.json 2>/dev/null | head -1 || true)"
SEARCH_ARG=""
if [ -n "$SEARCH" ]; then
  echo "  检测到 Google Trends search 信号：$SEARCH"
  SEARCH_ARG="--search $SEARCH"
fi
python src/aggregate.py --corpus "$CORPUS" --detail "$DETAIL" \
  --vocab data/seed_vocabulary.json $FANWORK_ARG $SEARCH_ARG --out "$DASH"

echo "[3/3] 渲染看板 ..."
OUT_HTML="${HERPULSE_OUT_HTML:-$ROOT/../herpulse-dashboard-live.html}"
mkdir -p "$(dirname "$OUT_HTML")"
python src/build_dashboard.py --data "$DASH" --template templates/dashboard.html --out "$OUT_HTML"

echo "===== 完成：$OUT_HTML ====="
