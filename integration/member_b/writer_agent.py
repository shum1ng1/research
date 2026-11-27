# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""Writer Agent（B 负责的第二个 Agent）。

契约：writer_agent.run(request, config, data_bundle, analysis_bundle,
                       feedback=None) -> dict
- analysis_bundle 是 C 解开上一阶段外壳后的 payload，不是外壳本身。
- 只把结构化分析转成固定模板，不新增数字、不新增结论、不改写 evidence_id。
- 从已计算指标生成表格，从同一行情窗口绘制股价图。
- 导出 A4 PDF，填写真实的 pdf_path 与 page_count。超过 10 页则本阶段失败。
"""
import os

from integration.member_b.tools import claim_manifest, report_template
from integration.member_b.tools.charts import build_price_chart
from integration.member_b.tools.formatting import prefix_from_bundle
from integration.member_b.tools.pdf_export import export_a4_pdf
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
    report_dir = config.get("report_dir") or f"{run_dir}/reports"
    output_root = config.get("output_root", ".")
    ticker = request.get("ticker") or data_bundle.get("company", {}).get("company_id", "REPORT")
    report_path = f"{report_dir}/{ticker}_draft.md"

    warnings = []
    try:
        chart = build_price_chart(analysis_bundle, data_bundle, run_dir, output_root or ".",
                                  config.get("data_root") or output_root or ".")
        layout = report_template.build_layout(analysis_bundle, data_bundle, config, request, chart)
    except (OSError, ValueError) as exc:
        return _envelope(run_id, "writer", "failed", {},
                         errors=[{"code": "REPORT_INPUT", "message": str(exc), "retryable": False}], config=config)
    template_id = config.get("template_id", "equity_note_v0.1")
    if (config.get("llm") or {}).get("enabled"):
        from integration.llm_author import author_paragraphs
        from integration.member_a.tools.agent_llm import LLMError
        try:
            paragraphs = author_paragraphs(layout, analysis_bundle, data_bundle, config)
        except LLMError as exc:
            return _envelope(run_id, "writer", "failed", {},
                             errors=[{"code": "LLM_ERROR", "message": str(exc), "retryable": False}],
                             config=config)
        for paragraph_id, text in paragraphs.items():
            layout["paragraphs"][paragraph_id] = text
        template_id = "equity_note_v0.1_model_paragraphs"
        warnings.append("报告段落由模型提交，且必须原样包含已审核的分析句；没有新数字。失败时不会改回模板。")
    claims = claim_manifest.build_claim_manifest(
        analysis_bundle, layout, data_bundle, config)
    try:
        report_template.render_markdown(layout, report_path, output_root or ".")
        pdf_rel = f"{report_dir}/{ticker}.pdf"
        page_count = export_a4_pdf(layout, os.path.join(output_root or ".", pdf_rel))
    except Exception as exc:
        return _envelope(run_id, "writer", "failed", {},
                         errors=[{"code": "PDF_EXPORT", "message": f"{type(exc).__name__}: {exc}",
                                  "retryable": False}],
                         warnings=warnings, config=config)
    if page_count > 10:
        return _envelope(run_id, "writer", "failed", {},
                         errors=[{"code": "FINAL_PAGE_COUNT",
                                  "message": f"PDF has {page_count} A4 pages; the limit is 10",
                                  "retryable": False}],
                         warnings=warnings, config=config)
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
        "template_id": template_id,
        "language": request.get("language", "zh-CN"),
        "report_path": report_path,
        "pdf_path": pdf_rel,
        "page_count": page_count,
        "sections": layout["sections"],
        "claim_manifest": claims["claim_manifest"],
        "charts": [chart["record"]] if chart else [],
        "citations": claims["citations"],
    }
    return _envelope(run_id, "writer", status, payload, warnings=warnings,
                     artifacts=[report_path, pdf_rel] + ([chart["record"]["path"]] if chart else []), config=config)
