#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HerPulse 热度聚合器（M3/M4 端到端串联核心）

把「真实抽取结果 + 语料信号」聚合成看板数据：
1. 读取语料（corpus_sample.json：text/market/day/signals）与抽取结果（extractions_detail）；
2. 按设定点聚合四信号（social/fanwork/search/rank）；
3. 用 heat_engine 去量纲公式算热度（相对本批语料分布 z-score）；
4. delta 用「最近3天 vs 前11天」声量变化（真实计算，非拍脑袋）；
5. 组合配方：同一样本内跨维度 tag 共现，用 combine_recipe 算配方热度；
6. 收集 new_words（新词提案）与 signal_layer（ship/OTP/二创/争议）；
7. 输出 dashboard_data.json（喂看板）。

用法：
  python src/aggregate.py --corpus data/corpus_sample.json \
      --detail data/extractions_detail_corpus.json \
      --vocab data/seed_vocabulary.json \
      --out data/dashboard_data.json
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from extract import load_json, build_index          # noqa: E402
from heat_engine import (                           # noqa: E402
    SIGNALS, DEFAULT_WEIGHTS, preprocess, compute_heat,
    combine_recipe, reverse_rank, log1p,
)

DIM_NAMES = {
    "relationship": "关系动态",
    "personality": "人物性格",
    "mood": "情绪基调",
    "occupation_age": "职业·年龄",
    "appearance": "外观设定点",
    "world": "世界观·场景",
    "gameplay": "交互·玩法",
}


def build_global_stats(samples, point_values=None):
    """基于本批语料所有样本的信号，算 log 空间的全局均值/方差（z-score 基准）。

    point_values: {signal_name: [设定点粒度的值列表]}。若提供某信号，其 z-score 基准改用
    「设定点粒度」（如 fanwork=AO3 真实 works、search=Google Trends interest），而非样本
    累加值（后者在真实采集时该信号恒为 0）。
    """
    point_values = point_values or {}
    logs = {name: [] for name in SIGNALS}
    for s in samples:
        sig = s.get("signals", {})
        for name in SIGNALS:
            if name in point_values:
                continue  # 该信号单独用设定点粒度
            x = sig.get(name, 0)
            if name == "rank":
                x = reverse_rank(x)
            logs[name].append(log1p(x))
    for name, vals in point_values.items():
        logs[name] = [log1p(v) for v in vals]
    stats = {}
    for name in SIGNALS:
        vals = logs[name]
        if not vals:
            stats[name] = {"mu": 0.0, "sigma": 0.0}
            continue
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / len(vals)
        stats[name] = {"mu": mu, "sigma": var ** 0.5}
    return stats


def load_fanwork_map(path, dim_info):
    """读取 AO3 fanwork 文件，把英文 tag 反查为设定点，返回 {设定点tag: works}。

    fanwork_ao3_*.json 结构：[{tag, works, signals:{fanwork}, ...}, ...]
    dim_info 来自 build_index（别名→规范tag，已小写化），用于反查。
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        print(f"  [fanwork] 未找到 {path}，fanwork 信号保持原值（可能为 0/模拟）")
        return {}
    data = load_json(path)
    fanwork_map = defaultdict(int)
    matched = 0
    for item in data:
        ao3_tag = str(item.get("tag", "")).strip().lower()
        works = int(item.get("works", 0)
                    or (item.get("signals", {}) or {}).get("fanwork", 0)
                    or 0)
        trope = dim_info.get(ao3_tag)
        if trope and works > 0:
            fanwork_map[trope] += works
            matched += 1
    print(f"  [fanwork] 读取 {len(data)} 个 AO3 tag，命中 {matched} 个设定点（去重后 {len(fanwork_map)} 个）")
    return dict(fanwork_map)


def load_search_map(path, dim_info):
    """读取 Google Trends search 文件，把英文关键词反查为设定点，返回 {设定点tag: interest}。

    search_google_*.json 结构：[{tag, interest, momentum, signals:{search}, ...}, ...]
    interest 为 Google Trends 相对热度（0-100，该词自身历史归一化，非跨词绝对量）。
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        print(f"  [search] 未找到 {path}，search 信号保持原值（可能为 0/模拟）")
        return {}
    data = load_json(path)
    search_map = defaultdict(float)
    matched = 0
    for item in data:
        kw = str(item.get("tag", "")).strip().lower()
        interest = float(item.get("interest", 0)
                         or (item.get("signals", {}) or {}).get("search", 0)
                         or 0)
        trope = dim_info.get(kw)
        if trope and interest > 0:
            search_map[trope] += interest
            matched += 1
    print(f"  [search] 读取 {len(data)} 个 Google Trends 关键词，命中 {matched} 个设定点（去重后 {len(search_map)} 个）")
    return dict(search_map)


