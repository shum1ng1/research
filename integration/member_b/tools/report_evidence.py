"""Select short, attributable passages from the evidence already collected by Data."""
import re

from integration.member_a.tools.member_a_common import known_by


_BUSINESS = re.compile(
    r"\b(?:Microsoft|Cisco|Apple|the Company|We) (?:is a technology company|"
    r"designs and sells|designs, manufactures and markets|develop and support|offer an array of services)\b",
    re.I,
)
_GUIDANCE = re.compile(
    r"\b(?:we|the company|Microsoft|Cisco|Apple) (?:currently )?"
    r"(?:expects?|anticipates?|forecasts?|projects?)\b[^.!?]*"
    r"\b(?:revenue|net sales|margin|earnings per share|operating income)\b",
    re.I,
)


def source_passages(bundle, document_types, pattern):
    candidates = {}
    for source in bundle.get("sources") or []:
        if (source.get("document_type") in document_types and source.get("role") != "transport_snapshot"
                and known_by(source, bundle["cutoff_at"])):
            candidates[source["source_id"]] = source
    if not candidates:
        return []
    latest = max(source.get("public_date") or "" for source in candidates.values())
    passages = {}
    for evidence in bundle.get("evidence") or []:
        source = candidates.get(evidence.get("source_id"))
        if not source or (source.get("public_date") or "") != latest:
            continue
        quote = " ".join((evidence.get("quote") or "").split())
        for match in pattern.finditer(quote):
            end = re.search(r"[.!?](?=\s|$)", quote[match.start():])
            if end and 45 <= end.end() <= 320:
                sentence = quote[match.start():match.start() + end.end()]
                if not re.search(r"\d|https?://", sentence):
                    passages.setdefault(sentence, evidence["evidence_id"])
    return list(passages.items())


def business_passages(bundle):
    return source_passages(bundle, {"10-K"}, _BUSINESS)[:2]


def guidance_passage(bundle):
    passages = source_passages(bundle, {"8-K", "earnings_release", "earnings_transcript"}, _GUIDANCE)
    return next(((sentence, eid) for sentence, eid in passages if re.search(
        r"\b(?:next|upcoming|current|full.year|fiscal)\b", sentence, re.I)), None)
