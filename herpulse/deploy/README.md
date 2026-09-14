# HerPulse 海外部署指南（当前方案：只采集出海）

## 一句话结论

整条 pipeline 里，**只有「采集」这一步必须出海**：

| 环节 | 脚本 | 是否必须海外网络 | 说明 |
|------|------|------------------|------|
| 1. 采集 | `src/fetchers.py` | **必须海外** | 直连 Reddit / AO3 / Google Trends，国内被墙 |
| 2. 抽取 | `src/extract.py` | 不必需 | 调 DeepSeek API（`api.deepseek.com` 国内可达） |
| 3. 聚合 | `src/aggregate.py` | 不必需 | 纯本地计算 |
| 4. 渲染 | `src/build_dashboard.py` | 不必需 | 纯本地计算 |

**当前决策**：采用「轻量」粒度——**海外（GitHub Actions）只跑采集，corpus 回传后在国内跑后半段三步**。

项目零第三方依赖（纯 Python 标准库 urllib/json），海外环境只需 Python 3.10+，无需 `pip install`。

## 两条链路

```
海外（GitHub Actions，免费 runner）
  fetch_only.sh  →  data/corpus_reddit_*.json + fanwork_ao3_*.json + search_google_*.json
        │  Artifacts 下载回本地
        ▼
国内（本地 / 任意机器）
  run_local.sh   →  抽取(DeepSeek) → 聚合 → 渲染看板 HTML
```

## 目录文件

| 文件 | 用途 |
|------|------|
| `fetch_only.sh` | **海外**只采集（Bluesky 社媒 + AO3 二创 + Google Trends 搜索），产出 corpus |
| `run_local.sh` | **国内**后半段（抽取→聚合→渲染），吃 corpus，自动检测并注入 fanwork/search 真值 |
| `github-actions.yml` | GitHub Actions 定时采集（免费海外 runner） |
| `run_pipeline.sh` | 完整链路（采集+后半段），供「全链路出海」备选 |
| `Dockerfile` | 完整链路容器化，供「全链路出海」备选 |
| `crontab.example` | VPS 定时任务（备选） |

---

## 主流程：海外采集 + 国内后半段

### 第一步：GitHub Actions 定时采集（海外，免费）

1. 代码推到**私有仓库**（含 `herpulse/` 目录与 `deploy/github-actions.yml`）。
2. 把 `deploy/github-actions.yml` 复制为仓库根 `.github/workflows/herpulse-fetch.yml`。
3. `Actions` 页手动 `Run workflow` 验证一次（首次建议先手动，确认 Reddit 不被限流）。
4. 每次 run 结束后，在 `Artifacts` 里下载 `herpulse-corpus`（含 `corpus_reddit_*.json`、`fanwork_ao3_*.json`、`search_google_*.json`）。

无需任何 Secrets——采集步骤不调 LLM，不碰 API key。

### 第二步：国内跑后半段

```bash
# 1. 把下载的 corpus_reddit_*.json 放到 herpulse/data/ 下
# 2. 注入 DeepSeek key（不要写进文件）
export HERPULSE_LLM_API_KEY="sk-xxxx"

# 3. 跑后半段（自动取最新 corpus_reddit_*.json；也可显式指定）
cd herpulse
bash deploy/run_local.sh data/corpus_reddit_20260914_120000.json

# 产出：../herpulse-dashboard-live.html
```

自测（不联网、不耗 token）：`HERPULSE_LLM_BACKEND=mock bash deploy/run_local.sh`

---

## 四信号来源（热度公式 social/fanwork/search/rank）

| 信号 | 数据源 | 语义 | 采集方式 |
|------|--------|------|----------|
| social 社媒声量 | Bluesky 公开 API | 帖子的 likes + replies×10（真实声量） | `--source bluesky` |
| fanwork 二创产量 | AO3 | 该设定点 tag 的作品总数（真实二创产量） | `--source ao3 --tags-from-vocab` |
| search 搜索热度 | Google Trends | 该词相对自身历史热度的相对值（0-100） | `--source trends --tags-from-vocab` |
| rank 榜单名次 | 采集内名次 | 同平台内排序位置（第 1 名最热） | 随社媒采集产出 |

### ⚠️ Google Trends 搜索信号的语义局限（务必理解）

Google Trends 免费接口返回的 **0-100 是「该词相对自身历史峰值的归一化」，不是跨词的绝对搜索量**。因此：

- **不能**拿「yandere 的 92 比 slow burn 的 55 更热」下结论——92 只说明 yandere 正处在自己历史热度的 92% 高位。
- **可以**用的信息：`interest`（近 7 天均值，越接近 100 = 正处历史高位）+ `momentum`（近 7 天 vs 前 7 天的涨跌 %，跨词可比，用于突增检测）。
- 看板会把 search 信号如实标注为「Google Trends 真实相对热度(自身历史归一化)」，不伪装成绝对搜索量。

若后续需要「跨词绝对搜索量级」，需改用付费数据服务（如 Semrush / Ahrefs），不在当前 MVP 范围。

---

## 备选：全链路出海

若后续想整套放海外定时跑通（省掉回传人工步骤），用 `run_pipeline.sh` + `Dockerfile` + `crontab.example`。此时才需要给海外环境配 `HERPULSE_LLM_API_KEY`。

其他可选路径（不展开，需要时再说）：
- **海外 VPS**（最稳，几美元/月）：`fetch_only.sh` 或 `run_pipeline.sh` + crontab 定时。
- **云函数**（成本极低）：把 `fetch_reddit()` + `to_corpus()` 封装成函数入口，只采集。
- **本地 + 海外代理**（临时验证）：`export https_proxy=http://127.0.0.1:7890` 后直接跑。

---

## 已知缺口

- **Google Trends 限流与数据中心 IP**：Google Trends 对数据中心 IP（GitHub runner）可能返回 429 或要求验证码。已做 1.5s 请求间隔 + 重试退避，但 53 个 high_signal 关键词一次全采约需 80s+，偶发 429 时部分词会失败（记录 `error` 字段、search 信号置 0，不中断整体）。若持续失败，需换 VPS + 住宅代理，或减少 `HERPULSE_TRENDS_LIMIT`。
- **Reddit 可能 429 限流**：同属数据中心 IP 问题。AO3、Bluesky 通常正常。若 Reddit 频繁被限，需换 VPS + 住宅代理。
- **金标集仍为开发侧自标**：`data/gold_set_seed.json` 有自证风险，正式评测需真人双标注 + kappa（工具 `src/kappa_annotate.py` 已备）。

## 安全提示

- API key 只经环境变量注入（本地），**不要写进代码或提交进 git**。
- 私有仓库 + `.gitignore` 排除 `data/*_*_*.json`（含时间戳的采集产物），避免历史数据被误提交。
