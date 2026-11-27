"""Write the course delivery folder for MSFT, CSCO, and AAPL.

Offline output is a labeled demo. It is not a live verified research report.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from integration.orchestrator import orchestrate

TICKERS = ("MSFT", "CSCO", "AAPL")
AS_OF = "2026-11-13"
TIMEZONE = "Asia/Hong_Kong"


def run_pack(output_root, *, offline=True, provider="deepseek"):
    output_root = Path(output_root)
    folder = output_root / ("offline_demo" if offline else "live")
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for ticker in TICKERS:
        summary = orchestrate(
            ticker, AS_OF, TIMEZONE, offline=offline, output_root=str(folder / ticker),
            run_id=ticker, provider=provider, report_dir=None if offline else "reports",
        )
        results.append(summary)
    notice = folder / "README.md"
    if offline:
        notice.write_text(
            "# 离线演示包，不是课程正式稿\n\n"
            "这三个目录是合成夹具跑出来的 Data → Analysis → Writer → Reviewer 演示。\n"
            "数字不是 Yahoo、SEC 或 DeepSeek 的真实结果，不能当作 11 月 29 日要交的研究报告。\n"
            "正式稿请在本机配置 .env 后运行 `python3 -m integration.pack --live`。\n",
            encoding="utf-8",
        )
    else:
        notice.write_text(
            "# Live reports\n\n"
            "These folders are candidates for the course submission only after DEEPSEEK_API_KEY and SEC_USER_AGENT were set and the network calls succeeded.\n"
            "Check the PDF page count, citations, and figures yourself. Transcripts and Seeking Alpha still depend on local files under imports/{TICKER}/.\n",
            encoding="utf-8",
        )
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the MSFT, CSCO, and AAPL report pack.")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--provider", choices=("deepseek", "hku"), default="deepseek")
    parser.add_argument("--output-root", default="course_pack")
    args = parser.parse_args(argv)
    if args.live and args.offline:
        parser.error("pass only one of --offline or --live")
    offline = not args.live
    results = run_pack(args.output_root, offline=offline, provider=args.provider)
    failed = [item.get("ticker") for item in results if item.get("final_status") == "failed"]
    print("pack folder:", args.output_root)
    for item in results:
        print(item.get("ticker"), item.get("final_status"), item.get("report_path"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