def aggregate_signals(samples):
    """对每个 tag 聚合信号。返回 {tag: {signals:{...}, days:[...], markets:[...], evidence:[...]}}。"""
    acc = defaultdict(lambda: {
        "signals": {name: 0 for name in SIGNALS},
        "rank_best": 999,
        "days": [],
        "markets": [],
        "evidence": [],
        "samples": set(),
    })
    for s in samples:
        sig = s.get("signals", {})
        tags = set()
        # s["_tags"] 由调用方在聚合前注入：[(dim, tag, evidence), ...]
        for dim, tag, ev in s.get("_tags", []):
            tags.add(tag)
            a = acc[tag]
            a["evidence"].append(ev)
        for tag in tags:
            a = acc[tag]
            for name in SIGNALS:
                a["signals"][name] += sig.get(name, 0)
            a["rank_best"] = min(a["rank_best"], sig.get("rank", 999))
            a["days"].append(s.get("day", 0))
            a["markets"].append(s.get("market", "?"))
            a["samples"].add(s.get("id", ""))
    return acc


def calc_delta(days, signals_map):
    """最近3天 vs 前11天 的 social 声量变化百分比。signals_map: {day: social}。"""
    recent = sum(v for d, v in signals_map.items() if d <= 2)
    older = sum(v for d, v in signals_map.items() if d > 2)
    if older <= 0:
        return 100.0 if recent > 0 else 0.0
    return (recent - older) / older * 100.0


def region_label(markets):
    eu = sum(1 for m in markets if m == "EUUS")
    jp = sum(1 for m in markets if m == "JPKR")
    if eu > 0 and jp > 0:
        return "全球"
    return "欧美" if eu > 0 else "日韩"


