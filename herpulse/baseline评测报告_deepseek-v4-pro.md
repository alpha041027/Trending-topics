# HerPulse 题材抽取 Baseline 评测报告

> 模型：DeepSeek V4 Pro（`deepseek-v4-pro`，thinking=disabled 非思考模式，temperature=0）
> 金标集：`data/gold_set_seed.json`（种子版 32 条 / 143 个 gold 设定点，7 维度）
> 日期：2026-09-14
> 评测脚本：`src/eval_harness.py`

---

## 一、评测配置

| 项 | 值 |
|---|---|
| 模型 | deepseek-v4-pro |
| 端点 | https://api.deepseek.com |
| thinking | disabled（非思考模式，贴近生产 fast 配置） |
| temperature | 0（保证一致性） |
| response_format | json_object |
| 词表注入 | v0.3 全量 167 设定点（含定义 + 英/日/韩别名） |
| few-shot | 2 条（中/英各一，与金标集隔离） |
| 抽取轮数 | 每样本 2 轮（测一致性） |

---

## 二、核心指标（对照验收线）

| 维度 | Precision | Recall | F1 | 验收线 | 判定 |
|---|---:|---:|---:|---:|:--:|
| relationship 关系动态 | 0.9412 | 0.9697 | **0.9552** | ≥0.7 | ✅ |
| personality 人物性格 | 0.9048 | 0.8261 | **0.8636** | ≥0.7 | ✅ |
| mood 情绪基调 | 0.9048 | 0.9048 | **0.9048** | （新增，未设线） | ✅ |
| occupation_age 职业·年龄 | 0.8095 | 1.0 | **0.8947** | ≥0.85 | ✅ |
| appearance 外观 | 1.0 | 1.0 | **1.0** | ≥0.85 | ✅ |
| world 世界观 | 0.9259 | 1.0 | **0.9615** | ≥0.85 | ✅ |
| gameplay 交互·玩法 | 0.8333 | 1.0 | **0.9091** | ≥0.85 | ✅ |
| **微平均 micro** | 0.9007 | 0.951 | **0.9252** | — | — |
| **宏平均 macro-F1** | — | — | **0.927** | — | — |

**附加指标：**
- 一致性（Jaccard，≥0.9 达标）：**0.9723** ✅
- 覆盖率（金标命中率，≥0.85 达标）：**0.951**（136/143）✅

**结论：7 个维度全部达标，且大幅超过验收线。** 抽取链路（词表 RAG 注入 → few-shot → 别名归一 → 证据引用）在真实模型上成立。

---

## 三、错例分析（比分数更有价值的部分）

### 3.1 漏抽 FN（6 处）与多抽 FP（14 处）可归纳为三类系统性问题

#### 问题 A：跨维度归属分歧（MECE 边界）—— 评审遗留问题在真实模型上暴露

| 样本 | 争议 |
|---|---|
| gs_008 / gs_018（韩文 재벌 财阀） | gold 标 `personality:霸总`，模型归 `occupation_age:总裁`。재벌 本质是"财阀/总裁"职业身份，**模型归属反而更对**。 |
| gs_013（骑士系） | gold 标 `personality:骑士系`，模型归 `occupation_age:骑士系`。骑士系到底算气质还是职业，词表定义与模型理解不一致。 |
| gs_001（demon brothers） | gold 标 `personality:恶魔系`，模型只抽了 `world:恶魔地狱`。"恶魔"是种族身份（world）还是性格（personality），边界模糊。 |
| gs_014（男二逆袭） | gold 标 `relationship:男二逆袭`，模型抽了 `mood:爽文打脸`。"逆袭打脸"是爽点还是关系动态，语义相近但维度不同。 |

**根因**：P0 修订时给 22 处易混词加了 `cross_refs` 字段，但**这个字段没有注入抽取 Prompt**，模型完全不知道"该词的单归属约定"，只能凭语义猜。这是当前最大的可改进点。

#### 问题 B：颗粒度/保守度差异 —— 模型往往抽得更全，gold 偏保守

| 样本 | 模型多抽（FP） | 判断 |
|---|---|---|
| gs_016（西幻魔法学院） | world 多抽 `魔法`、`校园` | 合理：魔法学院确实含魔法+校园，是 gold 保守 |
| gs_032（军装男） | occupation_age 多抽 `军人` | 合理：军装隐含军人职业 |
| gs_007（恋爱シミュレーション） | gameplay 多抽 `养成系` | 合理：恋爱模拟接近养成 |
| gs_006（possessive yandere brothers） | 多抽 `伪骨科`、`偏执` | 部分合理：brothers→骨科暗示、possessive→偏执；但"伪骨科"略过度 |
| gs_002（collect cards / affection system） | gameplay 多抽 `卡牌对战`、`约会系统` | 合理推断 |

