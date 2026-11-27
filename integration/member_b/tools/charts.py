"""Plot the retained closing-price series over the same window as price return."""
from math import isfinite
from pathlib import Path
from xml.sax.saxutils import escape

from .price_metrics import _aware, read_price_rows


def build_price_chart(analysis, bundle, run_dir, output_root, data_root):
    metric = next((m for m in analysis.get("derived_metrics", [])
                   if m["name"] == "price_return_window" and m["calculation_status"] == "calculated"), None)
    if not metric:
        return None
    files = bundle.get("price_files") or []
    price_file = next((f for f in files if f["path"] == metric.get("price_file")), None)
    cutoff = _aware(bundle.get("cutoff_at"))
    if not price_file or cutoff is None:
        raise ValueError("The calculated price return has no matching price file or cutoff")
    rows, _ = read_price_rows(price_file, cutoff, data_root)
    start = metric["period"]["period_start"]
    end = metric["period"]["period_end"]
    if len(rows) < 2 or (rows[0][0], rows[-1][0]) != (start, end):
        raise ValueError("The chart and calculated price return have different price windows")
    values = [row[1] for row in rows]
    if not all(isfinite(value) for value in values):
        raise ValueError("The closing-price series contains a non-finite value")

    low, high = min(values), max(values)
    span = (rows[-1][2] - rows[0][2]).total_seconds()
    if span <= 0:
        raise ValueError("The closing-price series needs distinct timestamps")
    points = [((timestamp - rows[0][2]).total_seconds() / span,
               (value - low) / (high - low) if high > low else 0.5)
              for _, value, timestamp in rows]
    ticks = [(0, low), (0.5, (low + high) / 2), (1, high)] if high > low else [(0.5, low)]
    unit = price_file.get("currency") or bundle.get("company", {}).get("currency")
    title = "Daily closing price" + (f" ({unit})" if unit else "")
    path = Path(run_dir, "charts", "price_history.svg")
    svg = Path(output_root, path)
    svg.parent.mkdir(parents=True, exist_ok=True)
    coordinates = " ".join(f"{56 + x * 466:.2f},{150 - y * 116:.2f}" for x, y in points)
    grid = "\n".join(
        f'<line x1="56" x2="522" y1="{150 - fraction * 116:g}" y2="{150 - fraction * 116:g}" stroke="#dce5ec"/>'
        f'<text x="50" y="{154 - fraction * 116:g}" text-anchor="end">{value:,.2f}</text>'
        for fraction, value in ticks)
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="550" height="184" viewBox="0 0 550 184">\n'
        '<g font-family="sans-serif" font-size="11" fill="#172a3a">\n'
        '<rect width="550" height="184" fill="#fff"/>\n'
        f'<text x="56" y="19" font-size="13">{escape(title)}</text>\n'
        f'{grid}\n<polyline points="{coordinates}" fill="none" stroke="#176780" stroke-width="2"/>\n'
        f'<text x="56" y="169">{escape(start)}</text><text x="522" y="169" text-anchor="end">{escape(end)}</text>\n'
        '</g></svg>\n', encoding="utf-8")
    return {
        "record": {"chart_id": "CHART1", "path": path.as_posix(),
                   "metric_ids": [metric["metric_id"]],
                   "caption": f"Daily closing prices from {start} through {end}."},
        "title": title, "points": points, "ticks": ticks, "start": start, "end": end,
    }
