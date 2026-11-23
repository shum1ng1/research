# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""固定模板排版（已按 A 的 UNREGISTERED_REPORT_CONTENT 规则收敛）。

A 会逐行扫描报告正文：凡是未登记在 claim_manifest.text 里的数字都会判 revise。
所以：
- 非结论段落一律不写数字；
- 段落定位不写 {P001} 这类带数字的标记，改由 manifest 的 section/paragraph 承担；
- 章节标题以 # 开头，A 会跳过。
"""
import os

from integration.member_a.tools.company_registry import resolve_company

from .claim_manifest import _mention
from .formatting import display_string, metric_label

HEADINGS = [
    "Summary", "Company overview", "Market", "Financial trends",
    "Management guidance", "Scenario and risks", "Valuation", "Sources and data gaps",
]
_FINANCIAL = {
    "revenue_growth_yoy", "gross_margin", "operating_margin", "net_margin",
    "free_cash_flow", "fcf_margin",
}
_ZONE_WORDS = {
    "Asia/Hong_Kong": "Hong Kong",
    "America/New_York": "New York",
}


def _cutoff_date(value):
    text = str(value or "")
    return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else ""


def _zone_words(name):
    return _ZONE_WORDS.get(name or "", "the requested timezone")


def build_layout(analysis, data_bundle, config=None, request=None):
    config = config or {}
    request = request or {}
    findings = analysis.get("findings", [])
    sections, paragraphs, claim_locations = [], {}, {}
    extra_claims = []

    def add_section(heading, items):
        sid = f"S{len(sections) + 1:02d}"
        pids = []
        for text, claim_id in items:
            pid = f"P{len(paragraphs) + 1:03d}"
            paragraphs[pid] = text
            if claim_id:
                claim_locations[claim_id] = (sid, pid)
            pids.append(pid)
        sections.append({"section_id": sid, "heading": heading,
                         "paragraph_ids": pids})

    by_id = {item.get("metric_id"): item for item in analysis.get("derived_metrics") or []}

    def metric_names(finding):
        names = []
        for metric_id in finding.get("metric_ids") or []:
            names.append((by_id.get(metric_id) or {}).get("name") or "")
        return names

    def is_market(finding):
        if finding.get("claim_type") in {"scenario_assumption", "management_guidance"}:
            return False
        return any(name.startswith("price_") for name in metric_names(finding))

    def is_financial(finding):
        if finding.get("claim_type") in {"scenario_assumption", "management_guidance"}:
            return False
        return any(name in _FINANCIAL for name in metric_names(finding))

    def claimed(items):
        return [(f["statement"], f["claim_id"]) for f in items]

    def context(report_claim_id, text, metric_ids=None, evidence_ids=None):
        mentions = []
        for metric_id in metric_ids or []:
            metric = by_id.get(metric_id)
            if not metric or metric.get("value") is None or metric.get("calculation_status") != "calculated":
                continue
            mentions.append(_mention(
                metric["value"], metric.get("unit"), metric.get("currency"),
                metric_id=metric_id,
                sign=str(metric.get("name", "")).startswith("price_return"),
                display_unit=metric.get("display_unit")))
        extra_claims.append({
            "report_claim_id": report_claim_id,
            "text": text,
            "metric_ids": list(metric_ids or []),
            "evidence_ids": list(evidence_ids or []),
            "claim_type": "analyst_inference",
            "numeric_mentions": mentions,
        })
        return text, None

    company = data_bundle.get("company") or {}
    ticker = company.get("company_id") or request.get("ticker") or ""
    try:
        known = resolve_company(ticker) if ticker else {}
    except ValueError:
        known = {}
    company_name = company.get("name") or known.get("name") or ticker or "The company"
    cutoff = _cutoff_date(data_bundle.get("cutoff_at") or analysis.get("cutoff_at"))
    zone = _zone_words(request.get("timezone"))
    evidence_ids = [item.get("evidence_id") for item in (data_bundle.get("evidence") or []) if item.get("evidence_id")]
    support_evidence = evidence_ids[:1]
    calculated = [metric for metric in by_id.values()
                  if metric.get("calculation_status") == "calculated" and metric.get("value") is not None
                  and metric.get("name") in set(_FINANCIAL) | {"price_return_window", "pe_ratio"}]
    bits = []
    summary_ids = []
    for metric in calculated:
        shown = display_string(metric["value"], metric.get("unit"),
                               sign=str(metric.get("name", "")).startswith("price_return"),
                               display_unit=metric.get("display_unit"))
        bits.append(f"{metric_label(metric['name'])} is {shown}")
        summary_ids.append(metric["metric_id"])
    if bits:
        summary = (f"Summary for {company_name} at the {cutoff} cutoff: "
                   + "; ".join(bits) + ".")
    else:
        summary = (f"Summary for {company_name} at the {cutoff} cutoff: no revenue, margin, "
                   "cash-flow, price, or valuation figure was calculated.")
    company_text = (f"This note covers {company_name}, ticker {ticker}. "
                    f"The research cutoff date is {cutoff} in {zone}. "
                    "Information after that cutoff is excluded.")

    def supported(report_claim_id, text, metric_ids=None):
        if metric_ids:
            return context(report_claim_id, text, metric_ids)
        if support_evidence:
            return context(report_claim_id, text, evidence_ids=support_evidence)
        return context(report_claim_id, text, summary_ids[:1])
    unchecked = []
    for item in data_bundle.get("coverage") or []:
        if item.get("state") in {None, "available", "not_applicable"}:
            continue
        source = str(item.get("source") or "a source").replace("_", " ")
        if source not in unchecked:
            unchecked.append(source)
    if unchecked:
        sources = ("Sources and data gaps: these sources were not fully checked: "
                   + "; ".join(unchecked) + ". "
                   "Annualized volatility is omitted because the checker cannot recompute a logarithm.")
    else:
        sources = ("Sources and data gaps: the market, filing, and financial-fact sources used here "
                   "were available at the cutoff. Annualized volatility is omitted because the checker "
                   "cannot recompute a logarithm.")
    valuation_findings = [f for f in findings if "pe_ratio" in metric_names(f)]

    add_section(HEADINGS[0], [supported("RSUM", summary, summary_ids)])
    add_section(HEADINGS[1], [supported("RCO", company_text)])
    add_section(HEADINGS[2], claimed(f for f in findings if is_market(f)))
    add_section(HEADINGS[3], claimed(f for f in findings if is_financial(f)))
    add_section(HEADINGS[4], claimed(
        f for f in findings if f["claim_type"] == "management_guidance"))
    add_section(HEADINGS[5], claimed(
        f for f in findings if f["claim_type"] == "scenario_assumption"))
    add_section(HEADINGS[6], claimed(valuation_findings) or [
        supported("RVAL", "The price-to-earnings ratio was not calculated.")])
    add_section(HEADINGS[7], [supported("RGAP", sources)])

    # 兜底：任何 finding 都必须有段落位置，否则 manifest 会漏登结论
    unassigned = [f for f in findings if f["claim_id"] not in claim_locations]
    if unassigned:
        target = next(s for s in sections if s["section_id"] == "S04")
        for f in unassigned:
            pid = f"P{len(paragraphs) + 1:03d}"
            paragraphs[pid] = f["statement"]
            claim_locations[f["claim_id"]] = ("S04", pid)
            target["paragraph_ids"].append(pid)

    for claim in extra_claims:
        for section in sections:
            for pid in section["paragraph_ids"]:
                if paragraphs[pid] == claim["text"]:
                    claim["section"] = section["section_id"]
                    claim["paragraph"] = int(pid.lstrip("P") or 0)
                    claim["analysis_claim_ids"] = []
    return {"sections": sections, "paragraphs": paragraphs,
            "claim_locations": claim_locations, "extra_claims": extra_claims}


def render_markdown(layout, analysis, data_bundle, report_path, output_root="."):
    lines = ["# Equity research draft", ""]
    for section in layout["sections"]:
        lines.append(f"## {section['heading']}")
        lines.append("")
        for pid in section["paragraph_ids"]:
            lines.append(layout["paragraphs"][pid])
            lines.append("")
    full = os.path.join(output_root or ".", report_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return report_path