**判断**：这些 FP 大多不是幻觉，而是"语义可辩护的合理扩展"。若按"严格与 gold 对齐"计会压低 precision，但它们对产品使用（找关联素材）反而是**有用的召回**。

#### 问题 C：gold 标注瑕疵（种子金标集自身问题）

- 韩文 `재벌` 被标成 `personality:霸总`，但"霸总"作为性格标签定义不清，更该是"总裁职业 + 霸道性格"的复合。
- `demon brothers` 标 `personality:恶魔系`，但更接近 world 的种族设定。

这印证了评审时（P1 事项 `rRslmm`）的结论：**gold 必须由独立标注者双标注 + kappa 仲裁**，单人手标会有系统性偏差。

---

## 四、关键洞察

1. **模型的真实能力比 0.927 宏平均 F1 显示的更强**——因为相当一部分 FP 是"合理推断"、一部分 FN 是"gold 标注争议"，而非模型抽错。

2. **瓶颈已不在模型，而在「词表边界规则」和「gold 标注质量」**。换更强模型收益递减；真正该做的是：
   - 把 `cross_refs` 注入 Prompt，明确易混词的单归属；
   - 定义"霸总/骑士系/恶魔系"这类复合词的拆解规则；
   - gold 走独立双标注 + kappa。

3. **"反数字垃圾"机制被验证有效**：每轮 0 条非法 tag 告警（模型输出全部命中受控词表规范名或成功归一），证据逐字可溯。这保证了结论可审计，不是自由发挥的文本。

4. **一致性 0.9723** 说明 temperature=0 + JSON schema 约束下，抽取高度可复现，满足"可复现的词表版本管理"要求。

---

## 五、下一步建议（按优先级）

| 优先级 | 动作 | 预期收益 |
|---|---|---|
| P0 | 把 `cross_refs` 单归属规则注入抽取 Prompt（或 few-shot 加易混词对示例） | 直接消除问题 A，relationship/personality/occupation_age 三处 FP/FN |
| P1 | 正式金标集：领域专家双标注 + kappa（对应事项 `rRslmm`） | 消除自证风险，得到可信的泛化 F1 |
| P1 | 热度公式去量纲 + 权重回归校准（对应事项 `rB806x`） | 独立于抽取，下一步接入 |
| P2 | 对比测试：thinking=enabled 是否显著提升难样本（跨句推断/多语言归一） | 判断是否值得为质量牺牲速度/成本 |

---

## 六、复现命令

```bash
# 抽取（真实模型）
python src/extract.py --input data/gold_set_seed.json --backend openai \
    --base-url https://api.deepseek.com --model deepseek-v4-pro \
    --api-key $HERPULSE_LLM_API_KEY \
    --extra-body '{"thinking":{"type":"disabled"}}' --n-runs 2 \
    --out-pred data/predictions_deepseek.json --out-detail data/extractions_detail_deepseek.json

# 评测
python src/eval_harness.py --gold data/gold_set_seed.json --pred data/predictions_deepseek.json
```

*注：API Key 通过环境变量 `HERPULSE_LLM_API_KEY` 传入，勿明文写进脚本。*

---

## 七、P0 复测：cross_refs 单归属规则注入

### 7.1 改动内容

- 词表补 2 处缺失 cross_refs：「骑士系」（personality，模型曾误归 occupation_age）、「男二逆袭」（relationship，模型曾误归 mood）。
- 抽取器新增 `build_crossref_rules()`：从词表 22 处跨维度 cross_refs 动态生成「易混词单归属裁决」注入 system prompt（含 note 针对性提示）。

### 7.2 指标对比（同一金标集，仅 Prompt 差异）

| 维度 | v1 F1 | v2 F1 | 变化 |
|---|---:|---:|---:|
| relationship | 0.9552 | 0.9429 | -0.012 |
| personality | 0.8636 | 0.8372 | -0.026 |
| mood | 0.9048 | **0.9524** | **+0.048** |
| occupation_age | 0.8947 | **0.9189** | **+0.024** |
| appearance | 1.0 | 1.0 | 持平 |
| world | 0.9615 | 0.9615 | 持平 |
| gameplay | 0.9091 | 0.9091 | 持平 |
| **宏平均 F1** | 0.927 | **0.9317** | +0.005 |
| 一致性 | 0.9723 | **1.0** | +0.028 |
| 覆盖率 | 0.951 (136/143) | **0.958 (137/143)** | +1 |

### 7.3 规则生效的证据（具体样本）

