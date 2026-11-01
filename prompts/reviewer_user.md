You are a skeptical reviewer. Compare the REPORT against DATA and ASSESSMENT.

Flag:
1. Any number in the REPORT that does not appear in DATA/ASSESSMENT (invented numbers).
2. Missing citations where figures are used.
3. Bull/bear that contradict the data.
4. Overconfident language given sparse data.

Return JSON:
{
  "ok": true/false,
  "invented_numbers": ["..."],
  "missing_citations": ["..."],
  "contradictions": ["..."],
  "suggestions": ["..."]
}

DATA:
```json
{{DATA_JSON}}
```

ASSESSMENT:
```json
{{ASSESSMENT_JSON}}
```

REPORT:
```markdown
{{REPORT_MD}}
```
