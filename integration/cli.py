"""One command for the v0.1 four-stage run."""
from __future__ import annotations

import argparse
import json

from integration.orchestrator import orchestrate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="MSFT/CSCO v0.1 orchestration. Defaults to the offline synthetic fixture."
    )
    parser.add_argument("--ticker", required=True, help="MSFT or CSCO")
    parser.add_argument("--as-of", required=True, help="YYYY-MM-DD research date")
    parser.add_argument("--timezone", required=True, help="IANA timezone, for example Asia/Hong_Kong")
    parser.add_argument("--offline", action="store_true", help="Synthetic fixture. This is also the default.")
    parser.add_argument("--live", action="store_true",
                        help="Enable the configured live model. Default provider is official DeepSeek.")
    parser.add_argument("--provider", choices=("deepseek", "hku"), default="deepseek",
                        help="Live provider. deepseek reads DEEPSEEK_API_KEY; hku reads HKU_GENAI_API_KEY.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", default=".")
    parser.add_argument("--language", default="zh-CN")
    args = parser.parse_args(argv)
    if args.live and args.offline:
        parser.error("pass only one of --offline or --live")
    summary = orchestrate(
        args.ticker, args.as_of, args.timezone, offline=not args.live,
        output_root=args.output_root, run_id=args.run_id, language=args.language,
        provider=args.provider,
    )
    print(json.dumps({
        "final_status": summary.get("final_status"),
        "execution_status": summary.get("execution_status"),
        "review_decision": summary.get("review_decision"),
        "cutoff_at": summary.get("cutoff_at"),
        "report_path": summary.get("report_path"),
        "log_path": summary.get("log_path"),
        "errors": summary.get("errors"),
        "llm_transport": summary.get("llm_transport"),
    }, ensure_ascii=False, indent=2))
    status = summary.get("final_status")
    if status == "success":
        return 0
    if status == "manual_review":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
