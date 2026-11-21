# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Traceable manual imports and bounded evidence retrieval; no paywall scraping."""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from integration.member_a.tools.member_a_common import coverage, known_by, stable_id


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "tr", "div", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract_text(raw, suffix):
    if suffix == ".pdf":
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw))
        text = "\n".join(f"\n[PDF page {i}]\n{page.extract_text() or ''}" for i, page in enumerate(reader.pages, 1))
        if len(text.strip()) < 100:
            raise ValueError("PDF needs OCR/manual verification; no usable text extracted")
        return text
    text = raw.decode("utf-8-sig")
    if suffix in {".html", ".htm"}:
        parser = PlainHTML()
        parser.feed(text)
        return "".join(parser.parts)
    if suffix not in {".txt", ".md", ".json"}:
        raise ValueError("Unsupported import type; use UTF-8 text, HTML or text PDF")
    return text


def _load_materials(company_id, cutoff_at, manifest_path, kind, *, store, config):
    source_name = "seeking_alpha" if kind == "third_party_research" else "management_materials"
    target = config.get("target_seekingalpha_materials", 2) if kind == "third_party_research" else config.get("target_management_events", 4)
    if not config.get("allow_manual_import", True):
        return dict(sources=[], evidence=[], coverage=[coverage(kind, source_name, "not_checked", target, 0, "MANUAL_IMPORT_DISABLED")])
    if not manifest_path:
        # Absence of an import does not prove the site has no coverage.
        return dict(sources=[], evidence=[], coverage=[coverage(kind, source_name, "not_checked", target, 0, "NO_MANIFEST_PROVIDED")])
    manifest = json.loads(store.resolve(manifest_path).read_text(encoding="utf-8"))
    records = manifest.get("documents", [])
    sources, evidence, errors = [], [], []
    imported = 0
    seen_sources = set()
    for record in records:
        if record.get("company_id") != company_id or record.get("category") != kind:
            continue
        required = ["document_type", "source_type", "title", "source_url", "language", "local_path", "access_method"]
        if any(not record.get(k) for k in required):
            errors.append("MANIFEST_REQUIRED_FIELDS_MISSING")
            continue
        parsed = urlparse(record["source_url"])
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.query:
            errors.append("INVALID_PUBLIC_SOURCE_URL")
            continue
        if kind == "third_party_research" and not (host == "seekingalpha.com" or host.endswith(".seekingalpha.com")):
            errors.append("SEEKING_ALPHA_HOST_MISMATCH")
            continue
        precision = "second" if record.get("available_at") else "date" if record.get("public_date") else "unknown"
        temporal = dict(available_at=record.get("available_at"), public_date=record.get("public_date"),
                        available_at_precision=precision, source_timezone=record.get("source_timezone", "America/New_York"))
        try:
            if not known_by(temporal, cutoff_at):
                errors.append("UNVERIFIED_OR_AFTER_CUTOFF")
                continue
            path = store.resolve(record["local_path"])
            raw = path.read_bytes()
            if len(raw) > 30_000_000:
                raise ValueError("import too large")
            text = extract_text(raw, path.suffix.lower())
        except (OSError, ValueError) as exc:
            errors.append("IMPORT_READ_OR_PARSE_FAILED")
            continue
        if not text.strip():
            errors.append("EMPTY_TEXT")
            continue
        source = store.source(company_id, record["title"], record["source_type"], record["document_type"],
                              record["source_url"], raw, available_at=temporal["available_at"],
                              public_date=temporal["public_date"], precision=precision,
                              source_timezone=temporal["source_timezone"], language=record["language"],
                              access_method=record["access_method"], version=record.get("document_version", "1"),
                              extension=path.suffix.lstrip("."), role="analytical",
                              author=record.get("author"), category=kind,
                              verification_status=record.get("verification_status", "not_checked"))
        if source["source_id"] in seen_sources:
            continue
        seen_sources.add(source["source_id"])
        text_path = store.root / "processed" / f"{source['source_id']}.txt"
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        source["extracted_text_path"] = store.ref(text_path)
        source["extracted_text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        sources.append(source)
        # Offsets refer to immutable extracted text. No generated summary is labelled a quote.
        for start in range(0, len(text), 1600):
            stop = min(start + 1900, len(text))
            quote = text[start:stop]
            eid = stable_id(company_id + "_E", source["source_id"], start, stop)
            evidence.append(dict(evidence_id=eid, source_id=source["source_id"], quote=quote,
                                 locator=dict(format="text_offsets", start=start, end=stop,
                                              text_path=source["extracted_text_path"]), language=record["language"]))
        imported += 1
    state = "available" if imported >= target and not errors else "partial" if imported else "error" if errors else "not_found"
    # User may explicitly record an observed restriction; never infer a paywall from a failed parse.
    declared = manifest.get("coverage", {}).get(company_id, {}).get(source_name)
    if not imported and declared in {"access_restricted", "not_checked", "not_found"}:
        state = declared
    return dict(sources=sources, evidence=evidence, warnings=errors,
                coverage=[coverage(kind, source_name, state, target, imported,
                                   errors[0] if errors else None if imported else "NO_MATCHING_IMPORTS")])


def load_management_materials(company_id, cutoff_at, manifest_path, *, store, config):
    return _load_materials(company_id, cutoff_at, manifest_path, "management_materials", store=store, config=config)


def load_seekingalpha_materials(company_id, cutoff_at, manifest_path, *, store, config):
    return _load_materials(company_id, cutoff_at, manifest_path, "third_party_research", store=store, config=config)


def search_evidence(bundle_id, query, filters, top_k, *, data_bundle):
    if bundle_id != data_bundle["bundle_id"]:
        raise ValueError("Unknown bundle_id")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    tokens = set(re.findall(r"[\w]+", query.lower()))
    matches = []
    for item in data_bundle["evidence"]:
        if any(item.get(k) != v for k, v in filters.items()):
            continue
        words = set(re.findall(r"[\w]+", item["quote"].lower()))
        score = len(tokens & words) / max(len(tokens), 1)
        if score:
            matches.append(dict(item, score=score))
    return sorted(matches, key=lambda x: (-x["score"], x["evidence_id"]))[:top_k]
