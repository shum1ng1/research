"""Build one auditable layout for the Markdown and A4 drafts."""
from pathlib import Path
from urllib.parse import urlsplit

from integration.member_a.tools.company_registry import resolve_company

from .claim_manifest import _mention
from .formatting import display_string, metric_label
from .report_evidence import business_passages

HEADINGS = (
    "Summary", "Company overview", "Market", "Financial trends",
    "Management guidance", "Scenario and risks", "Valuation", "Sources and data gaps",
)
_FINANCIAL = {
    "revenue_growth_yoy", "gross_margin", "operating_margin", "net_margin",
    "free_cash_flow", "fcf_margin",
}
_TABLE_METRICS = _FINANCIAL | {"price_return_window", "pe_ratio"}
_ZONE_WORDS = {"Asia/Hong_Kong": "Hong Kong", "America/New_York": "New York"}
_SOURCE_LABELS = {
    "yahoo_finance": "Yahoo Finance historical prices",
    "sec_edgar": "SEC EDGAR filings",
    "sec_companyfacts": "SEC Company Facts",
    "management_materials": "Management materials",
    "seeking_alpha": "Seeking Alpha",
}
_TABLE_HEADER = ("Metric", "Value", "Period or basis")


def _period_label(metric, inputs):
    if metric["name"] == "pe_ratio":
        eps = inputs.get(next(iter(metric.get("input_metric_ids") or []), None), {})
        basis = {"annual": "Full-year EPS", "year": "Full-year EPS",
                 "quarter": "Quarterly EPS", "ttm": "Trailing twelve-month EPS"}.get(
                     metric.get("eps_period_type") or eps.get("period_type"), "EPS period unspecified")
        period_end = metric.get("eps_period_end") or eps.get("period_end")
        if period_end:
            basis += f" ending {period_end}"
        if metric.get("price_date"):
            basis += f" / close {metric['price_date']}"
        return basis
    period = metric.get("period") or {}
    end = period.get("period_end")
    if not end:
        return "Comparable period unavailable"
    if metric["name"] == "price_return_window":
        return f"{period.get('period_start')} to {end}"
    label = {"quarter": "Quarter", "annual": "Year", "half_year": "Six months",
             "nine_months": "Nine months"}.get(period.get("period_type"), "Period")
    if metric["name"] == "revenue_growth_yoy":
        prior = inputs.get(metric["input_metric_ids"][-1], {}).get("period_end")
        if prior:
            return f"{label} ended {end} vs. {prior}"
    return f"{label} ended {end}"


