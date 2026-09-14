#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HerPulse 题材设定点抽取质量评测 Harness

用途：对「LLM 题材设定点抽取」的预测结果做量化评估，
输出 Precision / Recall / F1（按维度微平均 + 宏平均）、一致性、覆盖率。

数据格式约定
=============
1. 金标集 gold.json（由领域专家双标注 + 仲裁得到）:
[
  {
    "id": "sample_001",
    "text": "原文（可选，便于溯源）",
    "language": "zh|en|ja|ko",
    "gold": {
      "relationship":   ["年下", "慢热"],
      "personality":    [],
      "occupation_age": [],
      "appearance":     ["长发", "兽耳"],
      "world":          ["星际"],
      "gameplay":       ["养成系"]
    }
  }
]

2. 预测结果 predictions.json（LLM 抽取输出，多轮可用于一致性）:
[
  {
    "id": "sample_001",
    "runs": [
      {"relationship": ["年下"], "personality": [], ...},   # run 1
      {"relationship": ["年下", "慢热"], "personality": [], ...}  # run 2
    ]
  }
]

用法
====
  python eval_harness.py --gold gold.json --pred predictions.json
  python eval_harness.py --demo            # 生成示例数据跑一遍演示
"""

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

DIMENSIONS = ["relationship", "personality", "mood", "occupation_age",
              "appearance", "world", "gameplay"]


# ---------------------------------------------------------------- 数据加载
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 兼容 {"samples": [...]} 包裹结构（种子金标集带 meta 元数据）
    if isinstance(data, dict) and "samples" in data:
        return data["samples"]
    return data


def normalize(item):
    """把预测的单个 run 统一成 {dimension: set(tags)}。"""
    return {d: set(item.get(d, []) or []) for d in DIMENSIONS}


# ---------------------------------------------------------------- 核心指标
def compute_metrics(gold_list, pred_list):
    """按维度计算 precision / recall / f1（微平均 + 宏平均）。"""
    # 每个维度累加 TP / FP / FN
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)

    for gold, pred in zip(gold_list, pred_list):
        g = normalize(gold["gold"])
        # 预测取最后一次 run（或合并所有 run，见 compute_consistency）
        last_run = pred["runs"][-1]
        p = normalize(last_run)
        for d in DIMENSIONS:
            gt, pt = g[d], p[d]
            tp[d] += len(gt & pt)
            fp[d] += len(pt - gt)
            fn[d] += len(gt - pt)

    report = {}
    for d in DIMENSIONS:
        prec = tp[d] / (tp[d] + fp[d]) if (tp[d] + fp[d]) else 0.0
        rec = tp[d] / (tp[d] + fn[d]) if (tp[d] + fn[d]) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        report[d] = {"precision": round(prec, 4), "recall": round(rec, 4),
                     "f1": round(f1, 4), "tp": tp[d], "fp": fp[d], "fn": fn[d]}

    # 微平均（先汇总所有维度的 TP/FP/FN 再算）
    micro_tp = sum(tp.values())
    micro_fp = sum(fp.values())
    micro_fn = sum(fn.values())
    micro_prec = micro_tp / (micro_tp + micro_fp) if (micro_tp + micro_fp) else 0.0
    micro_rec = micro_tp / (micro_tp + micro_fn) if (micro_tp + micro_fn) else 0.0
    micro_f1 = 2 * micro_prec * micro_rec / (micro_prec + micro_rec) if (micro_prec + micro_rec) else 0.0

    # 宏平均（各维度 F1 简单平均）
    macro_f1 = sum(report[d]["f1"] for d in DIMENSIONS) / len(DIMENSIONS)

    return {
        "per_dimension": report,
        "micro": {"precision": round(micro_prec, 4), "recall": round(micro_rec, 4), "f1": round(micro_f1, 4)},
        "macro_f1": round(macro_f1, 4),
    }


def compute_consistency(pred_list):
    """同一文本多次抽取的一致性（Jaccard 均值，应 >= 0.9）。"""
    scores = []
    for pred in pred_list:
        runs = [normalize(r) for r in pred["runs"]]
        if len(runs) < 2:
            continue
        # 相邻两轮对比（也可全两两对比）
        for r1, r2 in zip(runs, runs[1:]):
            inter = sum(len(r1[d] & r2[d]) for d in DIMENSIONS)
            union = sum(len(r1[d] | r2[d]) for d in DIMENSIONS)
            scores.append(inter / union if union else 1.0)
    return round(sum(scores) / len(scores), 4) if scores else None


def compute_coverage(gold_list, pred_list):
    """覆盖率：金标里有多少设定点被抽取到（漏标率 = 1 - coverage）。"""
    total = 0
    hit = 0
    for gold, pred in zip(gold_list, pred_list):
        g = normalize(gold["gold"])
        p = normalize(pred["runs"][-1])
        for d in DIMENSIONS:
            total += len(g[d])
            hit += len(g[d] & p[d])
    coverage = hit / total if total else 0.0
    return {"covered": hit, "total": total, "coverage": round(coverage, 4)}


# ---------------------------------------------------------------- 报告输出
def print_report(metrics, consistency, coverage):
    print("=" * 60)
    print("HerPulse 题材设定点抽取评测报告")
    print("=" * 60)
    print(f"\n{'维度':<16}{'Precision':>12}{'Recall':>12}{'F1':>12}")
    print("-" * 52)
    for d in DIMENSIONS:
        m = metrics["per_dimension"][d]
        print(f"{d:<16}{m['precision']:>12}{m['recall']:>12}{m['f1']:>12}")
    print("-" * 52)
    m = metrics["micro"]
    print(f"{'微平均 micro':<16}{m['precision']:>12}{m['recall']:>12}{m['f1']:>12}")
    print(f"{'宏平均 F1':<16}{'':>12}{'':>12}{metrics['macro_f1']:>12}")
    print(f"\n一致性（Jaccard，>=0.9 达标）: {consistency}")
    print(f"覆盖率（金标命中率，>=0.85 达标）: {coverage['coverage']} "
          f"({coverage['covered']}/{coverage['total']})")


# ---------------------------------------------------------------- Demo 数据
def make_demo():
    """生成一份演示用的小金标集 + 预测（含刻意错误，展示指标能抓出问题）。"""
    gold = [
        {"id": "s1", "text": "星际养成系，长发兽耳少年，年下慢热治愈", "language": "zh",
         "gold": {"relationship": ["年下"], "personality": [], "mood": ["慢热", "治愈"],
                  "occupation_age": [], "appearance": ["长发", "兽耳兽人"],
                  "world": ["星际"], "gameplay": ["养成系"]}},
        {"id": "s2", "text": "Slow-burn otome with kuudere CEO, fake dating", "language": "en",
         "gold": {"relationship": ["契约恋爱"], "personality": ["高冷"], "mood": ["慢热"],
                  "occupation_age": ["总裁"], "appearance": [], "world": [], "gameplay": []}},
        {"id": "s3", "text": "校园甜宠，傲娇青梅竹马", "language": "zh",
         "gold": {"relationship": ["竹马"], "personality": ["傲娇"], "mood": ["甜宠"],
                  "occupation_age": [], "appearance": [], "world": ["校园"], "gameplay": []}},
    ]
    # 预测：s1 错加「傲娇」(personality FP)；s2 把 kuudere 映射对但漏「契约恋爱」(relationship FN)；s3 全对
    pred = [
        {"id": "s1", "runs": [
            {"relationship": ["年下"], "personality": ["傲娇"], "mood": ["慢热", "治愈"],
             "occupation_age": [], "appearance": ["长发", "兽耳兽人"],
             "world": ["星际"], "gameplay": ["养成系"]},
            {"relationship": ["年下"], "personality": ["傲娇"], "mood": ["慢热", "治愈"],
             "occupation_age": [], "appearance": ["长发", "兽耳兽人"],
             "world": ["星际"], "gameplay": ["养成系"]},
        ]},
        {"id": "s2", "runs": [
            {"relationship": [], "personality": ["高冷"], "mood": ["慢热"],
             "occupation_age": ["总裁"], "appearance": [], "world": [], "gameplay": []},
            {"relationship": [], "personality": ["高冷"], "mood": ["慢热"],
             "occupation_age": ["总裁"], "appearance": [], "world": [], "gameplay": []},
        ]},
        {"id": "s3", "runs": [
            {"relationship": ["竹马"], "personality": ["傲娇"], "mood": ["甜宠"],
             "occupation_age": [], "appearance": [], "world": ["校园"], "gameplay": []},
        ]},
    ]
    return gold, pred


def main():
    parser = argparse.ArgumentParser(description="HerPulse 抽取质量评测")
    parser.add_argument("--gold", help="金标集 JSON 路径")
    parser.add_argument("--pred", help="预测结果 JSON 路径")
    parser.add_argument("--demo", action="store_true", help="运行演示数据")
    args = parser.parse_args()

    if args.demo or (not args.gold and not args.pred):
        print("[demo] 使用内置演示数据（s1 错加傲娇，s2 漏契约恋爱，s3 全对）\n")
        gold, pred = make_demo()
    else:
        gold = load_json(args.gold)
        pred = load_json(args.pred)

    # 校验 id 对齐
    gold_ids = {g["id"] for g in gold}
    pred_ids = {p["id"] for p in pred}
    assert gold_ids == pred_ids, f"gold 与 pred 的 id 不一致: {gold_ids ^ pred_ids}"

    metrics = compute_metrics(gold, pred)
    consistency = compute_consistency(pred)
    coverage = compute_coverage(gold, pred)

    print_report(metrics, consistency, coverage)


if __name__ == "__main__":
    main()