def main():
    p = argparse.ArgumentParser(description="HerPulse 热度聚合器")
    p.add_argument("--corpus", default="data/corpus_sample.json")
    p.add_argument("--detail", default="data/extractions_detail_corpus.json")
    p.add_argument("--vocab", default="data/seed_vocabulary.json")
    p.add_argument("--out", default="data/dashboard_data.json")
    p.add_argument("--fanwork", default="", help="AO3 fanwork 文件（fanwork_ao3_*.json），注入真实二创产量信号")
    p.add_argument("--search", default="", help="Google Trends search 文件（search_google_*.json），注入真实搜索热度信号")
    p.add_argument("--top", type=int, default=18, help="题材榜输出条数")
    args = p.parse_args()

    vocab = load_json(args.vocab)
    tag_info, dim_info = build_index(vocab)

    corpus = load_json(args.corpus)
    samples = corpus["samples"] if isinstance(corpus, dict) else corpus
    detail = load_json(args.detail)

    # 建立 id -> 抽取结果（取最后一轮）
    detail_by_id = {d["id"]: d for d in detail}

    # 注入每个样本的抽取 tags
    new_words = defaultdict(list)
    signal_layer = {"ship_otp": 0, "fanworks": 0, "controversy": 0}
    for s in samples:
        sid = s.get("id", "")
        d = detail_by_id.get(sid)
        if not d or not d.get("runs"):
            s["_tags"] = []
            continue
        run = d["runs"][-1]
        det = run.get("detail", {})
        tags = []
        for dim, items in det.items():
            for it in items:
                tags.append((dim, it["tag"], it.get("evidence", "")))
        s["_tags"] = tags
        # new_words / signal_layer 来自 raw
        raw = run.get("raw", {})
        for nw in raw.get("new_words", []):
            new_words[nw.get("candidate", "")].append(nw.get("reason", ""))
        sl = raw.get("signal_layer", {})
        for k in signal_layer:
            v = sl.get(k, [])
            signal_layer[k] += len(v) if isinstance(v, list) else (1 if v else 0)

    # 读 AO3 fanwork + Google Trends search 真值（可选）
    fanwork_map = load_fanwork_map(args.fanwork, dim_info)
    search_map = load_search_map(args.search, dim_info)

    # 聚合样本信号（social/search/rank + 样本内的 fanwork）
    acc = aggregate_signals(samples)

    # 注入真实 fanwork / search：AO3 works 数、Google Trends interest 覆盖样本累加值
    all_tags = list(acc.keys())
    point_values = {}
    if fanwork_map:
        point_values["fanwork"] = [fanwork_map.get(t, 0) for t in all_tags]
        for t in all_tags:
            acc[t]["signals"]["fanwork"] = fanwork_map.get(t, 0)
    if search_map:
        point_values["search"] = [search_map.get(t, 0) for t in all_tags]
        for t in all_tags:
            acc[t]["signals"]["search"] = search_map.get(t, 0)

    # 全局 z-score 基准：fanwork/search 用设定点粒度，其余用样本粒度
    stats = build_global_stats(samples, point_values=point_values)

    # 每个 tag 算去量纲热度 + delta + region
    tropes = []
    for tag, a in acc.items():
        sig = {name: a["signals"][name] for name in SIGNALS}
        sig["rank"] = a["rank_best"]
        z = preprocess(sig, stats)
        heat = compute_heat(z, DEFAULT_WEIGHTS)
        # delta：从 samples 里按 tag 重建 day->social（最近3天 vs 前11天）
        day_social = defaultdict(float)
        for s in samples:
            if tag in {t for _, t, _ in s.get("_tags", [])}:
                day_social[s.get("day", 0)] += s.get("signals", {}).get("social", 0)
        delta = calc_delta(list(day_social.keys()), dict(day_social))
        region = region_label(a["markets"])
        info = tag_info.get(tag, {})
        tropes.append({
            "tag": tag,
            "dim": info.get("dim", "?"),
            "dim_name": DIM_NAMES.get(info.get("dim", "?"), "?"),
            "heat": round(heat, 3),
            "delta": round(delta, 1),
            "region": region,
            "definition": info.get("definition", ""),
            "aliases": info.get("aliases", {}),
            "high_signal": info.get("high_signal", False),
            "works": len(a["samples"]),
            "signals_z": {n: round(z[n], 3) for n in SIGNALS},
            "signals_raw": {n: sig[n] for n in SIGNALS},
            "evidence": list(dict.fromkeys(a["evidence"]))[:4],  # 去重、最多4条证据
        })

    tropes.sort(key=lambda x: (-x["heat"]))

    # 组合配方：同一样本内跨维度 tag 共现
    pair_count = defaultdict(int)
    for s in samples:
        dims = defaultdict(list)
        for dim, tag, _ in s.get("_tags", []):
            dims[dim].append(tag)
        seen = set()
        for d1 in dims:
            for d2 in dims:
                if d1 >= d2:
                    continue
                for t1 in dims[d1]:
                    for t2 in dims[d2]:
                        key = tuple(sorted([t1, t2]))
                        if key not in seen:
                            seen.add(key)
                            pair_count[key] += 1

    heat_by_tag = {t["tag"]: t["heat"] for t in tropes}
    recipes = []
    for (t1, t2), cnt in pair_count.items():
        if cnt < 2:
            continue
        lift = 1.0 + 0.5 * (cnt - 1)  # 共现越多提升越大
        h = combine_recipe([heat_by_tag.get(t1, 0), heat_by_tag.get(t2, 0)], lift)
        recipes.append({
            "members": [t1, t2],
            "count": cnt,
            "lift": round(lift, 2),
            "heat": round(h, 3),
        })
    recipes.sort(key=lambda x: (-x["heat"]))
    recipes = recipes[:12]

    # 横截面热点：z-score 语义（热度是 z-score 加权和，>=2 即显著高于基准 2σ）。
    # 注：threshold_mad 用于「接入时序数据后的突增检测」，横截面热点不适用 MAD。
    obs_th, hot_th = 1.0, 2.0
    hot_tags = [t for t in tropes if t["heat"] >= hot_th]

    # 维度分布 + 市场分布（供看板图表）
    dim_dist = {}
    market_dist = {"欧美": 0, "日韩": 0, "全球": 0}
    for t in tropes:
        d = t["dim"]
        dd = dim_dist.setdefault(d, {"dim": d, "name": t["dim_name"], "count": 0, "hot": 0, "heat_sum": 0.0})
        dd["count"] += 1
        dd["heat_sum"] += t["heat"]
        if t["heat"] >= hot_th:
            dd["hot"] += 1
        market_dist[t["region"]] = market_dist.get(t["region"], 0) + 1
    dim_dist_list = sorted(dim_dist.values(), key=lambda x: -x["count"])

    # 判断信号来源：样本是否来自真实采集（source 字段）
    sources = {s.get("source", "") for s in samples}
    is_real_fetch = bool(sources & {"reddit", "bluesky", "ao3"})

    # KPI
    total_social = sum(s.get("signals", {}).get("social", 0) for s in samples)
    surge_tags = [t for t in tropes if t["delta"] >= 50]

    # new_words 汇总
    new_words_list = sorted(
        [{"candidate": k, "count": len(v), "reasons": list(dict.fromkeys(v))[:2]}
         for k, v in new_words.items()],
        key=lambda x: -x["count"],
    )

    # 信号来源标注（供看板如实展示哪些是真值、哪些未接入）
    signal_source = {
        "social": {"label": "社媒声量", "real": is_real_fetch,
                    "source": "Bluesky 真实声量(likes+replies×10)" if is_real_fetch else "模拟值"},
        "fanwork": {"label": "二创产量", "real": bool(fanwork_map),
                     "source": "AO3 真实作品数" if fanwork_map else ("未接入(恒0)" if is_real_fetch else "模拟值")},
        "search": {"label": "搜索热度", "real": bool(search_map),
                    "source": "Google Trends 真实相对热度(0-100,自身历史归一化)" if search_map else ("未接入(恒0)" if is_real_fetch else "模拟值")},
        "rank": {"label": "榜单名次", "real": is_real_fetch,
                  "source": "采集内名次" if is_real_fetch else "模拟值"},
    }

    out = {
        "meta": {
            "generated": "2026-09-14",
            "pipeline": "extract(deepseek-v4-pro) -> aggregate(heat_engine 去量纲)",
            "note": "social=Bluesky 真实声量；fanwork=AO3 真实二创产量；search=Google Trends 相对热度(自身历史归一化,未接入时恒0)；rank=采集内名次；delta=最近3天vs前11天声量变化",
            "sample_count": len(samples),
            "signal_source": signal_source,
        },
        "kpi": {
            "tracked": len(samples),
            "hotspots": len(hot_tags),
            "surge_alerts": len(surge_tags),
            "total_social": total_social,
        },
        "thresholds": {
            "observe": obs_th,
            "hot": hot_th,
            "note": "横截面 z-score 阈值（heat>=2 为热点、>=1 为观察）；MAD 阈值在接入时序数据后用于突增检测",
        },
        "tropes": tropes[:args.top],
        "recipes": recipes,
        "new_words": new_words_list,
        "signal_layer": signal_layer,
        "dim_dist": dim_dist_list,
        "market_dist": market_dist,
        "dims": {k: {"name": v} for k, v in DIM_NAMES.items()},
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"聚合完成：{len(samples)} 条语料 → {len(tropes)} 个设定点 → {args.out}")
    print(f"  热点阈值 {hot_th:.3f}，热点设定点 {len(hot_tags)} 个，突增(Δ≥50%) {len(surge_tags)} 个")
    print(f"  组合配方 {len(recipes)} 个，新词提案 {len(new_words_list)} 个")
    print(f"\n  Top 8 设定点：")
    for t in tropes[:8]:
        print(f"    {t['heat']:+.3f}  Δ{t['delta']:+.1f}%  [{t['dim_name']}] {t['tag']}  (跨作品 {t['works']})")


if __name__ == "__main__":
    main()
