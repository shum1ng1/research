Analyze the company using ONLY the DATA JSON below.

Chain-of-thought order (put the reasoning into assessment.growth / .profitability / .solvency — do NOT invent extra wrapper keys):
1) YoY revenue → 2) margins → 3) D/E & current ratio → 4) vs industry (qualitative) → 5) overall_health 1–5.

Copy ratio numbers exactly from DATA.ratios (or null). Do not recalculate differently.

CRITICAL output rules:
- Return ONE flat JSON object. Top-level keys MUST be exactly:
  ticker, as_of, ratios, assessment, bull_case, bear_case, confidence, sources_cited
- Do NOT wrap the whole object under another "assessment" key (no {"assessment": {...}} nesting).
- Do NOT use alternate names like health_score, key_metrics, chain_of_thought, or nested "assessment.assessment".
- assessment must be an object with ONLY: growth (string), profitability (string), solvency (string), overall_health (integer 1–5).
- bull_case and bear_case are top-level strings (not inside assessment).
- confidence is "high" | "medium" | "low".
- sources_cited is an array of citation tags from DATA.

Target schema (match this shape exactly):
```json
{{SCHEMA_JSON}}
```

DATA:
```json
{{DATA_JSON}}
```
