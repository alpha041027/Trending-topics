#!/usr/bin/env python3
"""HerPulse 本地采集 + 自动推送到仓库（触发 GitHub Actions 后半段）"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 自动走本地代理（Clash/V2Ray），不影响已设置的环境变量
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7897")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7897")

ROOT = Path(__file__).parent.parent
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

REDDIT_SUBS = "otomegames,OtomeIsekai,RomanceClub,LoveAndDeepspace,MysticMessenger,TwistedWonderland"
AO3_TAGS = "Enemies to Lovers,Slow Burn,Yandere,Boss and Employee,Office Romance,Reincarnation"


def run(cmd, cwd=None, check=True):
    """运行命令，失败时根据 check 决定是否退出。"""
    cwd = cwd or ROOT
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if check and result.returncode != 0:
        print(f"[ERROR] Command failed: {' '.join(cmd)}")
        sys.exit(1)
    return result.returncode


def main():
    print("===== HerPulse Daily Fetch & Push =====\n")

    # 1. Fetch Reddit（本地有代理，应该成功；失败不阻断 AO3）
    print("[1/3] Fetching Reddit ...")
    reddit_ok = run([
        sys.executable, "src/fetchers.py",
        "--source", "reddit",
        "--subreddits", REDDIT_SUBS,
        "--time-range", "month",
        "--limit", "40",
        "--out", f"data/corpus_reddit_{STAMP}.json",
    ], check=False) == 0

    if not reddit_ok:
        print("[WARN] Reddit fetch failed, continuing with AO3 ...\n")

    # 2. Fetch AO3
    print("[2/3] Fetching AO3 ...")
    ao3_ok = run([
        sys.executable, "src/fetchers.py",
        "--source", "ao3",
        "--tags", AO3_TAGS,
        "--out", f"data/fanwork_ao3_{STAMP}.json",
    ], check=False) == 0

    if not ao3_ok:
        print("[WARN] AO3 fetch failed\n")

    # 如果没有 corpus，不推送
    corpus_files = list(ROOT.glob("data/corpus_reddit_*.json"))
    if not corpus_files:
        print("[ERROR] No corpus generated. Nothing to push.")
        input("Press Enter to exit ...")
        sys.exit(1)

    # 3. Git push
    print("\n[3/3] Pushing to GitHub ...")
    run(["git", "add", "data/"])
    run(["git", "commit", "-m", f"data: {STAMP}"], check=False)
    run(["git", "push"])

    print("\n===== Done =====")
    print("Wait 3-5 minutes and visit:")
    print("  https://alpha041027.github.io/Trending-topics/")
    input("\nPress Enter to exit ...")


if __name__ == "__main__":
    main()