def build_layout(analysis, data_bundle, config=None, request=None, chart=None):
    request = request or {}
    company = data_bundle.get("company") or {}
    ticker = company.get("company_id") or request.get("ticker") or ""
    try:
        known = resolve_company(ticker) if ticker else {}
    except ValueError:
        known = {}
    name = company.get("name") or known.get("name") or ticker or "Equity research"
    cutoff = (request.get("as_of") or data_bundle.get("cutoff_at") or analysis.get("cutoff_at") or "")[:10]
    zone = _ZONE_WORDS.get(request.get("timezone"), request.get("timezone") or "research timezone")
    subtitle = " | ".join(part for part in (ticker, company.get("market") or known.get("market"),
                                          f"Research cutoff {cutoff} ({zone})" if cutoff else "") if part)
    findings = analysis.get("findings", [])
    by_id = {m["metric_id"]: m for m in analysis.get("derived_metrics") or []}
    inputs = {m["metric_id"]: m for m in data_bundle.get("metrics") or []}
    evidence = {e["evidence_id"]: e for e in data_bundle.get("evidence") or []}
    calculated = [m for m in by_id.values() if m["name"] in _TABLE_METRICS
                  and m.get("calculation_status") == "calculated" and m.get("value") is not None]
    metric_ids = [m["metric_id"] for m in calculated]
    sections, paragraphs, locations, extra_claims, blocks = [], {}, {}, [], {}

    def add_section(heading):
        section = {"section_id": f"S{len(sections) + 1:02d}", "heading": heading, "paragraph_ids": []}
        sections.append(section)
        return section

    def add(text, *, finding=None, claim=None, ids=(), eids=(), mentions=(), kind="text", **block):
        section = sections[-1]
        sid = section["section_id"]
        nr = len(paragraphs) + 1
        pid = f"P{nr:03d}"
        paragraphs[pid] = text
        section["paragraph_ids"].append(pid)
        blocks[pid] = {"kind": kind, **block}
        if finding:
            locations[finding["claim_id"]] = (sid, pid)
        if claim and (ids or eids):
            extra_claims.append({
                "report_claim_id": claim, "section": sid, "paragraph": nr,
                "text": text, "analysis_claim_ids": [], "metric_ids": list(ids), "evidence_ids": list(eids),
                "claim_type": "reported_fact" if claim.startswith("RCO") else "analyst_inference",
                "numeric_mentions": list(mentions),
            })
        elif claim:
            blocks[pid]["note"] = True
        return pid

    add_section("Summary")
    add((f"This note reviews {name}'s financial performance, share-price history and valuation at the research cutoff. "
         "The table shows the reporting period behind each calculation."),
        claim="RSUM", ids=metric_ids[:1])
    rows, mentions = [], []
    for metric in by_id.values():
        name_key = metric["name"]
        if name_key not in _TABLE_METRICS:
            continue
        value = metric.get("value") if metric.get("calculation_status") == "calculated" else None
        sign = name_key == "price_return_window"
        if value is not None:
            mentions.append(_mention(value, metric["unit"], metric.get("currency"),
                                     metric_id=metric["metric_id"], sign=sign, display_unit=metric.get("display_unit")))
        elif name_key not in {"revenue_growth_yoy", "price_return_window", "pe_ratio"}:
            continue
        shown = display_string(value, metric["unit"], sign=sign, display_unit=metric.get("display_unit"),
                               unavailable="Unavailable")
        if name_key == "pe_ratio" and value is not None:
            shown += "x"
        label = "Year-over-year revenue growth" if name_key == "revenue_growth_yoy" else metric_label(name_key).capitalize()
        rows.append((label, shown, _period_label(metric, inputs) if value is not None else "See report notes"))
    if rows:
        table = [_TABLE_HEADER, ("---", "---", "---"), *rows]
        table_text = "\n".join("| " + " | ".join(row) + " |" for row in table)
        add(table_text, claim="RTABLE", ids=metric_ids, mentions=mentions, kind="table", rows=rows)

    add_section("Company overview")
    business = business_passages(data_bundle)
    for i, (sentence, evidence_id) in enumerate(business, 1):
        add(f'The annual filing describes the business as follows. “{sentence}”',
            claim=f"RCO{i}", eids=[evidence_id])
    if not business:
        add("A business description could not be substantiated from the available annual-filing excerpts.",
            claim="RCO")

    findings_by_section = {heading: [] for heading in HEADINGS[2:]}
    for finding in findings:
        kind = finding["claim_type"]
        names = {by_id[mid]["name"] for mid in finding.get("metric_ids") or [] if mid in by_id}
        if kind == "scenario_assumption":
            target = "Scenario and risks"
        elif kind == "management_guidance":
            target = "Management guidance"
        elif "pe_ratio" in names:
            target = "Valuation"
        elif names == {"price_volatility_window"} and by_id[finding["metric_ids"][0]].get("value") is None:
            target = "Sources and data gaps"
        elif any(item.startswith("price_") for item in names):
            target = "Market"
        else:
            target = "Financial trends"
        findings_by_section[target].append(finding)

    for heading in HEADINGS[2:7]:
        add_section(heading)
        for finding in findings_by_section[heading]:
            add(finding["statement"], finding=finding)
        if heading == "Market" and chart:
            add(chart["record"]["caption"], claim="RCHART",
                ids=chart["record"]["metric_ids"], kind="chart", chart=chart)
        elif heading == "Financial trends":
            yoy = next((m for m in by_id.values() if m["name"] == "revenue_growth_yoy"), None)
            if yoy and yoy.get("calculation_status") != "calculated":
                add("Comparable prior-year revenue is missing or zero, so year-over-year growth is unavailable.",
                    claim="RFIN", ids=[yoy["metric_id"]])
            elif yoy and len(yoy.get("input_metric_ids", [])) == 2:
                current, prior = (inputs.get(mid) for mid in yoy["input_metric_ids"])
                if current and prior:
                    values = [display_string(m["value"], m["unit"]) for m in (current, prior)]
                    add(f"Revenue was {values[0]} for the period ended {current['period_end']}, "
                        f"compared with {values[1]} for the period ended {prior['period_end']}.",
                        claim="RFIN", ids=yoy["input_metric_ids"],
                        eids=list(dict.fromkeys(m["evidence_id"] for m in (current, prior)
                                               if m.get("evidence_id") in evidence)),
                        mentions=[_mention(m["value"], m["unit"], m.get("currency"), metric_id=m["metric_id"])
                                  for m in (current, prior)])
            fcf = [m["metric_id"] for m in calculated if m["name"] in {"free_cash_flow", "fcf_margin"}]
            if fcf:
                add("The free cash flow calculation subtracts capital expenditure from operating cash flow. "
                    "Its margin measures cash generation relative to revenue for the same period.",
                    claim="RFCF", ids=fcf)
        elif heading == "Management guidance" and not sections[-1]["paragraph_ids"]:
            add("Current financial guidance was not substantiated by the available source passages.",
                claim="RMGMT")
        elif heading == "Scenario and risks":
            scenario_ids = [mid for f in findings if f["claim_type"] == "scenario_assumption"
                            for mid in f["metric_ids"]]
            if scenario_ids:
                add("The scenario holds the selected margin fixed. Changes in costs or sales mix could move the actual margin "
                    "away from that assumption. This report does not estimate a scenario share price.",
                    claim="RRISK", ids=scenario_ids)
            else:
                add("A margin scenario could not be constructed from the available periods.", claim="RRISK")
        elif heading == "Valuation" and not sections[-1]["paragraph_ids"]:
            add("The price-to-earnings ratio was unavailable at the research cutoff.", claim="RVAL")

    add_section("Sources and data gaps")
    for finding in findings_by_section["Sources and data gaps"]:
        add(finding["statement"], finding=finding)
    coverage = data_bundle.get("coverage") or []
    available, gaps = [], []
    for item in coverage:
        key = item.get("source") or item.get("category") or "source"
        label = _SOURCE_LABELS.get(key, key.replace("_", " "))
        if item.get("state") == "available":
            available.append(label)
        elif item.get("state") not in {None, "not_applicable"}:
            gaps.append(label)
    if available:
        add("Available sources include " + ", ".join(dict.fromkeys(available)) + ".", claim="RSOURCE",
            ids=metric_ids[:1])
    if gaps:
        add("Coverage remains incomplete for " + ", ".join(dict.fromkeys(gaps)) + ".",
            claim="RGAP", ids=metric_ids[:1])
    volatility = next((m for m in by_id.values() if m["name"] == "price_volatility_window"
                       and m.get("calculation_status") != "calculated"), None)
    if volatility and not findings_by_section["Sources and data gaps"]:
        add("Annualized volatility is omitted because the formula checker cannot verify the calculation.",
            claim="RVOL", ids=[volatility["metric_id"]])
    if not sections[-1]["paragraph_ids"]:
        add("Source coverage is limited to the materials supplied for this run.", claim="RSOURCE")

    referenced = [eid for c in extra_claims for eid in c["evidence_ids"]]
    referenced.extend(eid for f in findings for eid in f.get("evidence_ids") or [])
    input_ids = [mid for m in calculated for mid in m.get("input_metric_ids") or []]
    referenced.extend(inputs[mid].get("evidence_id") for mid in input_ids if mid in inputs)
    source_ids = [evidence[eid]["source_id"] for eid in referenced if eid in evidence]
    if chart:
        source_ids.extend(f.get("source_id") for f in data_bundle.get("price_files") or []
                          if f.get("path") == by_id[chart["record"]["metric_ids"][0]].get("price_file"))
    sources = {s["source_id"]: s for s in data_bundle.get("sources") or []}
    references = {}
    for sid in source_ids:
        source = sources.get(sid) or {}
        source = sources.get(source.get("parent_source_id"), source) if source.get("source_type") == "SEC_XBRL_FACT" else source
        url = source.get("source_url") or ""
        if urlsplit(url).scheme in {"https", "http"} and urlsplit(url).hostname:
            label = ("Yahoo Finance historical prices" if source.get("source_type") == "YAHOO_FINANCE"
                     else source.get("title") or source.get("document_type") or "Source document")
            references.setdefault(url, label)

    return {"title": name, "subtitle": subtitle, "sections": sections, "paragraphs": paragraphs,
            "claim_locations": locations, "extra_claims": extra_claims, "blocks": blocks,
            "references": [(label, url) for url, label in references.items()]}


def render_markdown(layout, report_path, output_root="."):
    lines = [f"# {layout['title']}", f"### {layout['subtitle']}", ""]
    for section in layout["sections"]:
        lines.extend((f"## {section['heading']}", ""))
        for pid in section["paragraph_ids"]:
            block = layout["blocks"][pid]
            text = layout["paragraphs"][pid]
            if block.get("note"):
                text = f"### {text}"
            lines.extend((text, ""))
    if layout["references"]:
        lines.extend(("### Referenced documents", ""))
        for label, url in layout["references"]:
            lines.append(f"### [{label}]({url})")
    path = Path(output_root, report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path
