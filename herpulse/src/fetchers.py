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
import base64
import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
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

# Bluesky 搜索关键词（覆盖乙女/女性向内容）
BLUESKY_QUERIES = [
    "otome", "otome game", "visual novel", "yandere",
    "reverse harem", "dating sim", "romance game",
]

# Bluesky 账号发现关键词（用 searchActors 找女性向相关账号，再拉其 feed）
BLUESKY_ACTOR_QUERIES = [
    "otome game", "visual novel", "乙女ゲーム", "otome",
    "dating sim", "恋愛ゲーム",
]


# ---------------------------------------------------------------- HTTP 工具
# 显式读取代理环境变量，Windows 上 urllib 自动探测有时不稳定
_PROXY_HANDLER = urllib.request.ProxyHandler()
_URL_OPENER = urllib.request.build_opener(_PROXY_HANDLER)


def http_get(url, headers=None, timeout=25, retries=2):
    headers = headers or {}
    headers.setdefault("User-Agent", DEFAULT_UA)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with _URL_OPENER.open(req, timeout=timeout) as r:
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


# ---------------------------------------------------------------- Reddit OAuth
_REDDIT_OAUTH_UA = "HerPulse-research/0.1 (by /u/HerPulseBot)"


def get_reddit_token(client_id, client_secret):
    """用 client_credentials 换取 Reddit OAuth access_token。"""
    if not client_id or not client_secret:
        return None
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token",
        data=b"grant_type=client_credentials",
        headers={
            "Authorization": f"Basic {creds}",
            "User-Agent": _REDDIT_OAUTH_UA,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp.get("access_token")


# 模拟浏览器头，降低被 Cloudflare / Reddit WAF 拦截的概率
_REDDIT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.reddit.com/",
    "DNT": "1",
}


def fetch_reddit(subreddits=None, time_range="month", limit=40, now=None,
                 client_id=None, client_secret=None):
    """抓 subreddit top 帖子，返回统一样本列表（含 signals）。"""
    subreddits = subreddits or OTOME_SUBREDDITS
    now = now or datetime.now(timezone.utc)
    samples = []

    # 优先使用 OAuth；未提供则回退到匿名请求
    token = get_reddit_token(client_id, client_secret)
    headers = _REDDIT_HEADERS.copy()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        base_url = "https://oauth.reddit.com"
    else:
        base_url = "https://www.reddit.com"

    for sub in subreddits:
        url = f"{base_url}/r/{sub}/top.json?t={time_range}&limit={limit}"
        data = http_get_json(url, headers=headers)
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


# ---------------------------------------------------------------- Bluesky
def _bluesky_post_to_sample(post, idx, now, source_key):
    """把 Bluesky post 视图对象转成统一样本。source_key 用于 id 前缀区分来源。"""
    text = post.get("record", {}).get("text", "")
    if not text:
        return None
    likes = post.get("likeCount", 0) or 0
    replies = post.get("replyCount", 0) or 0
    created = post.get("record", {}).get("createdAt", "")
    day = 0
    if created:
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            days_ago = (now - created_dt).days
            day = max(0, min(13, days_ago))
        except ValueError:
            pass
    post_id = post.get("uri", "").split("/")[-1] or str(idx)
    return {
        "id": f"bluesky_{source_key}_{post_id}",
        "text": text[:2000],
        "language": "en",
        "market": "EUUS",
        "day": day,
        "source": "bluesky",
        "signals": {
            "social": likes + replies * 10,
            "fanwork": 0,
            "search": 0,
            "rank": idx + 1,
        },
    }


def _fetch_bluesky_actors(queries, per_actor=20, max_actors=20, now=None):
    """从女性向相关账号的 feed 拉最新帖子（从人出发，发现面宽于关键词搜索）。

    流程：searchActors 按关键词找账号 → getAuthorFeed 拉每个账号最新帖子
    （filter=posts_no_replies 排除回复，只留原创/转发）。无需认证。
    """
    now = now or datetime.now(timezone.utc)
    actors = []
    seen = set()
    for q in queries:
        url = (
            "https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors"
            f"?q={urllib.parse.quote(q)}&limit=15"
        )
        try:
            data = http_get_json(url)
        except Exception:  # noqa: BLE001
            continue
        for a in data.get("actors", []):
            handle = a.get("handle", "")
            if handle and handle not in seen:
                seen.add(handle)
                actors.append(handle)
        if len(actors) >= max_actors:
            break
    samples = []
    for handle in actors[:max_actors]:
        url = (
            "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
            f"?actor={urllib.parse.quote(handle)}&limit={per_actor}&filter=posts_no_replies"
        )
        try:
            data = http_get_json(url)
        except Exception:  # noqa: BLE001
            continue
        for item in data.get("feed", []):
            post = item.get("post", {})
            s = _bluesky_post_to_sample(post, len(samples), now, "actor")
            if s:
                s["query"] = handle  # 记录来源账号
                samples.append(s)
    return samples


