#!/usr/bin/env bash
# HerPulse 海外采集（只采集，不跑抽取/聚合/渲染）
# 在 GitHub Actions 海外 runner 或海外 VPS 上运行。
# 产物：data/corpus_reddit_<时间戳>.json + data/fanwork_ao3_<时间戳>.json
set -euo pipefail

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
REDDIT_SUBS="${HERPULSE_REDDIT_SUBS:-otomegames,OtomeIsekai,RomanceClub,LoveAndDeepspace,MysticMessenger,TwistedWonderland}"
AO3_TAGS="${HERPULSE_AO3_TAGS:-Enemies to Lovers,Slow Burn,Yandere,Boss and Employee,Office Romance,Reincarnation}"

STAMP="$(date +%Y%m%d_%H%M%S)"
echo "===== HerPulse 海外采集 $STAMP ====="

echo "[1/2] 采集 Reddit ..."
python src/fetchers.py --source reddit --subreddits "$REDDIT_SUBS" \
  --time-range month --limit 40 --out "data/corpus_reddit_$STAMP.json"

echo "[2/2] 采集 AO3 fanwork 信号 ..."
python src/fetchers.py --source ao3 --tags "$AO3_TAGS" \
  --out "data/fanwork_ao3_$STAMP.json"

echo "===== 采集完成 ====="
echo "  corpus: data/corpus_reddit_$STAMP.json"
echo "  fanwork: data/fanwork_ao3_$STAMP.json"
