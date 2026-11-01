# ELEC7082 Equity Research Agent

多步骤 LLM 股票研究 agent（HKU ELEC7082 小组项目骨架）  
Multi-step LLM equity-research agent scaffold (Lecture 6 pipeline).

```
Data → Analysis → Writer → Reviewer
```

Tools compute **all numbers in Python** (yfinance). The LLM only interprets injected DATA and must not invent figures.

## Setup / 安装

```bash
cd elec7082-agent
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
- `reports/{TICKER}.md` — report draft
- `reports/{TICKER}.meta.json` — ratios, assessment JSON, reviewer flags

## Layout / 目录

```
tools/       yfinance wrappers (price, financials, ratios, news)
agents/      data → analysis → writer → reviewer
prompts/     system.md + assessment schema + user templates (edit these!)
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
