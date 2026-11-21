# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""Writer Agent（B 负责的第二个 Agent）。

契约：writer_agent.run(request, config, data_bundle, analysis_bundle,
                       feedback=None) -> dict
- analysis_bundle 是 C 解开上一阶段外壳后的 payload，不是外壳本身。
- 只把结构化分析转成固定模板，不新增数字、不新增结论、不改写 evidence_id。
- 未导出 PDF：pdf_path / page_count 一律 null，不声称已检查页数。
"""
from integration.member_b.tools import claim_manifest, report_template
from integration.member_b.tools.formatting import prefix_from_bundle
from integration.member_b.analysis_agent import _envelope, _revision


def _validate(request, data_bundle, analysis_bundle):
    errors = []
    if not analysis_bundle.get("analysis_id"):
        errors.append("INVALID_ANALYSIS: 缺 analysis_id")
    if analysis_bundle.get("input_bundle_id") != data_bundle.get("bundle_id"):
        errors.append("INVALID_ANALYSIS: input_bundle_id 与 DataBundle 不一致")
    if analysis_bundle.get("input_bundle_revision") != data_bundle.get("revision"):
        errors.append("INVALID_ANALYSIS: input_bundle_revision 与 DataBundle 不一致")
    return errors


def run(request, config, data_bundle, analysis_bundle, feedback=None):
    config = config or {}
    request = request or {}
    errors = _validate(request, data_bundle, analysis_bundle)
    if errors:
        return _envelope(request.get("run_id", "unknown"), "writer", "failed",
                         {}, errors=errors, config=config)

    prefix = prefix_from_bundle(data_bundle["bundle_id"])
    run_id = request.get("run_id", "unknown")
    run_dir = config.get("run_dir") or f"runs/{run_id}"
    output_root = config.get("output_root", ".")
    ticker = request.get("ticker") or data_bundle.get("company", {}).get("company_id", "REPORT")
    report_path = f"{run_dir}/reports/{ticker}_draft.md"

    layout = report_template.build_layout(analysis_bundle, data_bundle, config)
    report_template.render_markdown(layout, analysis_bundle, data_bundle,
                                    report_path, output_root)
    claims = claim_manifest.build_claim_manifest(
        analysis_bundle, layout, data_bundle, config)

    warnings = []
    if feedback and feedback.get("target_agent") == "writer":
        warnings.append("writer 返工：只重渲染受影响段落与 claim_manifest，"
                        "不改动 AnalysisBundle")
    elif feedback and feedback.get("target_agent") not in (None, "writer"):
        warnings.append(f"feedback.target_agent={feedback.get('target_agent')}"
                        " 不属于 writer 的处理范围")

    partial = any(d.get("name") == "price_volatility_window"
                  and d.get("calculation_status") != "calculated"
                  for d in analysis_bundle.get("derived_metrics", []))
    status = "partial" if partial else "success"

    payload = {
        "report_id": f"{prefix}RPT001",
        "revision": _revision(feedback),
        "input_bundle_id": data_bundle["bundle_id"],
        "input_bundle_revision": data_bundle.get("revision", 1),
        "input_analysis_id": analysis_bundle["analysis_id"],
        "input_analysis_revision": analysis_bundle.get("revision", 1),
        "template_id": config.get("template_id", "equity_note_v0.1"),
        "language": request.get("language", "zh-CN"),
        "report_path": report_path,
        "pdf_path": None,
        "page_count": None,
        "sections": layout["sections"],
        "claim_manifest": claims["claim_manifest"],
        "charts": claims["charts"],
        "citations": claims["citations"],
    }
    return _envelope(run_id, "writer", status, payload, warnings=warnings,
                     artifacts=[report_path], config=config)
