#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HerPulse 受控词表覆盖率验证

用途：用受控词表（tag + 多语言别名）去覆盖 top 作品测试集的设定点，
输出每个作品的命中率、总覆盖率，以及未覆盖的设定点（= 词表缺口）。

用法：
  python coverage_check.py --vocab seed_vocabulary.json --test coverage_test.json
"""

import argparse
import json
from collections import defaultdict

DIMENSION_KEYS = ["relationship", "personality", "occupation_age",
                  "appearance", "world", "gameplay"]


def build_index(vocab):
    """构建 tag -> 维度 的索引，以及 别名 -> tag 的索引。"""
    tag_to_dim = {}
    alias_to_tag = {}

    for dim in vocab["dimensions"]:
        for cat in dim["categories"]:
            for point in cat["points"]:
                tag = point["tag"]
                tag_to_dim[tag] = dim["key"]
                for lang_aliases in point["aliases"].values():
                    for a in lang_aliases:
                        alias_to_tag[a.lower()] = tag

    # signal_layer 的 tag 也纳入（不算题材维度，但可命中）
    for sp in vocab.get("signal_layer", {}).get("points", []):
        tag_to_dim[sp["tag"]] = "signal_layer"
        for lang_aliases in sp["aliases"].values():
            for a in lang_aliases:
                alias_to_tag[a.lower()] = sp["tag"]

    return tag_to_dim, alias_to_tag


def check_coverage(vocab, test):
    tag_to_dim, alias_to_tag = build_index(vocab)

    per_work = []
    uncovered = defaultdict(list)  # 维度 -> 未覆盖设定点
    total = 0
    hit = 0

    for w in test["works"]:
        w_hit = 0
        for p in w["points"]:
            total += 1
            if p in tag_to_dim:
                w_hit += 1
            elif p.lower() in alias_to_tag:
                w_hit += 1
            else:
                uncovered["unknown"].append(f"{w['name']}: {p}")
        hit += w_hit
        per_work.append((w["name"], w_hit, len(w["points"])))

    coverage = hit / total if total else 0.0
    return per_work, coverage, uncovered, total, hit


def main():
    parser = argparse.ArgumentParser(description="HerPulse 词表覆盖率验证")
    parser.add_argument("--vocab", default="seed_vocabulary.json")
    parser.add_argument("--test", default="coverage_test.json")
    args = parser.parse_args()

    vocab = json.load(open(args.vocab, encoding="utf-8"))
    test = json.load(open(args.test, encoding="utf-8"))

    per_work, coverage, uncovered, total, hit = check_coverage(vocab, test)

    print("=" * 56)
    print("HerPulse 受控词表覆盖率报告")
    print("=" * 56)
    print(f"\n{'作品':<28}{'命中':>6}{'总数':>6}{'覆盖率':>10}")
    print("-" * 56)
    for name, h, n in per_work:
        rate = h / n if n else 0
        flag = "" if rate == 1.0 else "  <-- 有缺口"
        print(f"{name:<28}{h:>6}{n:>6}{rate:>9.0%}{flag}")
    print("-" * 56)
    print(f"\n总覆盖率：{hit}/{total} = {coverage:.1%}")
    print(f"达标线：>= 80%（M1 验收标准）")
    print(f"{'✅ 达标' if coverage >= 0.8 else '❌ 未达标'}")

    if uncovered["unknown"]:
        print(f"\n未覆盖设定点（词表缺口，共 {len(uncovered['unknown'])} 个）：")
        for u in sorted(uncovered["unknown"]):
            print(f"  - {u}")
    else:
        print("\n无词表缺口，全部覆盖。")


if __name__ == "__main__":
    main()
