#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HerPulse 金标集双标注一致性工具（P1 覆盖重构 rRslmm）

用途：输入两名标注者（annotator A / B）对同一批样本的金标 JSON，
计算标注一致性，输出不一致项清单供仲裁。为「独立双标注 + kappa」流程提供量化依据。

指标说明：
- 样本级 Jaccard 一致率（均值）：两标注者全体标签集合的交/并，衡量整体重叠度。
- 完全一致样本比例：两标注者标签完全相同的样本占比。
- Cohen's kappa（按维度）：在「至少一方标注的设定点」上计算二元一致性，
  避免稀疏多标签场景下「两人都不标」主导导致 kappa 虚高（等价于并集一致性）。

用法：
  python src/kappa_annotate.py --a data/gold_A.json --b data/gold_B.json

输入格式（与 gold_set_seed.json 相同）：
  {"samples": [{"id": "...", "gold": {"relationship": [...], ...}}]}

参考阈值（业界常见）：kappa / Jaccard >= 0.8 视为高度一致；0.6-0.8 中等；
< 0.6 需重新对齐标注规范。
"""

import argparse
import json
from collections import defaultdict

DIMS = ["relationship", "personality", "mood", "occupation_age",
        "appearance", "world", "gameplay"]


def load_gold(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("samples", data) if isinstance(data, dict) else data


def cohen_kappa(obs):
    """obs: list[(a_labeled: bool, b_labeled: bool)]。

    在「至少一方标注」的设定点上计算 Cohen's kappa（此时 d≈0，
    等价于基于并集的一致性，避免稀疏标签下 d 主导）。"""
    n = len(obs)
    if n == 0:
        return None
    a = sum(1 for x, y in obs if x and y)
    b = sum(1 for x, y in obs if x and not y)
    c = sum(1 for x, y in obs if not x and y)
    d = sum(1 for x, y in obs if not x and not y)
    po = (a + d) / n
    pa = ((a + b) / n) * ((a + c) / n)
    pb = ((c + d) / n) * ((b + d) / n)
    pe = pa + pb
    if (1 - pe) == 0:
        return 1.0
    return (po - pe) / (1 - pe)


def main():
    p = argparse.ArgumentParser(description="HerPulse 金标集双标注一致性工具")
    p.add_argument("--a", required=True, help="标注者 A 的金标 JSON")
    p.add_argument("--b", required=True, help="标注者 B 的金标 JSON")
    args = p.parse_args()

    A = {g["id"]: g.get("gold", {}) for g in load_gold(args.a)}
    B = {g["id"]: g.get("gold", {}) for g in load_gold(args.b)}
    ids = sorted(set(A) & set(B))
    if not ids:
        raise SystemExit("两个文件无共同样本 id，无法计算一致性。")

    obs_by_dim = defaultdict(list)
    jaccards = []
    exact = 0
    diff_items = []

    for sid in ids:
        ga, gb = A[sid], B[sid]
        total_a, total_b = set(), set()
        for d in DIMS:
            sa = set(ga.get(d, []))
            sb = set(gb.get(d, []))
            total_a |= sa
            total_b |= sb
            for t in sa | sb:
                obs_by_dim[d].append((t in sa, t in sb))
            for t in sorted(sa - sb):
                diff_items.append((sid, d, t, "A有B无"))
            for t in sorted(sb - sa):
                diff_items.append((sid, d, t, "B有A无"))
        union = total_a | total_b
        j = len(total_a & total_b) / len(union) if union else 1.0
        jaccards.append(j)
        if total_a == total_b:
            exact += 1

    print("=" * 60)
    print("HerPulse 金标集双标注一致性报告")
    print("=" * 60)
    print(f"样本数：{len(ids)}")
    print(f"样本级 Jaccard 一致率（均值）：{sum(jaccards) / len(jaccards):.4f}")
    print(f"完全一致样本：{exact}/{len(ids)}（{exact / len(ids):.2%}）")
    print()
    print(f"{'维度':<16}{'kappa':>10}{'观测数':>8}")
    print("-" * 36)
    for d in DIMS:
        k = cohen_kappa(obs_by_dim[d])
        ks = f"{k:.4f}" if k is not None else "N/A"
        print(f"{d:<16}{ks:>10}{len(obs_by_dim[d]):>8}")
    print()
    print(f"不一致项 {len(diff_items)} 条（供仲裁）：")
    for sid, d, t, who in diff_items:
        print(f"  [{sid}] {d}: 「{t}」({who})")


if __name__ == "__main__":
    main()