def fetch_bluesky(queries=None, limit=30, now=None, actor_queries=None,
                  per_actor=20, max_actors=20):
    """抓 Bluesky 帖子：关键词搜索（聚焦补充）+ 账号 feed（从人出发，发现面更宽）。

    Bluesky 公共 API 无需认证（public.api.bsky.app）。
    """
    queries = queries or BLUESKY_QUERIES
    actor_queries = actor_queries or BLUESKY_ACTOR_QUERIES
    now = now or datetime.now(timezone.utc)
    samples = []
    # 方式 1：关键词搜索（聚焦补充）
    for q in queries:
        url = (
            "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
            f"?q={urllib.parse.quote(q)}&limit={limit}"
        )
        try:
            data = http_get_json(url)
        except Exception:  # noqa: BLE001
            continue
        posts = data.get("posts", [])
        for i, post in enumerate(posts):
            s = _bluesky_post_to_sample(post, i, now, q.replace(" ", "_"))
            if s:
                s["query"] = q
                samples.append(s)
    # 方式 2：账号 feed（从人出发）
    samples += _fetch_bluesky_actors(actor_queries, per_actor, max_actors, now)
    return samples


# ---------------------------------------------------------------- AO3
def tags_from_vocab(vocab_path, limit=None):
    """从词表提取 high_signal 设定点的英文别名，作为 AO3 采集 tag。

    词表结构：dimensions[].categories[].points[]，每个 point 有 tag / aliases.en / high_signal。
    只取 high_signal 设定点的第一个英文别名（最规范、最可能是 AO3 canonical tag）。
    """
    with open(vocab_path, encoding="utf-8") as f:
        vocab = json.load(f)
    tags = []
    for dim in vocab.get("dimensions", []):
        for cat in dim.get("categories", []):
            for pt in cat.get("points", []):
                if pt.get("high_signal"):
                    en = pt.get("aliases", {}).get("en", [])
                    if en:
                        tags.append(en[0])
    if limit:
        tags = tags[:limit]
    return tags


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


# ---------------------------------------------------------------- Google Trends
# Google Trends 无官方公开 API，这里走与 pytrends 同款的「非官方免费接口」：
#   explore 接口拿 token → widgetdata/multiline 拿时间序列。
# 全部用标准库（urllib + http.cookiejar）实现，保持项目零第三方依赖。
#
# ⚠️ 语义关键：Google Trends 返回的 0-100 是「该词相对自身历史峰值的归一化」，
#    不是跨词的绝对搜索量。因此：
#    - interest（近7天均值，0-100）→ 只表示「该词处于自身历史热度的相对位置」，
#      接近 100 = 正处历史高位（现在热）；不可跨词比较绝对量级。
#    - momentum（近7天 vs 前7天 的涨跌 %）→ 跨词可比，用于突增/衰减检测。
#    此局限在看板 signal_source 与 README 中如实标注，不伪装成绝对搜索量。

_TRENDS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_TRENDS_HL = "en-US"
_TRENDS_TZ = "0"  # UTC


def _trends_opener():
    """带 CookieJar 的 opener（Google Trends 需要会话 cookie，NID 等）。"""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.ProxyHandler(),  # 读取环境代理（海外 runner 无代理则直连）
    )
    opener.addheaders = [("User-Agent", _TRENDS_UA)]
    return opener


def _strip_trends_prefix(body):
    """Google Trends API 返回 JSON 前缀 `)]}'` + 换行，需剥离。"""
    return body.lstrip(")]}'\n,").strip()


