"""get_news_headlines(ticker, limit) — recent headlines via yfinance."""

from __future__ import annotations

from typing import Any

import yfinance as yf


def _extract_link(item: dict, content: dict) -> str:
    if item.get("link"):
        return str(item["link"])
    if item.get("url"):
        return str(item["url"])
    canon = content.get("canonicalUrl")
    if isinstance(canon, dict) and canon.get("url"):
        return str(canon["url"])
    click = content.get("clickThroughUrl")
    if isinstance(click, dict) and click.get("url"):
        return str(click["url"])
    return ""


def get_news_headlines(ticker: str, limit: int = 5) -> dict[str, Any]:
    """Return up to `limit` recent news headlines for ticker."""
    ticker = ticker.upper().strip()
    t = yf.Ticker(ticker)
    try:
        raw = t.news or []
    except Exception:
        raw = []

    headlines = []
    for item in raw[: max(0, limit)]:
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        title = (
            item.get("title")
            or content.get("title")
            or item.get("headline")
            or ""
        )
        provider = content.get("provider") if isinstance(content.get("provider"), dict) else {}
        publisher = (
            item.get("publisher")
            or provider.get("displayName")
            or ""
        )
        published = (
            item.get("providerPublishTime")
            or content.get("pubDate")
            or item.get("pubDate")
            or ""
        )
        if title:
            headlines.append(
                {
                    "title": title,
                    "publisher": publisher,
                    "link": _extract_link(item, content),
                    "published": published,
                }
            )

    return {
        "ticker": ticker,
        "count": len(headlines),
        "headlines": headlines,
        "source": "yfinance.news",
    }