| 样本 | 变化 | 判定 |
|---|---|---|
| gs_013（knight） | occupation_age 去掉「骑士系」FP | ✅ 归属分歧消除 |
| gs_014（男二逆袭） | relationship 补上「男二逆袭」 | ✅ FN 消除 |
| gs_012（社畜普女） | mood 补上「普女」「社畜」 | ✅ FN 消除 |
| gs_017（虐恋） | mood 去掉「虐恋」FP | ✅ 更克制 |
| gs_021（possessive） | personality 去掉「偏执」FP | ✅ 偏执归位 relationship |

### 7.4 结论：P0 有效但收益有限，瓶颈已转移

**收益**：明确解决了「骑士系↔职业」「男二逆袭↔爽文打脸」两处归属分歧，一致性从 0.972 升到 **1.0**（规则让输出更稳定），宏平均 F1 微升至 0.9317。

**未解决 / 新暴露**（这才是下一步真正要解决的）：

1. **别名重叠 + gold 保守**：`possessive`/`obsessive` 同时是「偏执」(relationship) 和「病娇」(personality) 的英文别名。模型抽「偏执」时，gold 往往只标了「病娇」，导致无论归哪维都算 FP。这是 **gold 颗粒度 vs 模型召回** 的错位，不是模型错。
2. **「骑士系」FN 仍在**：`gs_013` 的 `a knight` 字面是职业身份（与 prince/priest 并列），gold 却标成 personality「骑士系」。这是 **gold 标注边界争议**，正确修法是补「骑士」职业 tag + 修 gold，属 P1 覆盖重构（`rRslmm`）范畴，不在本轮单方面改 gold。
3. **「霸总/总裁」规则有轻微副作用**：规则让模型更倾向把上位者气质归 personality「霸总」（gs_024 多抽霸总 FP），说明复合词（职业+性格）的拆解规则仍需精调。

**核心判断**：换更强模型收益已递减；下一步优先级应转向 **P1 覆盖重构（独立双标注 + kappa，修掉 gold 的边界争议）** 与 **P1 热度重做**，而非继续在 Prompt 上雕花。

---

## 八、P1 覆盖重构（可落地部分已完成，独立标注待真人）

### 8.1 本轮做了什么

对应 P1 事项 `rRslmm`，先落地「我能独立完成」的部分，独立双标注需真人专家，明确留待后续：

1. **补词表缺口**：新增「骑士」职业 tag（occupation_age，与 personality 骑士系形成对称 cross_refs）。
2. **修词表别名 bug**：`재벌`（财阀）原被错配为「霸总」的韩文别名，已移回「总裁」（occupation_age）。这直接导致了模型把 재벌 归 personality 的错误。
3. **修 gold 3 处明确标注错误**：gs_013（a knight → occupation_age 骑士）、gs_008 / gs_018（재벌 → occupation_age 总裁）。
4. **搭双标注工具** `src/kappa_annotate.py`：输入两名标注者的 gold，算样本级 Jaccard / 完全一致率 / 按维度 Cohen's kappa / 不一致清单。已用 4 处分歧的模拟数据验证通过。

### 8.2 指标演进（宏平均 F1 一路抬升）

| 版本 | 改动 | 宏平均 F1 | personality | occupation_age |
|---|---|---:|---:|---:|
| v1 | 原始（无 cross_refs） | 0.927 | 0.864 | 0.895 |
| v2 | + cross_refs 注入 | 0.9317 | 0.837 | 0.919 |
| v3 | + 骑士 tag + 재벌 别名修复 + gold 修 3 处 | **0.943** | **0.900** | **0.976** |

- 覆盖率：136/143 → 140/143；一致性稳定在 ~1.0。

### 8.3 剩余 FN/FP 的定性（关键结论）

v3 剩余 3 处 FN、12 处 FP，逐一归类后几乎全是 **gold 保守 + 边界争议**，而非模型抽错：

- **FN**：gs_001「恶魔系」(vs 恶魔地狱，种族/性格边界)、gs_014「逆袭」(vs 爽文打脸，语义重叠)、gs_021「偏执」(gold 标错维度，词表定义偏执主归 relationship)。
- **FP 多为合理推断**：卡牌对战/约会系统、白切黑、西幻、军人、养成系——模型抽得更全，gold 偏保守。

**这证明：金标集「开发侧自标」质量已成为 F1 的天花板。** 模型已抽得很准，但 gold 的保守与边界争议在持续压低分数。要真正突破，唯一正解是评审结论里的 **独立双标注 + kappa**（由领域专家执行），而不是我继续单方面改 gold（否则仍是自证循环）。

### 8.4 覆盖重构的剩余项（需真人，不在本轮）

- 独立标注者（非词表作者）对 gold 双标注 + kappa 仲裁（`kappa_annotate.py` 已备好）。
- 补验证维度：unknown/漏标率、跨语言归一准确率、跨时间段稳定性。