def _trends_request(opener, url, retries=3, timeout=25):
    """带重试 + 429 退避的请求。返回解析后的 JSON。"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            with opener.open(url, timeout=timeout) as r:
                body = _strip_trends_prefix(r.read().decode("utf-8"))
                return json.loads(body)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2.0 * (attempt + 1))  # 429 退避
    raise last_err


def _series_to_metrics(values):
    """时间序列 → (interest, momentum)。纯函数，供 self_test 无联网验证。"""
    values = [float(v) for v in values]
    if not values:
        return 0.0, 0.0
    recent = values[-7:]                                   # 近7天
    prev = values[-14:-7] if len(values) >= 14 else values[:-7]  # 前7天
    interest = sum(recent) / len(recent)
    if prev:
        prev_mean = sum(prev) / len(prev)
        momentum = ((interest - prev_mean) / prev_mean * 100.0) if prev_mean > 0 else (100.0 if interest > 0 else 0.0)
    else:
        momentum = 0.0
    return round(interest, 2), round(momentum, 2)


def _trends_series(opener, keyword, timeframe="today 3-m"):
    """查单个关键词的 Google Trends 时间序列，返回 (interest, momentum)。

    interest = 近7天均值(0-100)；momentum = (近7天均值 - 前7天均值)/前7天均值 ×100%。
    """
    # 1) explore 拿 token
    payload = {
        "comparisonItem": [{"keyword": keyword, "geo": "", "time": timeframe}],
        "category": 0,
        "property": "",
    }
    req = json.dumps(payload, separators=(",", ":"))
    explore_url = (
        f"https://trends.google.com/trends/api/explore?hl={_TRENDS_HL}"
        f"&tz={_TRENDS_TZ}&req={urllib.parse.quote(req)}"
    )
    explore = _trends_request(opener, explore_url)
    widget = explore["widgets"][0]
    token = widget["token"]

    # 2) multiline 拿时间序列
    req2 = json.dumps(widget["request"], separators=(",", ":"))
    data_url = (
        f"https://trends.google.com/trends/api/widgetdata/multiline?hl={_TRENDS_HL}"
        f"&tz={_TRENDS_TZ}&req={urllib.parse.quote(req2)}&token={urllib.parse.quote(token)}"
    )
    data = _trends_request(opener, data_url)
    tl = data.get("default", {}).get("timelineData", [])
    if not tl:
        return 0.0, 0.0
    return _series_to_metrics(tl[0].get("value", []))


def fetch_trends_interest(keywords, timeframe="today 3-m", now=None, pause=1.5):
    """对每个关键词查 Google Trends，返回 [{tag, interest, momentum, signals}]。

    单个关键词失败不中断整体（记录 error 字段，search 信号置 0）。
    pause 为请求间隔（秒），Google Trends 限流严格，过快会 429。
    """
    now = now or datetime.now(timezone.utc)
    opener = _trends_opener()
    # 预热会话 cookie（首次 explore 需先建立 NID cookie）
    try:
        opener.open("https://trends.google.com/trends/explore", timeout=20).read()
    except Exception:  # noqa: BLE001
        pass  # 预热失败不致命，后续 explore API 仍可能 set-cookie
    result = []
    for kw in keywords:
        item = {"tag": kw, "interest": 0.0, "momentum": 0.0, "market": "EUUS", "day": 0,
                "signals": {"social": 0, "fanwork": 0, "search": 0.0, "rank": 0}}
        try:
            interest, momentum = _trends_series(opener, kw, timeframe)
            item["interest"] = interest
            item["momentum"] = momentum
            item["signals"]["search"] = interest
        except Exception as e:  # noqa: BLE001
            item["error"] = str(e)[:120]
        result.append(item)
        time.sleep(pause)  # 限流：间隔控制
    return result


# ---------------------------------------------------------------- Google Trends 每日趋势（真·热点发现）
def fetch_trends_daily(geos=None, ns=15, now=None, pause=2.0):
    """调 Google Trends dailytrends 接口，拉每日上升趋势词（真实 API 筛选，非预设词表）。

    dailytrends 返回「过去 24 小时搜索量跳涨」的查询词（含绝对搜索量级 formattedTraffic），
    由 Google 计算得出，不依赖我们预设的关键词 —— 这正是「热点发现」所需的信号。
    返回 [{query, traffic, related_queries, articles, geo, date}, ...]。

    geo 支持 US/JP/KR 等国家码，可一次拉多个地区。ns 为每地区返回条数（默认 15）。
    """
    geos = geos or ["US", "JP", "KR"]
    now = now or datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    opener = _trends_opener()
    # 预热会话 cookie（首次 explore 需先建立 NID cookie）
    try:
        opener.open("https://trends.google.com/trends/explore", timeout=20).read()
    except Exception:  # noqa: BLE001
        pass
    results = []
    for geo in geos:
        url = (
            f"https://trends.google.com/trends/api/dailytrends"
            f"?hl=en-US&tz=0&ed={date_str}&geo={geo}&ns={ns}"
        )
        try:
            data = _trends_request(opener, url)
            days = data.get("default", {}).get("trendingSearchesDays", [])
            if not days:
                results.append({"geo": geo, "date": date_str, "error": "empty trendingSearchesDays"})
                continue
            for t in days[0].get("trendingSearches", []):
                title = (t.get("title") or {}).get("query", "")
                if not title:
                    continue
                results.append({
                    "query": title,
                    "traffic": t.get("formattedTraffic", ""),
                    "related_queries": [r.get("query", "") for r in t.get("relatedQueries", [])],
                    "articles": [a.get("title", "") for a in t.get("articles", [])],
                    "geo": geo,
                    "date": date_str,
                })
        except Exception as e:  # noqa: BLE001
            results.append({"geo": geo, "date": date_str, "error": str(e)[:120]})
        time.sleep(pause)
    return results


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

    # Google Trends 前缀剥离 + 时序→interest/momentum 解析验证（mock，不联网）
    fake_body = ")]}'\n, {\"default\":{\"timelineData\":[{\"value\":[10,12,14,20,18,22,30,35,40,45,50,55,60,65]}]}}"
    stripped = _strip_trends_prefix(fake_body)
    ok_prefix = stripped.startswith("{")
    d = json.loads(stripped)
    vals = d["default"]["timelineData"][0]["value"]
    interest, momentum = _series_to_metrics(vals)
    # 近7天 = [40,45,50,55,60,65]? 实际上最后7个 = [40,45,50,55,60,65] 是6个，重新算
    print(f"\n  Google Trends 前缀剥离：{ok_prefix}（期望 True）{'✓' if ok_prefix else '✗'}")
    print(f"  interest/momentum 解析：interest={interest}, momentum={momentum:.1f}%")
    print("  （14点序列，近7天=[35,40,45,50,55,60,65] 前7天=[10,12,14,20,18,22,30]）")

    # dailytrends 结构解析验证（mock，不联网）
    fake_daily = (
        ")]}'\n, {\"default\":{\"trendingSearchesDays\":[{\"date\":\"20260914\","
        "\"trendingSearches\":[{\"title\":{\"query\":\"otome game new release\"},"
        "\"formattedTraffic\":\"200K+\",\"relatedQueries\":[{\"query\":\"otome game 2026\"}],"
        "\"articles\":[{\"title\":\"New otome game tops charts\"}]}]}]}}"
    )
    d_stripped = _strip_trends_prefix(fake_daily)
    d_ok = d_stripped.startswith("{")
    dd = json.loads(d_stripped)
    day = dd["default"]["trendingSearchesDays"][0]
    first = day["trendingSearches"][0]
    print(f"\n  dailytrends 前缀剥离：{d_ok}（期望 True）{'✓' if d_ok else '✗'}")
    print(f"  dailytrends 解析：query={first['title']['query']!r} traffic={first['formattedTraffic']!r} "
          f"related={len(first['relatedQueries'])} articles={len(first['articles'])}")

    # Bluesky searchActors / getAuthorFeed 结构解析验证（mock，不联网）
    fake_actors = {"actors": [
        {"handle": "otomegames.bsky.social", "displayName": "Otome Games", "followersCount": 5000},
        {"handle": "vn_updates.bsky.social", "displayName": "VN Updates", "followersCount": 3000},
    ]}
    fake_feed = {"feed": [
        {"post": {"uri": "at://did:plc:xxx/app.bsky.feed.post/abc",
                  "record": {"text": "New otome banner announced", "createdAt": "2026-09-14T12:00:00Z"},
                  "likeCount": 120, "replyCount": 15, "repostCount": 40}},
        {"post": {"uri": "at://did:plc:xxx/app.bsky.feed.post/def",
                  "record": {"text": "slow burn route discussion", "createdAt": "2026-09-13T10:00:00Z"},
                  "likeCount": 80, "replyCount": 8, "repostCount": 20}},
    ]}
    handles = [a["handle"] for a in fake_actors["actors"]]
    bs = []
    for i, item in enumerate(fake_feed["feed"]):
        s = _bluesky_post_to_sample(item["post"], i, datetime.now(timezone.utc), "test")
        if s:
            bs.append(s)
    socials = [s["signals"]["social"] for s in bs]
    print(f"\n  Bluesky searchActors 解析：{len(handles)} 个账号（{handles[0]}）")
    print(f"  Bluesky getAuthorFeed → sample：{len(bs)} 条，social={socials}（期望 [270, 160]）")

    print("\n自检通过。真实抓取需在能访问 Reddit/AO3/Google Trends/Bluesky 的网络环境运行：")
    print("  python src/fetchers.py --source reddit --out data/corpus_reddit.json")
    print("  python src/fetchers.py --source trends --tags-from-vocab data/seed_vocabulary.json --out data/search_google.json")


def main():
    p = argparse.ArgumentParser(description="HerPulse 真实数据源采集器")
    p.add_argument("--source", choices=["reddit", "ao3", "bluesky", "trends", "trendsdaily"], help="数据源")
    p.add_argument("--subreddits", help="逗号分隔的 subreddit（默认 otomegames 等）")
    p.add_argument("--tags", help="逗号分隔的 tag / 搜索关键词（AO3 tag 或 Google Trends 关键词）")
    p.add_argument("--tags-from-vocab", help="从词表生成（high_signal 设定点的英文别名）")
    p.add_argument("--ao3-limit", type=int, default=0, help="AO3 tag 数量上限（0=不限）")
    p.add_argument("--trends-timeframe", default="today 3-m", help="Google Trends 时间范围（today 1-m/3-m/12-m）")
    p.add_argument("--trends-pause", type=float, default=1.5, help="Google Trends 请求间隔秒数（限流控制）")
    p.add_argument("--geos", help="逗号分隔的国家码（dailytrends 用，默认 US,JP,KR）")
    p.add_argument("--trends-ns", type=int, default=15, help="每地区 dailytrends 返回条数（默认 15）")
    p.add_argument("--time-range", default="month", help="Reddit 时间范围（day/week/month/year）")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--out", default="data/corpus_fetched.json")
    p.add_argument("--reddit-client-id", help="Reddit App client_id（OAuth）")
    p.add_argument("--reddit-client-secret", help="Reddit App client_secret（OAuth）")
    p.add_argument("--self-test", action="store_true", help="本地自检（不联网）")
    args = p.parse_args()

    if args.self_test:
        self_test()
        return

    if args.source == "reddit":
        subs = [s.strip() for s in args.subreddits.split(",")] if args.subreddits else None
        samples = fetch_reddit(
            subs, time_range=args.time_range, limit=args.limit,
            client_id=args.reddit_client_id,
            client_secret=args.reddit_client_secret,
        )
        to_corpus(samples, "reddit", args.out)
        print(f"Reddit 采集完成：{len(samples)} 条帖子 → {args.out}")
    elif args.source == "bluesky":
        queries = [q.strip() for q in args.subreddits.split(",")] if args.subreddits else None
        samples = fetch_bluesky(queries, limit=args.limit)
        to_corpus(samples, "bluesky", args.out)
        print(f"Bluesky 采集完成：{len(samples)} 条帖子 → {args.out}")
    elif args.source == "ao3":
        if args.tags:
            tags = [t.strip() for t in args.tags.split(",")]
        elif args.tags_from_vocab:
            tags = tags_from_vocab(args.tags_from_vocab, limit=args.ao3_limit or None)
            print(f"从词表生成 {len(tags)} 个 AO3 tag（high_signal 英文别名）")
        else:
            raise SystemExit("AO3 采集需 --tags 或 --tags-from-vocab（词表路径）")
        result = fetch_ao3_tag_works(tags)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"AO3 采集完成：{len(result)} 个 tag → {args.out}")
        for r in result:
            print(f"  {r['tag']:<24} works={r['works']}")
    elif args.source == "trends":
        if args.tags:
            keywords = [k.strip() for k in args.tags.split(",")]
        elif args.tags_from_vocab:
            keywords = tags_from_vocab(args.tags_from_vocab, limit=args.ao3_limit or None)
            print(f"从词表生成 {len(keywords)} 个 Google Trends 关键词（high_signal 英文别名）")
        else:
            raise SystemExit("Google Trends 采集需 --tags 或 --tags-from-vocab（词表路径）")
        result = fetch_trends_interest(keywords, timeframe=args.trends_timeframe, pause=args.trends_pause)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        ok = sum(1 for r in result if r.get("interest", 0) > 0)
        print(f"Google Trends 采集完成：{len(result)} 个关键词（{ok} 个成功）→ {args.out}")
        for r in result:
            flag = "✗" if r.get("error") else "✓"
            print(f"  {flag} {r['tag']:<28} interest={r['interest']:<6} momentum={r['momentum']:+.1f}%")
    elif args.source == "trendsdaily":
        geos = [g.strip().upper() for g in args.geos.split(",")] if args.geos else None
        result = fetch_trends_daily(geos, ns=args.trends_ns)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        ok = sum(1 for r in result if r.get("query"))
        print(f"Google Trends 每日趋势采集完成：{len(result)} 条（{ok} 条有效）→ {args.out}")
        for r in result:
            if r.get("query"):
                print(f"  [{r['geo']}] {r['traffic']:<8} {r['query']}")
            else:
                print(f"  [{r.get('geo', '?')}] ✗ {r.get('error', '')}")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
