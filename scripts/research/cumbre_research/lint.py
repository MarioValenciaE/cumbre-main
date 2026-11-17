"""Citation and output linting: report statement → claim → source → URL."""

from __future__ import annotations

import re
from pathlib import Path

from .safety import find_secrets
from .util import read_jsonl

SRC_RE = re.compile(r"\[(SRC-\d{6})\]\(([^)]+)\)")
SRC_BARE = re.compile(r"\bSRC-\d{6}\b")
CLM_RE = re.compile(r"`(CLM-\d{5})`")
SIG_RE = re.compile(r"\bSIG-\d{5}\b")
REQUIRED_FILES = [
    "EXECUTIVE_BRIEF.md", "FULL_COMPANY_DOSSIER.md", "coverage_report.md", "research_journal.md", "company_identity.md",
    "company_history_timeline.md", "business_model_and_operations.md", "products_services_channels.md",
    "digital_footprint.md", "technology_observations.md", "social_media_and_reputation.md",
    "personnel_hiring_and_partners.md", "public_records_and_news.md", "competitors_and_market.md",
    "public_risk_observations.md", "OPPORTUNITY_SIGNAL_CATALOG.md", "opportunity_hypotheses.md",
    "discovery_questions.md", "research_gaps.md", "discovery_context_handoff.md", "MEETING_TALKING_POINTS.md",
    "DISCOVERY_FOLLOWUPS.md", "ownership_funding_and_recent_events.md", "public_incidents_and_advisories.md",
    "research_config.yaml", "run_manifest.json", "sources/source_registry.jsonl", "sources/claims.jsonl",
    "sources/signals.jsonl", "sources/relationship_edges.jsonl", "sources/processed_page_metadata.jsonl",
    "sources/inaccessible_or_excluded.jsonl", "data/structured_profile.json", "data/company_research.sqlite",
]
BRIEF_HEADINGS = ["## 1. Snapshot (5 bullets maximum)", "## 2. What they do", "## 3. History and milestones",
                  "## 4. Web presence and authentication", "## 5. Data and technology signals",
                  "## 6. Social and community presence", "## 7. Publicly reported incidents and exposure observations",
                  "## 8. Business red flags and useful meeting talking points", "## 9. Priority questions for the meeting",
                  "## 10. Sources and retrieval dates"]
FORBIDDEN_PHRASES = [r"no public exposure exists", r"passed (a|the) security", r"has no vulnerabilities",
                     r"\bis secure\.", r"is incident-free\."]
CITABLE_TABLE_SECTIONS = ("## 3.", "## 4.", "## 5.", "## 6.", "## 7.")


def lint_run(run_dir: Path) -> tuple[list[str], list[str]]:
    errors, warns = [], []
    for f in REQUIRED_FILES:
        if not (run_dir / f).exists():
            errors.append(f"missing output: {f}")
    sources = {s["source_id"]: s for s in read_jsonl(run_dir / "sources" / "source_registry.jsonl")}
    claims = {c["claim_id"]: c for c in read_jsonl(run_dir / "sources" / "claims.jsonl")}
    signals = {s["signal_id"] for s in read_jsonl(run_dir / "sources" / "signals.jsonl")}
    for cid, c in claims.items():
        sup = c.get("supporting_source_ids") or []
        if not sup:
            errors.append(f"{cid}: claim without supporting sources")
        for sid in sup + (c.get("contradicting_source_ids") or []):
            if sid not in sources:
                errors.append(f"{cid}: cites unknown source {sid}")
    for md in sorted(run_dir.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        rel = md.relative_to(run_dir).as_posix()
        for sid, url in SRC_RE.findall(text):
            s = sources.get(sid)
            if not s:
                errors.append(f"{rel}: cites unknown {sid}")
            elif url not in (s.get("url"), s.get("final_url")):
                errors.append(f"{rel}: {sid} link does not match registry URL")
        for sid in SRC_BARE.findall(text):
            if sid not in sources:
                errors.append(f"{rel}: mentions unknown {sid}")
        for cid in CLM_RE.findall(text):
            if cid not in claims:
                errors.append(f"{rel}: cites unknown claim {cid}")
        for gid in SIG_RE.findall(text):
            if gid not in signals:
                errors.append(f"{rel}: cites unknown signal {gid}")
        low = text.lower()
        for rx in FORBIDDEN_PHRASES:
            if re.search(rx, low):
                errors.append(f"{rel}: forbidden categorical security wording /{rx}/")
        secrets = find_secrets(text)
        if secrets:
            errors.append(f"{rel}: secret-like content {secrets}")
    brief = run_dir / "EXECUTIVE_BRIEF.md"
    if brief.is_file():
        errors += lint_brief(brief.read_text(encoding="utf-8"))
    return errors, warns


def lint_brief(text: str) -> list[str]:
    errors = []
    for h in BRIEF_HEADINGS:
        if h not in text:
            errors.append(f"EXECUTIVE_BRIEF.md: missing heading '{h}'")
    if "**TL;DR for the meeting:**" not in text:
        errors.append("EXECUTIVE_BRIEF.md: missing TL;DR line")
    parts = re.split(r"\n(?=## )", text)
    for part in parts:
        head = part.splitlines()[0] if part else ""
        if head.startswith("## 1. Snapshot"):
            bullets = [l for l in part.splitlines() if l.startswith("- ")]
            if len(bullets) > 5:
                errors.append(f"EXECUTIVE_BRIEF.md: snapshot has {len(bullets)} bullets (max 5)")
            for b in bullets:
                if not SRC_RE.search(b) and "no official page processed" not in b:
                    errors.append(f"EXECUTIVE_BRIEF.md: uncited snapshot bullet: {b[:80]}")
        if head.startswith(CITABLE_TABLE_SECTIONS):
            for line in part.splitlines():
                if line.startswith("| ") and not line.startswith("| Event date") and not re.match(r"\|\s*(Domain|Technology|Platform|Finding)\b", line):
                    if not (SRC_RE.search(line) or "UNKNOWN" in line or "NO_HIT" in line or "NOT_CHECKED" in line):
                        errors.append(f"EXECUTIVE_BRIEF.md: uncited table row in '{head[:30]}': {line[:80]}")
    return errors
