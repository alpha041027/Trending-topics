# HerPulse 海外部署指南（当前方案：只采集出海）

## 一句话结论

整条 pipeline 里，**只有「采集」这一步必须出海**：

| 环节 | 脚本 | 是否必须海外网络 | 说明 |
|------|------|------------------|------|
| 1. 采集 | `src/fetchers.py` | **必须海外** | 直连 Reddit / AO3，国内被墙 |
| 2. 抽取 | `src/extract.py` | 不必需 | 调 DeepSeek API（`api.deepseek.com` 国内可达） |
| 3. 聚合 | `src/aggregate.py` | 不必需 | 纯本地计算 |
| 4. 渲染 | `src/build_dashboard.py` | 不必需 | 纯本地计算 |

**当前决策**：采用「轻量」粒度——**海外（GitHub Actions）只跑采集，corpus 回传后在国内跑后半段三步**。

项目零第三方依赖（纯 Python 标准库 urllib/json），海外环境只需 Python 3.10+，无需 `pip install`。

## 两条链路

```
海外（GitHub Actions，免费 runner）
  fetch_only.sh  →  data/corpus_reddit_*.json + fanwork_ao3_*.json
        │  Artifacts 下载回本地
        ▼
国内（本地 / 任意机器）
  run_local.sh   →  抽取(DeepSeek) → 聚合 → 渲染看板 HTML
```

## 目录文件

| 文件 | 用途 |
|------|------|
| `fetch_only.sh` | **海外**只采集（Reddit + AO3），产出 corpus |
| `run_local.sh` | **国内**后半段（抽取→聚合→渲染），吃 corpus |
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
4. 每次 run 结束后，在 `Artifacts` 里下载 `herpulse-corpus`（含 `corpus_reddit_*.json` 与 `fanwork_ao3_*.json`）。

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

## 备选：全链路出海

若后续想整套放海外定时跑通（省掉回传人工步骤），用 `run_pipeline.sh` + `Dockerfile` + `crontab.example`。此时才需要给海外环境配 `HERPULSE_LLM_API_KEY`。

其他可选路径（不展开，需要时再说）：
- **海外 VPS**（最稳，几美元/月）：`fetch_only.sh` 或 `run_pipeline.sh` + crontab 定时。
- **云函数**（成本极低）：把 `fetch_reddit()` + `to_corpus()` 封装成函数入口，只采集。
- **本地 + 海外代理**（临时验证）：`export https_proxy=http://127.0.0.1:7890` 后直接跑。

---

## 已知缺口

- **AO3 fanwork 信号未并入热度**：`fetch_only.sh` 采集的 fanwork（二创产量）落盘为独立文件 `fanwork_ao3_*.json`，`aggregate.py` 尚未消费它。需给 `aggregate.py` 增加 `--fanwork` 参数，把 `{tag: works}` 映射进对应设定点的 `fanwork` 信号（对应热度公式决策④）。接入前，看板热度信号主要来自 Reddit `social/rank`。
- **GitHub runner 数据中心 IP**：Reddit 可能返回 429 限流，AO3 通常正常。若 Reddit 频繁被限，需换 VPS + 住宅代理。

## 安全提示

- API key 只经环境变量注入（本地），**不要写进代码或提交进 git**。
- 私有仓库 + `.gitignore` 排除 `data/*_*_*.json`（含时间戳的采集产物），避免历史数据被误提交。
