Write a short equity research draft in Markdown (≤ ~800 words) for {{TICKER}}.

Output Markdown ONLY. Do NOT wrap the answer in a JSON object. Do NOT paste raw ASSESSMENT JSON. Do NOT open with a ```json fence. Start with a level-1 title.

Required structure (use these headings):
# {{TICKER}} — Equity research draft
## Overview
## Recent financials
## Ratios & health
## Factor chain
## Bull case / Bear case
## Confidence & data gaps

Content rules:
- Use ONLY facts from DATA and the ASSESSMENT below.
- Never invent numbers. If missing, say "data unavailable".
- Cite sources inline like [income_statement …] / [yfinance.info].
- In **Ratios & health**, cite pe, debt_to_equity, current_ratio, gross_margin from ASSESSMENT.ratios (or DATA.ratios) and state overall_health (1–5).
- In **Factor chain**, narrate growth → profitability → solvency → overall_health using ASSESSMENT.assessment text (prose, not a JSON dump).
- Bull and bear: one short paragraph each from ASSESSMENT.bull_case / bear_case.
- Do not add a buy/sell rating.

DATA:
```json
{{DATA_JSON}}
```

ASSESSMENT:
```json
{{ASSESSMENT_JSON}}
```
