#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HerPulse 题材设定点抽取器（M2 harness 核心）

把设计稿《题材分析引擎设计稿_v0.md》第 4 节的抽取 Prompt 落地成可运行脚本：
- 加载受控词表 v0.3，构建 tag/别名索引；
- 词表按需注入 system prompt（默认全量，可用 --high-signal-only 精简）；
- 注入 few-shot 示例（与金标集隔离）；
- 调用可插拔后端（mock 冒烟 / openai 真实）；
- 解析并校验模型输出：别名→规范名归一、非法 tag 丢弃并告警；
- 输出两份结果：predictions.json（喂 eval_harness）+ extractions_detail.json（带证据，供审计）。

用法：
  # 离线冒烟（链路验证，不衡量真实抽取质量）
  python src/extract.py --input data/gold_set_seed.json --backend mock --n-runs 2

  # 真实 baseline（配好 LLM 后）
  python src/extract.py --input data/gold_set_seed.json --backend openai \
      --base-url https://api.example.com/v1 --model your-model --api-key sk-xxx
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm_backends import build_backend, DIMENSION_KEYS  # noqa: E402

SIGNAL_KEYS = ["ship_otp", "fanworks", "controversy"]

SYSTEM_PROMPT_TEMPLATE = """你是 HerPulse 的题材设定点抽取器。你的任务是从给定的「海外女性向互动内容」文本中，抽取出多维度的题材设定点（setting points），输出严格 JSON。

【输入】一段原始内容（可能是作品简介、角色介绍、社媒帖子、评论、PV 文案，语言为中文/英文/日文/韩文之一）。

【受控词表】你只能从下面的词表中选 tag（规范名）。每个维度列出的设定点已人工确认，含定义与多语言别名（en/ja/ko），别名用于把原文中的外来词归一到规范名。
__VOCAB__

__CROSSREFS__

【输出 JSON Schema】严格遵守，只输出 JSON，不要输出任何 JSON 以外的文字：
{
  "dimensions": {
    "relationship":   [{"tag": "关系动态设定点规范名", "confidence": 0.0, "evidence": "原文逐字引用"}],
    "personality":    [{"tag": "人物性格设定点规范名", "confidence": 0.0, "evidence": "原文逐字引用"}],
    "mood":           [{"tag": "情绪基调体验设定点规范名", "confidence": 0.0, "evidence": "原文逐字引用"}],
    "occupation_age": [{"tag": "职业年龄设定点规范名", "confidence": 0.0, "evidence": "原文逐字引用"}],
    "appearance":     [{"tag": "外观设定点规范名", "confidence": 0.0, "evidence": "原文逐字引用"}],
    "world":          [{"tag": "世界观场景设定点规范名", "confidence": 0.0, "evidence": "原文逐字引用"}],
    "gameplay":       [{"tag": "交互玩法设定点规范名", "confidence": 0.0, "evidence": "原文逐字引用"}]
  },
  "signal_layer": {"ship_otp": [], "fanworks": [], "controversy": []},
  "new_words": [{"dimension": "维度名", "candidate": "候选新词", "evidence": "原文逐字引用", "reason": "为什么认为它是新设定点"}],
  "language": "zh|en|ja|ko"
}

【铁律】
1. evidence 必须逐字引用原文，禁止改写、禁止编造。无法引用原文的抽取一律舍弃。
2. 宁缺毋滥：不命中任何受控词表就输出空数组 []，禁止硬塞近义词。
3. 词表里没有的候选词，放进 new_words，绝不允许塞进现有维度数组。
4. 每个 tag 必须与受控词表的规范名完全一致（原文中的中/英/日/韩别名都要归一到规范名）。
5. confidence 是你对该 tag 与原文匹配的确信度：0.9+ 显式原文；0.7-0.9 需推断；<0.7 不确定，可放 new_words 或舍弃。
6. 多语言输入：识别原文语言填入 language，tag 一律映射回受控词表的规范名。"""


