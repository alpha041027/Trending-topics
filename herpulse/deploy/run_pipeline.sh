#!/usr/bin/env bash
# HerPulse 全链路 pipeline：采集 -> 抽取 -> 聚合 -> 渲染看板
#
# 关键：只有「采集」这一步必须在海外网络运行（Reddit/AO3 国内被墙）。
# 抽取(DeepSeek API)、聚合、渲染在国内即可跑；整套放海外也能跑。
#
# 用法：
#   export HERPULSE_LLM_API_KEY="sk-xxxx"
#   bash deploy/run_pipeline.sh
#
# 可选环境变量：
#   HERPULSE_LLM_BASE_URL   默认 https://api.deepseek.com
#   HERPULSE_LLM_MODEL      默认 deepseek-chat
#   HERPULSE_LLM_EXTRA_BODY 默认 {"thinking":{"type":"disabled"}}
#   HERPULSE_REDDIT_SUBS    默认 otomegames,OtomeIsekai,...（逗号分隔）
#   HERPULSE_AO3_TAGS       默认 Enemies to Lovers,Slow Burn,...（逗号分隔）
#   HERPULSE_OUT_HTML       默认 <root>/../herpulse-dashboard-live.html

set -euo pipefail

# ---- 定位项目根（含 src/ 的目录），兼容本地(deploy/) 与容器(/app/) ----
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

# ---- 配置 ----
API_KEY="${HERPULSE_LLM_API_KEY:-}"
BASE_URL="${HERPULSE_LLM_BASE_URL:-https://api.deepseek.com}"
MODEL="${HERPULSE_LLM_MODEL:-deepseek-chat}"
EXTRA_BODY="${HERPULSE_LLM_EXTRA_BODY:-}"
if [ -z "$EXTRA_BODY" ]; then
  EXTRA_BODY='{"thinking":{"type":"disabled"}}'
fi
REDDIT_SUBS="${HERPULSE_REDDIT_SUBS:-otomegames,OtomeIsekai,RomanceClub,LoveAndDeepspace,MysticMessenger,TwistedWonderland}"
AO3_TAGS="${HERPULSE_AO3_TAGS:-Enemies to Lovers,Slow Burn,Yandere,Boss and Employee,Office Romance}"
OUT_HTML="${HERPULSE_OUT_HTML:-$ROOT/../herpulse-dashboard-live.html}"

if [ -z "$API_KEY" ]; then
  echo "错误：请设置环境变量 HERPULSE_LLM_API_KEY（DeepSeek API key）" >&2
  exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
echo "===== HerPulse pipeline $STAMP ====="
echo "  ROOT=$ROOT"

# 1) 采集（必须在海外网络运行）
echo "[1/4] 采集 Reddit ..."
python src/fetchers.py --source reddit --subreddits "$REDDIT_SUBS" \
  --time-range month --limit 40 --out "data/corpus_reddit_$STAMP.json"

echo "[1/4] 采集 AO3 fanwork 信号 ..."
python src/fetchers.py --source ao3 --tags "$AO3_TAGS" \
  --out "data/fanwork_ao3_$STAMP.json"

# 2) 抽取（DeepSeek 真实 LLM）
echo "[2/4] 题材抽取 ..."
python src/extract.py --backend openai \
  --input "data/corpus_reddit_$STAMP.json" \
  --base-url "$BASE_URL" --model "$MODEL" --api-key "$API_KEY" \
  --extra-body "$EXTRA_BODY" \
  --n-runs 1 \
  --out-pred "data/predictions_$STAMP.json" \
  --out-detail "data/extractions_detail_$STAMP.json"

# 3) 聚合
# 注：AO3 fanwork 信号(fanwork_ao3_*.json)目前采集但尚未并入 aggregate 热度，
#     待 aggregate.py 增加 --fanwork 参数后接入（见 README「已知缺口」）。
echo "[3/4] 热度聚合 ..."
python src/aggregate.py \
  --corpus "data/corpus_reddit_$STAMP.json" \
  --detail "data/extractions_detail_$STAMP.json" \
  --vocab data/seed_vocabulary.json \
  --out "data/dashboard_data_$STAMP.json"

# 4) 渲染看板
echo "[4/4] 渲染看板 ..."
mkdir -p "$(dirname "$OUT_HTML")"
python src/build_dashboard.py \
  --data "data/dashboard_data_$STAMP.json" \
  --template templates/dashboard.html \
  --out "$OUT_HTML"

echo "===== 完成 ====="
ls -la "$OUT_HTML"
