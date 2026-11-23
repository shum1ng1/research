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

HEADINGS = [
    "Summary", "Company", "Market", "Financial trends",
    "Management guidance", "Scenario and risks", "Valuation", "Sources and gaps",
]


def build_layout(analysis, data_bundle, config=None):
    config = config or {}
    findings = analysis.get("findings", [])
    sections, paragraphs, claim_locations = [], {}, {}

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
        return any(name.startswith("price_") for name in metric_names(finding))

    def is_valuation(finding):
        return "pe_ratio" in metric_names(finding)

    # A 的 Reviewer 要求：报告正文每一行非标题文字都必须登记进 claim_manifest。
    # 因此正文只放有 claim_id 的结论段落；无依据的引导句、风险/缺口说明保留在
    # 结构化字段（risks/limitations）里，不写进 Markdown，避免语义覆盖不全。
    def claimed(items):
        return [(f["statement"], f["claim_id"]) for f in items]

    add_section(HEADINGS[0], [])  # 结论摘要：标题即可，正文结论见各章节
    add_section(HEADINGS[1], [])  # 公司概况：口径信息在 DataBundle，不重复叙述

    add_section(HEADINGS[2], claimed(
        f for f in findings if f["claim_type"] == "analyst_inference" and is_market(f)))

    add_section(HEADINGS[3], claimed(
        f for f in findings if f["claim_type"] == "reported_fact"))

    add_section(HEADINGS[4], claimed(
        f for f in findings if f["claim_type"] == "management_guidance"))

    add_section(HEADINGS[5], claimed(
        f for f in findings if f["claim_type"] == "scenario_assumption"))

    add_section(HEADINGS[6], claimed(
        f for f in findings if f["claim_type"] == "analyst_inference" and is_valuation(f)))

    add_section(HEADINGS[7], [])  # 引用/缺口：引用有 inline tag，缺口在 JSON 字段

    # 兜底：任何 finding 都必须有段落位置，否则 manifest 会漏登结论
    unassigned = [f for f in findings if f["claim_id"] not in claim_locations]
    if unassigned:
        target = next(s for s in sections if s["section_id"] == "S04")
        for f in unassigned:
            pid = f"P{len(paragraphs) + 1:03d}"
            paragraphs[pid] = f["statement"]
            claim_locations[f["claim_id"]] = ("S04", pid)
            target["paragraph_ids"].append(pid)

    return {"sections": sections, "paragraphs": paragraphs,
            "claim_locations": claim_locations}


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