# ---------------------------------------------------------------- 数据加载
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_index(vocab):
    """tag -> 元信息；别名(lower) -> 规范 tag。"""
    tag_info = {}
    alias_to_tag = {}
    for dim in vocab["dimensions"]:
        dk = dim["key"]
        for cat in dim["categories"]:
            for p in cat["points"]:
                tag = p["tag"]
                tag_info[tag] = {
                    "dim": dk,
                    "definition": p.get("definition", ""),
                    "aliases": p.get("aliases", {}),
                    "high_signal": p.get("high_signal", False),
                }
                alias_to_tag.setdefault(tag.lower(), tag)
                for lang_aliases in p["aliases"].values():
                    for a in lang_aliases:
                        alias_to_tag.setdefault(a.lower(), tag)
    return tag_info, alias_to_tag


def build_vocab_injection(vocab, high_signal_only=False):
    """生成紧凑词表文本，注入 system prompt。"""
    lines = []
    for dim in vocab["dimensions"]:
        name = dim["name"]["zh"]
        lines.append(f"【{name}】(key={dim['key']})")
        for cat in dim["categories"]:
            for p in cat["points"]:
                if high_signal_only and not p.get("high_signal", False):
                    continue
                alias_bits = []
                for lang in ("en", "ja", "ko"):
                    al = p["aliases"].get(lang, [])[:3]
                    if al:
                        alias_bits.append(f"{lang}:{','.join(al)}")
                alias_str = f"  [{'; '.join(alias_bits)}]" if alias_bits else ""
                lines.append(f"- {p['tag']}：{p.get('definition', '')}{alias_str}")
    return "\n".join(lines)


def build_crossref_rules(vocab):
    """从词表 cross_refs 字段生成「易混词单归属裁决」文本，注入 system prompt。

    只处理跨维度的 cross_refs（消除「霸总↔总裁」「骑士系↔职业」这类归属分歧）；
    同维度内的包含/易混（如 魔法⊂西幻、爽文打脸↔逆袭）不在本规则范围。
    """
    dim_name = {d["key"]: d["name"]["zh"] for d in vocab["dimensions"]}
    rows = []
    for dim in vocab["dimensions"]:
        dk = dim["key"]
        for cat in dim["categories"]:
            for p in cat["points"]:
                cross_dims = [c for c in (p.get("cross_refs") or []) if c.get("dim") != dk]
                if not cross_dims:
                    continue
                refs = "、".join(
                    f"{dim_name.get(c['dim'], c['dim'])}·{c['tag']}" for c in cross_dims)
                notes = " ".join(c.get("note", "") for c in cross_dims if c.get("note"))
                suffix = f"（{notes}）" if notes else ""
                rows.append(
                    f"- 「{p['tag']}」主归【{dim_name[dk]}】；易与 {refs} 混淆，命中只归本维度、勿重复计入其它维度。{suffix}")
    if not rows:
        return ""
    return ("【易混词单归属裁决】下列设定点跨维度易混，务必按「主归维度」判定，不要重复计入多个维度：\n"
            + "\n".join(rows))


# ---------------------------------------------------------------- Prompt 构建
def build_user_prompt(text, fewshot):
    parts = []
    if fewshot:
        parts.append("以下为示例（帮助理解别名→规范名的映射规则）：")
        for ex in fewshot:
            parts.append(f"【输入】{ex['text']}")
            parts.append(f"【输出】{json.dumps(ex['output'], ensure_ascii=False)}")
    parts.append("")
    parts.append("现在请抽取下面这段文本，只输出 JSON：")
    parts.append(f"【待抽取文本】{text}")
    return "\n".join(parts)


# ---------------------------------------------------------------- 解析与校验
def extract_json(raw):
    raw = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"无法从模型输出解析 JSON，前 200 字符：{raw[:200]}")
    return json.loads(raw[start:end + 1])


