"""
ELEC7082 multi-step equity-research pipeline.

  Data → Analysis → Writer → Reviewer

Usage (from project root):
  python -m pipeline --ticker AAPL
  python -m pipeline --ticker AAPL --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as `python -m pipeline`
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.analysis_agent import analyze, overall_health
from agents.data_agent import collect
from agents.reviewer_agent import review
from agents.writer_agent import write_report
from llm_client import has_api_key


def run(ticker: str, *, dry_run: bool = False, news_limit: int = 5) -> dict:
    ticker = ticker.upper().strip()
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Data agent — fetching {ticker} via yfinance …")
    data = collect(ticker, news_limit=news_limit)
    ratios = data.get("ratios") or {}
    print("      Ratios (Python-computed):")
    for k, v in ratios.items():
        print(f"        {k}: {v}")

    use_dry = dry_run or not has_api_key()
    if dry_run:
        print("[info] --dry-run: skipping LLM calls")
    elif not has_api_key():
        print("[warn] HKU_GENAI_API_KEY unset — falling back to dry-run for LLM steps")

    print("[2/4] Analysis agent …")
    assessment = analyze(data, dry_run=use_dry)
    health = overall_health(assessment)
    print(f"      health={health} confidence={assessment.get('confidence')}")

    print("[3/4] Writer agent …")
    report_md = write_report(data, assessment, dry_run=use_dry)
    out_path = reports_dir / f"{ticker}.md"
    out_path.write_text(report_md, encoding="utf-8")
    print(f"      wrote {out_path}")

    print("[4/4] Reviewer agent …")
    review_result = review(data, assessment, report_md, dry_run=use_dry)
    print(f"      ok={review_result.get('ok')} invented={review_result.get('invented_numbers')}")

    meta_path = reports_dir / f"{ticker}.meta.json"
    meta = {
        "ticker": ticker,
        "dry_run": use_dry,
        "ratios": ratios,
        "assessment": assessment,
        "review": review_result,
        "report_path": str(out_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"[done] meta → {meta_path}")
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ELEC7082 equity-research agent pipeline")
    parser.add_argument("--ticker", default="AAPL", help="Stock ticker (default: AAPL)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run tools/ratios only; skip LLM (or use placeholders)",
    )
    parser.add_argument("--news-limit", type=int, default=5)
    args = parser.parse_args(argv)
    run(args.ticker, dry_run=args.dry_run, news_limit=args.news_limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
