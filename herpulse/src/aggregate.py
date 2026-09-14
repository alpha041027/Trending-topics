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


def build_global_stats(samples):
    """基于本批语料所有样本的信号，算 log 空间的全局均值/方差（z-score 基准）。"""
    logs = {name: [] for name in SIGNALS}
    for s in samples:
        sig = s.get("signals", {})
        for name in SIGNALS:
            x = sig.get(name, 0)
            if name == "rank":
                x = reverse_rank(x)
            logs[name].append(log1p(x))
    stats = {}
    for name in SIGNALS:
        vals = logs[name]
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / len(vals)
        stats[name] = {"mu": mu, "sigma": var ** 0.5}
    return stats


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
    p.add_argument("--top", type=int, default=18, help="题材榜输出条数")
    args = p.parse_args()

    vocab = load_json(args.vocab)
    tag_info, _ = build_index(vocab)

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

    stats = build_global_stats(samples)
    acc = aggregate_signals(samples)

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

    # KPI
    total_social = sum(s.get("signals", {}).get("social", 0) for s in samples)
    surge_tags = [t for t in tropes if t["delta"] >= 50]

    # new_words 汇总
    new_words_list = sorted(
        [{"candidate": k, "count": len(v), "reasons": list(dict.fromkeys(v))[:2]}
         for k, v in new_words.items()],
        key=lambda x: -x["count"],
    )

    out = {
        "meta": {
            "generated": "2026-09-14",
            "pipeline": "extract(deepseek-v4-pro) -> aggregate(heat_engine 去量纲)",
            "note": "抽取=真实 LLM；热度信号(social/fanwork/search/rank)=模拟值待接真实数据源；delta=最近3天vs前11天声量变化",
            "sample_count": len(samples),
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