def normalize_output(obj, tag_info, alias_to_tag):
    """别名→规范名归一；非法 tag 丢弃并告警。返回 (规范tags, 证据详情, 告警列表)。"""
    dims_raw = obj.get("dimensions", {})
    norm_tags = {d: [] for d in DIMENSION_KEYS}
    detail = {d: [] for d in DIMENSION_KEYS}
    warnings = []
    for d in DIMENSION_KEYS:
        for item in (dims_raw.get(d) or []):
            if not isinstance(item, dict):
                continue
            tag = item.get("tag")
            if not tag:
                continue
            if tag in tag_info:
                canon = tag
            elif tag.lower() in alias_to_tag:
                canon = alias_to_tag[tag.lower()]
            else:
                warnings.append(f"[{d}] 未知 tag「{tag}」已丢弃（应走 new_words）")
                continue
            if canon not in norm_tags[d]:
                norm_tags[d].append(canon)
                detail[d].append({
                    "tag": canon,
                    "confidence": item.get("confidence"),
                    "evidence": item.get("evidence", ""),
                })
    return norm_tags, detail, warnings


# ---------------------------------------------------------------- 主流程
def load_samples(args):
    if args.text:
        return [{"id": "text_0", "text": args.text}]
    data = load_json(args.input)
    return data.get("samples", data) if isinstance(data, dict) else data


def main():
    p = argparse.ArgumentParser(description="HerPulse 题材设定点抽取器")
    p.add_argument("--vocab", default="data/seed_vocabulary.json")
    p.add_argument("--input", help="待抽取文本 JSON（支持 {\"samples\":[...]} 或纯 list）")
    p.add_argument("--text", help="单条文本（与 --input 二选一）")
    p.add_argument("--fewshot", default="data/fewshot_examples.json")
    p.add_argument("--backend", default="mock", help="mock | openai")
    p.add_argument("--base-url", help="openai 后端 base_url")
    p.add_argument("--model", help="openai 后端模型名")
    p.add_argument("--api-key", help="openai 后端 api_key")
    p.add_argument("--extra-body", help="额外请求体 JSON（如 DeepSeek V4 禁用 thinking：'{\"thinking\":{\"type\":\"disabled\"}}'）")
    p.add_argument("--n-runs", type=int, default=2, help="同文本抽取轮数（测一致性）")
    p.add_argument("--high-signal-only", action="store_true", help="词表只注入 high_signal 子集")
    p.add_argument("--out-pred", default="data/predictions.json")
    p.add_argument("--out-detail", default="data/extractions_detail.json")
    args = p.parse_args()

    vocab = load_json(args.vocab)
    tag_info, alias_to_tag = build_index(vocab)
    vocab_text = build_vocab_injection(vocab, args.high_signal_only)
    crossref_text = build_crossref_rules(vocab)
    system = SYSTEM_PROMPT_TEMPLATE.replace("__VOCAB__", vocab_text).replace("__CROSSREFS__", crossref_text)

    fewshot = []
    if args.fewshot:
        fs = load_json(args.fewshot)
        fewshot = fs.get("examples", fs) if isinstance(fs, dict) else fs

    samples = load_samples(args)
    backend = build_backend(args, vocab)

    predictions = []
    details = []
    warn_total = 0
    for s in samples:
        sid = s.get("id", str(len(predictions)))
        text = s["text"]
        user = build_user_prompt(text, fewshot)
        runs, run_details = [], []
        for _ in range(args.n_runs):
            raw = backend.complete(system, user)
            obj = extract_json(raw)
            norm, detail, warns = normalize_output(obj, tag_info, alias_to_tag)
            warn_total += len(warns)
            runs.append(norm)
            run_details.append({"raw": obj, "detail": detail, "warnings": warns})
        predictions.append({"id": sid, "runs": runs})
        details.append({"id": sid, "text": text, "runs": run_details})

    Path(args.out_pred).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_pred, "w", encoding="utf-8") as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)
    with open(args.out_detail, "w", encoding="utf-8") as f:
        json.dump(details, f, ensure_ascii=False, indent=2)

    print(f"抽取完成：{len(samples)} 条 × {args.n_runs} 轮")
    print(f"  预测结果 -> {args.out_pred}")
    print(f"  证据详情 -> {args.out_detail}")
    print(f"  归一/丢弃告警 {warn_total} 条（详见 detail）")


if __name__ == "__main__":
    main()
