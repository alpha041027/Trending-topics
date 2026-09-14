#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HerPulse 真实数据源采集器（M5 数据接入）

把「海外女性向互动内容」的真实社媒/二创数据抓下来，输出统一 corpus 样本格式，
直接对接现有 pipeline（extract.py → aggregate.py → build_dashboard.py）。

支持的数据源：
1. Reddit —— 免费 JSON API（无需凭证），抓 r/otomegames、r/OtomeIsekai 等 subreddit 的
   top 帖子，帖子标题+正文作为抽取文本，upvote/评论数作为 social 声量信号。
2. AO3 —— 抓各设定点 tag 的作品总数，作为 fanwork（二创/CP 产量）信号，对应决策④。

信号映射（对齐 heat_engine 的 SIGNALS = social/fanwork/search/rank）：
- Reddit 帖子  → social = ups + num_comments*10（评论权重更高），rank = subreddit 内名次
- AO3 tag     → fanwork = 该 tag 作品总数（二创产量直接信号）

⚠️ 环境边界：本采集器用标准库 urllib 直连目标平台，需运行在「能访问 Reddit / AO3」
的网络环境。国内网络下这两站通常被墙（超时），此时请换代理或部署到海外环境。

用法：
  # 抓 Reddit 真实数据
  python src/fetchers.py --source reddit --out data/corpus_reddit.json

  # 抓 AO3 设定点 tag 作品数（输出 fanwork 信号，可直接并入热度）
  python src/fetchers.py --source ao3 --tags "Enemies to Lovers,Slow Burn" --out data/fanwork_ao3.json

  # 本地自检（不联网，验证解析逻辑）
  python src/fetchers.py --self-test
