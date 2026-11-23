# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""claim_manifest：按 A 的 schemas/member_a.py 与 review_checks.py 生成。

A 硬要求的字段：
  claim: report_claim_id, text, section, paragraph, analysis_claim_ids,
         metric_ids, evidence_ids, claim_type, numeric_mentions
  mention: display_text, display_unit, precision + (metric_id|assumption_id)

display_text 必须逐字出现在 claim["text"] 中；text 里不能有其他未登记数字，
所以正文段落与这里都由同一份 statement 生成。
"""
from .formatting import present_number, prefix_from_bundle, rounding_policy


def _mention(value, unit, currency=None, metric_id=None, assumption_id=None,
             sign=False, display_unit=None):
    precision = 1
    number_text, unit_name, _prose = present_number(
        value, unit, precision, sign=sign, display_unit=display_unit)
    return {
        "display_text": number_text,
        "display_unit": unit_name,
        "precision": precision,
        "metric_id": metric_id,
        "assumption_id": assumption_id,
        "rounding_policy": rounding_policy(precision),
        "source_value": value,
    }


def build_claim_manifest(analysis, layout, data_bundle, config=None):
    config = config or {}
    prefix = prefix_from_bundle(analysis.get("input_bundle_id", "DEMO_B001"))
    derived = {d["metric_id"]: d for d in analysis.get("derived_metrics", [])}
    assumptions = {a["assumption_id"]: a for a in analysis.get("assumptions", [])}
    evidence = {e["evidence_id"]: e for e in data_bundle.get("evidence", [])}
    findings = analysis.get("findings", [])

    manifest, citations = [], {}
    for finding in findings:
        claim_id = finding["claim_id"]
        location = layout["claim_locations"].get(claim_id)
        if not location:
            continue
        section_id, paragraph_id = location
        text = layout["paragraphs"][paragraph_id]

        mentions = []
        for mid in finding.get("metric_ids", []):
            d = derived.get(mid)
            if not d or d.get("value") is None or d.get("calculation_status") != "calculated":
                continue
            mentions.append(_mention(
                d["value"], d["unit"], d.get("currency"), metric_id=mid,
                sign=d["name"].startswith("price_return"),
                display_unit=d.get("display_unit")))
        # 只有情景结论才允许把 assumption 数字当结论展示（A 的 ASSUMPTION_AS_FACT）
        if finding["claim_type"] == "scenario_assumption":
            for aid in finding.get("assumption_ids", []):
                a = assumptions.get(aid)
                if a and a.get("value") is not None:
                    mentions.append(_mention(
                        a["value"], a.get("unit", "ratio"), a.get("currency"),
                        assumption_id=aid))

        manifest.append({
            "report_claim_id": f"R{len(manifest) + 1:03d}",
            "text": text,
            "section": section_id,
            "paragraph": int(paragraph_id.lstrip("P") or 0),
            "analysis_claim_ids": [claim_id],
            "metric_ids": list(finding.get("metric_ids", [])),
            "evidence_ids": list(finding.get("evidence_ids", [])),
            "claim_type": finding["claim_type"],
            "numeric_mentions": mentions,
        })

        for evid in finding.get("evidence_ids", []):
            ev = evidence.get(evid)
            if not ev:
                continue
            sid = ev.get("source_id")
            if (sid, evid) not in citations:
                citations[(sid, evid)] = {
                    "citation_id": f"{prefix}CIT{len(citations) + 1:02d}",
                    "source_id": sid,
                    "evidence_id": evid,
                    "inline_tag": f"[{sid}/{evid}]",
                }

    return {
        "claim_manifest": manifest,
        "citations": list(citations.values()),
        "charts": [],
        "numeric_mentions": [m for c in manifest for m in c["numeric_mentions"]],
    }
