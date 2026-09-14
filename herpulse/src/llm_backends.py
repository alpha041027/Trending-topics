#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HerPulse 抽取器的 LLM 后端抽象层

设计目标：
- 抽取器（extract.py）只依赖一个统一接口 `complete(system, user) -> str`，
  因此可以在「离线冒烟」和「真实模型」之间无缝切换。
- 零第三方依赖：真实后端用标准库 urllib 调 OpenAI 兼容 API，无需 openai 包。

后端：
1. MockBackend             —— 基于受控词表别名做子串匹配的规则后端。
   仅用于「链路冒烟测试」（证明 抽取→评测 全链路能跑通），
   不代表真实抽取质量（它不捕捉跨句推断、语义隐含、多语言归一）。
2. OpenAICompatibleBackend —— 调用任意 OpenAI 兼容 /chat/completions 端点
   （OpenAI / DeepSeek / 通义 / 本地 vLLM 等）。配好 base_url + model + api_key
   即可跑「真实 baseline」评测。

注意：`temperature=0` 用于保证抽取一致性（评测一致性指标的前提）。
"""

import json
import os
import urllib.request
import urllib.error

DIMENSION_KEYS = ["relationship", "personality", "mood", "occupation_age",
                  "appearance", "world", "gameplay"]


class LLMBackend:
    """后端统一接口。返回模型生成的原始文本（期望是 JSON）。"""

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class MockBackend(LLMBackend):
    """规则后端：把原文中出现的「设定点 tag / 多语言别名」按子串匹配回受控词表。

    仅用于链路冒烟，证据为启发式命中（取别名本身），confidence 统一 0.9。
    """

    def __init__(self, vocab):
        self.dim_of_tag = {}
        self.matchers = []  # (lower_key, tag, dim_key)
        for dim in vocab["dimensions"]:
            dk = dim["key"]
            for cat in dim["categories"]:
                for p in cat["points"]:
                    tag = p["tag"]
                    self.dim_of_tag[tag] = dk
                    keys = [tag]
                    for lang_aliases in p["aliases"].values():
                        keys.extend(lang_aliases)
                    for k in keys:
                        self.matchers.append((k.lower(), tag, dk))
        # 长串优先，避免「long hair」先被「hair」这类短别名误命中
        self.matchers.sort(key=lambda x: -len(x[0]))

    @staticmethod
    def _detect_language(text):
        for ch in text:
            o = ord(ch)
            if 0x4E00 <= o <= 0x9FFF:
                return "zh"
            if 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF:
                return "ja"
            if 0xAC00 <= o <= 0xD7AF:
                return "ko"
        return "en"

    def complete(self, system: str, user: str) -> str:
        lowered = user.lower()
        dims = {d: {} for d in DIMENSION_KEYS}
        for alias, tag, dk in self.matchers:
            if alias in lowered and tag not in dims[dk]:
                dims[dk][tag] = {"tag": tag, "confidence": 0.9, "evidence": alias}
        result = {
            "dimensions": {d: list(dims[d].values()) for d in DIMENSION_KEYS},
            "signal_layer": {"ship_otp": [], "fanworks": [], "controversy": []},
            "new_words": [],
            "language": self._detect_language(user),
        }
        return json.dumps(result, ensure_ascii=False)


class OpenAICompatibleBackend(LLMBackend):
    """OpenAI 兼容 /chat/completions 后端（标准库实现，零依赖）。"""

    def __init__(self, base_url, model, api_key=None, temperature=0.0,
                 timeout=120, response_format="json_object", extra_headers=None,
                 extra_body=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self.response_format = response_format  # None 表示不请求 JSON 模式
        self.extra_headers = extra_headers or {}
        self.extra_body = extra_body or {}

    def complete(self, system: str, user: str) -> str:
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if self.response_format:
            payload["response_format"] = {"type": self.response_format}
        if self.extra_body:
            payload.update(self.extra_body)

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)
        for k, v in self.extra_headers.items():
            req.add_header(k, v)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise RuntimeError(f"LLM API HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"LLM API 连接失败: {e.reason}") from e

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"LLM API 返回格式异常: {json.dumps(body, ensure_ascii=False)[:500]}") from e


def build_backend(args, vocab):
    """根据命令行参数构造后端。"""
    kind = getattr(args, "backend", "mock")
    if kind == "mock":
        return MockBackend(vocab)

    if kind == "openai":
        base_url = getattr(args, "base_url", None) or os.environ.get("HERPULSE_LLM_BASE_URL")
        model = getattr(args, "model", None) or os.environ.get("HERPULSE_LLM_MODEL")
        api_key = getattr(args, "api_key", None) or os.environ.get("HERPULSE_LLM_API_KEY")
        if not base_url or not model:
            raise SystemExit(
                "openai 后端需要 --base-url 与 --model（或环境变量 "
                "HERPULSE_LLM_BASE_URL / HERPULSE_LLM_MODEL）。")
        extra_body = None
        eb_raw = getattr(args, "extra_body", None) or os.environ.get("HERPULSE_LLM_EXTRA_BODY")
        if eb_raw:
            try:
                extra_body = json.loads(eb_raw)
            except json.JSONDecodeError as e:
                raise SystemExit(f"--extra-body 不是合法 JSON: {e}")
        return OpenAICompatibleBackend(base_url=base_url, model=model, api_key=api_key,
                                       extra_body=extra_body)

    raise SystemExit(f"未知后端: {kind}（可选 mock / openai）")
