# ELEC7082 Equity Research Agent

多步骤 LLM 股票研究 agent（HKU ELEC7082 小组项目骨架）  
Multi-step LLM equity-research agent scaffold (Lecture 6 pipeline).

```
Data → Analysis → Writer → Reviewer
```

Tools compute **all numbers in Python** (yfinance). The LLM only interprets injected DATA and must not invent figures.

还没有 API key 时，先看 [示例输出](#sample-output)。那里有一份 2026-11-02（香港时间）的 AAPL 历史示例，打开就能看，不必先跑程序。

## Setup / 安装

克隆本仓库后，文件夹名是 `research`。下面的命令都在这个目录里执行。

```bash
cd research
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# 把港大 Developer 订阅的 Primary key 填进 .env
# Paste Primary key from https://developer.hku.hk/profile into HKU_GENAI_API_KEY=
```

## Environment / 环境变量

Default path is **HKU Azure DeepSeek (Foundry)**.

| Variable | Meaning |
|----------|---------|
| `HKU_GENAI_API_KEY` | Student GenAI subscription key (`api-key` header) |
| `HKU_GENAI_BASE_URL` | default `https://api.hku.hk/foundry/student` |
| `HKU_GENAI_DEPLOYMENT` or `HKU_GENAI_MODEL` | default `DeepSeek-V3.2` (also `DeepSeek-V4-Flash`, `DeepSeek-V4-Pro`) |

Chat endpoint: `POST {BASE_URL}/models/chat/completions` with OpenAI-style body `{"model", "messages", ...}`. **No `api-version` query param.**

**Never commit `.env`.** DeepSeek quotas are roughly **~3 req/min**, **~300/day**, **~900/month** (portal may cap DeepSeek ~500/day) — keep dry-runs for tool debugging.

### Optional: Azure OpenAI gateway

If you prefer GPT models on the OpenAI student gateway:

```bash
HKU_GENAI_BASE_URL=https://api.hku.hk/openai/student
HKU_GENAI_DEPLOYMENT=gpt-4.1-mini
HKU_GENAI_API_VERSION=2025-04-01-preview
```

The client auto-switches to `/deployments/{name}/chat/completions?api-version=...` when the base URL is not Foundry.

## Run / 运行

```bash
# Tools + ratios only (no LLM) — works without API key
python3 -m pipeline --ticker AAPL --dry-run

# Full chain (needs HKU_GENAI_API_KEY; uses DeepSeek by default)
python3 -m pipeline --ticker AAPL
```

Outputs:
- `reports/{TICKER}.md` — report draft（这次运行新写的；`reports/*.md` 默认不进 git）
- `reports/{TICKER}.meta.json` — ratios, assessment JSON, reviewer flags

<a id="sample-output"></a>

## 示例输出（不用 API key 也能看）

下面这份 AAPL 示例是 **2026-11-02（香港时间）** 一次完整模型运行留下的历史输出（`dry_run` 为 false）。打开就能看，用来认识流水线的输入和输出。说明写在示例文件外面；报告正文和 meta 里除路径以外的字段保持当时的原文。

日期以示例文件里写的为准：**财年 2025**（报表截止 2025-09-30），**市场数据截至 2026-10-30**（meta 里的 `assessment.as_of`）。这是那一天的模型草稿。提交这份示例时没有按当前行情重跑。请把它当作模型生成的历史样例：财务内容未经独立核实，也不能当作投资建议。

### 输入

当时的输入是股票代码 `AAPL`。比率由 Python 工具算好后交给模型；模型只做解读、写草稿和审阅。这份示例对应那一次已完成的运行，提交本示例时没有再次调用 API。

### 输出

- 研究报告草稿：[examples/AAPL.md](examples/AAPL.md)
- 元数据 JSON：[examples/AAPL.meta.json](examples/AAPL.meta.json)（比率、assessment、审阅结果）
- 同目录的简短说明：[examples/README.md](examples/README.md)

### 没有 key 时怎么看

不用创建 `.env`，也不用执行 `pipeline`。在上面的链接里打开报告和 JSON 即可。

### 用自己的 key 跑一遍

先完成上面的安装，把你自己的 Primary key 只写进本机 `.env` 的 `HKU_GENAI_API_KEY`（不要提交 `.env`）。在仓库根目录执行：

```bash
python3 -m pipeline --ticker AAPL
```

新的草稿和 meta 会写到 `reports/`，用的是你运行当时拉取的数据，和这份 2026-11-02 的示例不是同一份结果。只想检查工具、不调用模型时，执行 `python3 -m pipeline --ticker AAPL --dry-run`。

### 读的时候单独记住

这几句是仓库说明，不是模型写进报告里的句子：

- `review.ok`（本例为 true）是同一次流水线里审阅模型给出的结论，属于 LLM review。它不能代替独立的准确性核验。
- `assessment.confidence`（本例为 `high`）是模型未经校准的自我报告，不是统计意义上的概率。
- `assessment.assessment.overall_health`（本例为 4，满分 5）是模型自己打的健康分。
- 元数据里的 `report_path` 原来是生成机器上的绝对路径 `/workspace/elec7082-agent/reports/AAPL.md`。入库时改成了相对仓库根目录的 `examples/AAPL.md`，这样换一台电脑也能对上这份报告。这是相对原文件唯一的改动。

## Layout / 目录

```
tools/       yfinance wrappers (price, financials, ratios, news)
agents/      data → analysis → writer → reviewer
prompts/     system.md + assessment schema + user templates (edit these!)
examples/    historical AAPL sample (report + meta); open without an API key
llm_client.py
pipeline.py
```

Teammates: edit files under `prompts/` to change tone/schema; keep ratio math in `tools/ratios.py`.

## Course rules baked in

- Never invent numbers; say unavailable
- Cite sources like `[income_statement …]`
- One bull + one bear; confidence high/medium/low
- Analysis step: JSON mode, temperature 0
- CoT: YoY revenue → margins → D/E & current ratio → vs industry → health 1–5

## License

Course coursework scaffold — for ELEC7082 team use.
