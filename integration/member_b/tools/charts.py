# Vendored next to member B's Writer. Charts use only values Python already calculated.
"""Write one SVG chart and the caption numbers Reviewer can check.

Bar geometry is not labeled with a second numeric scale. The only numbers written
into the file are the same display strings registered on the chart claim.
"""
from __future__ import annotations

import os

from .claim_manifest import _mention

CHART_NAMES = (
    "revenue_growth_yoy",
    "gross_margin",
    "net_margin",
    "fcf_margin",
    "operating_margin",
    "price_return_window",
    "pe_ratio",
)


def _label(metric, mention):
    return f"{metric['name']} {mention['display_text']}"


def build_charts(analysis, report_dir, output_root):
    """Return (charts, caption, mentions, metric_ids, note).

    caption is None when nothing calculated is available to draw.
    """
    derived = []
    for metric in analysis.get("derived_metrics") or []:
        if metric.get("name") not in CHART_NAMES:
            continue
        if metric.get("calculation_status") != "calculated" or metric.get("value") is None:
            continue
        mention = _mention(
            metric["value"], metric.get("unit"), metric.get("currency"),
            metric_id=metric["metric_id"],
            sign=str(metric.get("name", "")).startswith("price_return"),
            display_unit=metric.get("display_unit"),
        )
        if not mention.get("display_text"):
            continue
        derived.append((metric, mention))
    if not derived:
        return [], None, [], [], "图表未绘制：没有已计算的收入、利润率或价格指标。"

    mentions = [mention for _metric, mention in derived]
    metric_ids = [metric["metric_id"] for metric, _mention in derived]
    labels = [_label(metric, mention) for metric, mention in derived]
    caption = "图表 " + "；".join(labels) + "。见 ![chart](../charts/metrics.svg)。"
    # Caption must contain every display string exactly.
    for mention in mentions:
        if mention["display_text"] not in caption:
            caption += mention["display_text"]

    chart_dir = os.path.join(output_root or ".", report_dir, "charts")
    os.makedirs(chart_dir, exist_ok=True)
    path = os.path.join(chart_dir, "metrics.svg")
    width, height = 480, 36 + 28 * len(labels)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="12" y="22" font-size="14">calculated metrics</text>',
    ]
    max_abs = max(abs(float(metric["value"])) for metric, _mention in derived) or 1.0
    for index, (metric, mention) in enumerate(derived):
        y = 36 + index * 28
        bar = int(180 * min(abs(float(metric["value"])) / max_abs, 1.0))
        parts.append(f'<rect x="12" y="{y}" width="{bar}" height="16" fill="#2f6fad"/>')
        # Visible number is the registered display string, not a second raw scale.
        parts.append(
            f'<text x="210" y="{y + 13}" font-size="13">{_xml(labels[index])}</text>'
        )
    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(parts) + "\n")
    rel = os.path.join(report_dir, "charts", "metrics.svg").replace("\\", "/")
    charts = [{
        "chart_id": "CHART1",
        "path": rel,
        "metric_ids": metric_ids,
        "caption": caption,
        "numeric_mentions": mentions,
    }]
    return charts, caption, mentions, metric_ids, ""


def _xml(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