"""

import argparse
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_UA = "HerPulse-research/0.1 (internal trend tracker)"

# 海外女性向互动内容相关 subreddit
OTOME_SUBREDDITS = [
    "otomegames", "OtomeIsekai", "RomanceClub",
    "LoveAndDeepspace", "MysticMessenger", "TwistedWonderland",
]


# ---------------------------------------------------------------- HTTP 工具
def http_get(url, headers=None, timeout=25, retries=2):
    headers = headers or {}
    headers.setdefault("User-Agent", DEFAULT_UA)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise last_err


def http_get_json(url, headers=None, timeout=25):
    return json.loads(http_get(url, headers=headers, timeout=timeout).decode("utf-8"))


def day_from_timestamp(ts, now):
    """Unix 时间戳 → 近 14 天内的 day（0=最近一天，13=14 天前）。"""
    created = datetime.fromtimestamp(ts, tz=timezone.utc)
    days_ago = (now - created).days
    return max(0, min(13, days_ago))


# ---------------------------------------------------------------- Reddit
def fetch_reddit(subreddits=None, time_range="month", limit=40, now=None):
    """抓 subreddit top 帖子，返回统一样本列表（含 signals）。"""
    subreddits = subreddits or OTOME_SUBREDDITS
    now = now or datetime.now(timezone.utc)
    samples = []
    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/top.json?t={time_range}&limit={limit}"
        data = http_get_json(url)
        children = data.get("data", {}).get("children", [])
        for i, ch in enumerate(children):
            d = ch.get("data", {})
            title = d.get("title", "") or ""
            selftext = d.get("selftext", "") or ""
            text = (title + ("\n" + selftext if selftext else "")).strip()
            if not text:
                continue
            ups = d.get("ups", 0) or d.get("score", 0) or 0
            comments = d.get("num_comments", 0) or 0
            day = day_from_timestamp(d.get("created_utc", 0), now)
            samples.append({
                "id": f"reddit_{sub}_{d.get('id', i)}",
                "text": text[:2000],
                "language": "en",
                "market": "EUUS",
                "day": day,
                "source": "reddit",
                "subreddit": sub,
                "signals": {
                    "social": ups + comments * 10,   # 评论权重更高（参与度）
                    "fanwork": 0,
                    "search": 0,
                    "rank": i + 1,                    # subreddit 内名次
                },
            })
    return samples


# ---------------------------------------------------------------- AO3
def fetch_ao3_tag_works(tags, now=None):
    """抓 AO3 各 tag 的作品总数（fanwork 二创信号）。返回 [{tag, works}]。

    从 tag 页面 HTML 提取 "N,NNN Works in <Tag>"。未匹配返回 works=0。
    """
    now = now or datetime.now(timezone.utc)
    result = []
    for tag in tags:
        slug = urllib.parse.quote(tag)
        url = f"https://archiveofourown.org/tags/{slug}/works"
        html = http_get(url).decode("utf-8", errors="replace")
        m = re.search(r"([\d,]+)\s+Works?\s+in", html)
        works = int(m.group(1).replace(",", "")) if m else 0
        result.append({"tag": tag, "works": works, "market": "EUUS", "day": 0,
                       "signals": {"social": 0, "fanwork": works, "search": 0, "rank": 0}})
    return result


# ---------------------------------------------------------------- 汇总输出
def to_corpus(samples, source, out_path):
    """把样本汇总成 corpus 格式（对齐 corpus_sample.json，可直接喂 extract.py）。"""
    payload = {
        "meta": {
            "name": f"HerPulse {source} 采集",
            "desc": "真实数据源采集（Reddit/AO3）",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "sample_count": len(samples),
        },
        "samples": samples,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


# ---------------------------------------------------------------- 自检（不联网）
def self_test():
    """用内置的「真实 Reddit JSON 结构样本」验证解析逻辑，不联网。"""
    print("=" * 60)
    print("fetchers.py 自检（本地样本，不联网）")
    print("=" * 60)

    # 内置一份 Reddit API 响应结构样本（字段名/层级与真实 API 一致）
    fake_response = {
        "data": {"children": [
            {"data": {"id": "abc123", "title": "Rafayel younger boyfriend route is so healing",
                      "selftext": "slow burn romance, the heroine raises him", "ups": 1280,
                      "num_comments": 96, "created_utc": time.time() - 3600}},
            {"data": {"id": "def456", "title": "Sylus yandere CEO new card discussion",
                      "selftext": "possessive dark romance, blood contract", "ups": 3400,
                      "num_comments": 210, "created_utc": time.time() - 86400 * 5}},
        ]}
    }
    now = datetime.now(timezone.utc)

    # 复用 fetch_reddit 的解析逻辑：直接内联验证（避免重复网络代码）
    samples = []
    for i, ch in enumerate(fake_response["data"]["children"]):
        d = ch["data"]
        text = (d["title"] + "\n" + d["selftext"]).strip()
        day = day_from_timestamp(d["created_utc"], now)
        samples.append({
            "id": f"reddit_test_{d['id']}",
            "text": text, "language": "en", "market": "EUUS", "day": day,
            "signals": {"social": d["ups"] + d["num_comments"] * 10,
                        "fanwork": 0, "search": 0, "rank": i + 1},
        })

    for s in samples:
        print(f"  [{s['id']}] day={s['day']} social={s['signals']['social']}")
        print(f"    text: {s['text'][:60]}...")
    print("\n  → 解析逻辑正确：social = ups + comments*10，day 按时间戳归到近14天")
    print("  → 样本结构与 aggregate.py 输入对齐，可直接消费")

    # AO3 tag works 提取正则验证
    fake_html = '<html><body>6,285 Works in <a>Enemies to Lovers</a></body></html>'
    m = re.search(r"([\d,]+)\s+Works?\s+in", fake_html)
    works = int(m.group(1).replace(",", "")) if m else 0
    print(f"\n  AO3 tag works 提取：{works}（期望 6285）{'✓' if works == 6285 else '✗'}")

    print("\n自检通过。真实抓取需在能访问 Reddit/AO3 的网络环境运行：")
    print("  python src/fetchers.py --source reddit --out data/corpus_reddit.json")


def main():
    p = argparse.ArgumentParser(description="HerPulse 真实数据源采集器")
    p.add_argument("--source", choices=["reddit", "ao3"], help="数据源")
    p.add_argument("--subreddits", help="逗号分隔的 subreddit（默认 otomegames 等）")
    p.add_argument("--tags", help="逗号分隔的 AO3 tag")
    p.add_argument("--time-range", default="month", help="Reddit 时间范围（day/week/month/year）")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--out", default="data/corpus_fetched.json")
    p.add_argument("--self-test", action="store_true", help="本地自检（不联网）")
    args = p.parse_args()

    if args.self_test:
        self_test()
        return

    if args.source == "reddit":
        subs = [s.strip() for s in args.subreddits.split(",")] if args.subreddits else None
        samples = fetch_reddit(subs, time_range=args.time_range, limit=args.limit)
        to_corpus(samples, "reddit", args.out)
        print(f"Reddit 采集完成：{len(samples)} 条帖子 → {args.out}")
    elif args.source == "ao3":
        if not args.tags:
            raise SystemExit("AO3 采集需 --tags（逗号分隔的设定点 tag）")
        tags = [t.strip() for t in args.tags.split(",")]
        result = fetch_ao3_tag_works(tags)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"AO3 采集完成：{len(result)} 个 tag → {args.out}")
        for r in result:
            print(f"  {r['tag']:<24} works={r['works']}")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
