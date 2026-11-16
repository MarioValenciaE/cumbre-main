#!/usr/bin/env python3
"""Cumbre Discovery Kit: create client discovery sessions and check their structure.

Standard library only (Python 3.9+). Used by the /cumbre-discovery skill; see docs/discovery/README.md.

  clients  List client folders (names, sessions, pending inbox files) so new files can be matched (/cumbre-new).
  inbox  Create clients/<client>/inbox/, a private drop folder for one client's raw documents.
  new    Create clients/<client>/sessions/<date>/ with session.yaml, inputs/ and outputs/.
  add    Copy (or --move) more input files into a session and record their checksums.
  check  Validate a session's setup and, once they exist, its outputs.

Client folders are confidential. Inside this repository they must be git-ignored
(clients/ is). Set CUMBRE_CLIENTS_DIR or pass --clients-dir to keep them outside the repo.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).with_name("session.template.yaml")
MANIFEST = ".inputs_manifest.json"
UNSORTED_INBOX = "_inbox"  # clients/_inbox/: drop zone when the client is not yet known
PUBLIC_INDEX = REPO_ROOT / "FOLDERS"

# Input kind -> subfolder of inputs/ (mirrors `inputs:` in session.template.yaml).
INPUT_DIRS = {
    "transcript": "transcript",
    "notes": "notes",
    "deck": "deck",
    "doc": "documents",
    "data": "data",
    "external": "external",
}

REQUIRED_KEYS = (
    "client_name", "client_slug", "project_slug", "session_date", "currency",
    "output_language", "inputs", "commercial_assumptions",
)

# Output contract from docs/discovery/methodology/MASTER_PROMPT_v1.md ("Required outputs").
REQUIRED_OUTPUTS = (
    "00_README.md", "01_business_profile.md", "02_process_maps.md", "03_systems_and_data.md",
    "04_problem_register.md", "05_opportunity_backlog.md", "06_opportunities",
    "07_delivery_pricing.md", "08_validation_requests.md", "09_internal_decision.md",
    "10_client_followup_draft.md",
)
EVIDENCE_TAGS = ("[CONFIRMADO]", "[NOTA_INTERNA]", "[INFERENCIA]", "[DESCONOCIDO]", "[EXTERNO]")
P_ID = re.compile(r"\bP-\d{3}\b")
OPP_ID = re.compile(r"\bOPP-\d{3}\b")
# Terms that should not reach a client-facing draft.
INTERNAL_ONLY = re.compile(
    r"margen bruto|gross margin|costo interno|internal cost|TARIFA NO DEFINIDA|"
    r"\[INFERENCIA\]|\[NOTA_INTERNA\]|\bNT:",
    re.IGNORECASE,
)
SECRET = re.compile(
    r"(password|contraseña|passwd|api[_-]?key|secret|token)\s*[:=]\s*\S{6,}|"
    r"\bsk-[A-Za-z0-9_-]{20,}|\bAKIA[0-9A-Z]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)


class KitError(Exception):
    pass


def slugify(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if not slug:
        raise KitError(f"cannot build a folder name from {value!r}")
    return slug


def clients_root(cli_value: str | None) -> Path:
    raw = cli_value or os.environ.get("CUMBRE_CLIENTS_DIR") or str(REPO_ROOT / "clients")
    return Path(raw).expanduser().resolve()


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def git_ignored(path: Path) -> bool | None:
    """True/False if git can tell; None if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", str(path)],
            capture_output=True,
        )
    except FileNotFoundError:
        return None
    return {0: True, 1: False}.get(result.returncode)


def git_tracked_files(path: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--", str(path)],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return []
    return [line for line in result.stdout.splitlines() if line]


def assert_private(path: Path) -> None:
    """Refuse to place client material anywhere Git could pick it up."""
    if not inside_repo(path):
        return
    ignored = git_ignored(path)
    if ignored is not True:
        reason = "git is unavailable to confirm it" if ignored is None else "it is not git-ignored"
        raise KitError(
            f"refusing to write client material to {path}: it is inside the repository and {reason}. "
            "Use the default clients/ folder, or set CUMBRE_CLIENTS_DIR to a private location."
        )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_files(session: Path) -> list[Path]:
    root = session / "inputs"
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def load_manifest(session: Path) -> dict[str, str]:
    path = session / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def register_inputs(session: Path) -> list[str]:
    """Record checksums for inputs not yet in the manifest. Existing entries are never updated,
    so later edits to a source file are detected by `check`."""
    manifest = load_manifest(session)
    added = []
    for path in input_files(session):
        rel = path.relative_to(session).as_posix()
        if rel not in manifest:
            manifest[rel] = sha256(path)
            added.append(rel)
    if added or not (session / MANIFEST).exists():  # no-op when nothing is new
        (session / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return added


def copy_inputs(session: Path, files_by_kind: dict[str, list[str]], move: bool = False) -> list[str]:
    copied = []
    for kind, files in files_by_kind.items():
        target_dir = session / "inputs" / INPUT_DIRS[kind]
        for raw in files or []:
            src = Path(raw).expanduser()
            if not src.is_file():
                raise KitError(f"input file not found: {src}")
            dest = target_dir / src.name
            if dest.exists():
                raise KitError(f"{dest} already exists; rename the new file instead of overwriting a source")
            target_dir.mkdir(parents=True, exist_ok=True)
            if move:  # used for files already in the private inbox: they become the session's sources
                shutil.move(str(src), dest)
            else:     # default: copy, so the operator's original stays untouched
                shutil.copy2(src, dest)
            copied.append(dest.relative_to(session).as_posix())
    return copied


def pending_files(folder: Path) -> list[str]:
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))


def client_names(client_dir: Path) -> list[str]:
    """client_name values from this client's session.yaml files (names only, no content)."""
    names = set()
    for cfg in client_dir.glob("sessions/*/session.yaml"):
        match = re.search(r'^client_name:\s*"?(.*?)"?\s*$', cfg.read_text(encoding="utf-8"), re.MULTILINE)
        if match and match.group(1):
            names.add(match.group(1))
    return sorted(names)


def cmd_clients(args: argparse.Namespace) -> int:
    root = clients_root(args.clients_dir)
    unsorted = root / UNSORTED_INBOX
    assert_private(unsorted)
    unsorted.mkdir(parents=True, exist_ok=True)
    loose = pending_files(unsorted)
    print(f"Clients root: {root}")
    print(f"Unsorted inbox: {unsorted} ({len(loose)} pending" + (f": {', '.join(loose)})" if loose else ")"))
    folders = sorted(d for d in root.iterdir() if d.is_dir() and d.name[0] not in "._")
    print(f"Client folders: {len(folders)}")
    for d in folders:
        sessions = sorted(p.name for p in (d / "sessions").glob("*") if p.is_dir())
        print(f"  {d.name}: names={client_names(d) or '-'}; sessions={', '.join(sessions) or 'none'}; "
              f"inbox pending={len(pending_files(d / 'inbox'))}")
    public = sorted(d.name for d in PUBLIC_INDEX.iterdir() if d.is_dir()) if PUBLIC_INDEX.is_dir() else []
    print(f"Public index FOLDERS/ (tracked; names only): {', '.join(public) or 'none'}")
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    client_dir = clients_root(args.clients_dir) / slugify(args.client_slug or args.client)
    inbox = client_dir / "inbox"
    assert_private(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    pending = pending_files(inbox)
    sessions = sorted(p.name for p in (client_dir / "sessions").glob("*") if p.is_dir())
    print(f"Inbox: {inbox}")
    print(f"  pending files: {len(pending)}" + (f" ({', '.join(pending)})" if pending else ""))
    print(f"  existing sessions: {', '.join(sessions) or 'none'}")
    if not pending:
        print(f'Inbox is empty. To add documents, drop them there and run: /cumbre-new {args.client}')
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    try:
        date = dt.date.fromisoformat(args.date)
    except ValueError:
        raise KitError(f"--date must be YYYY-MM-DD, got {args.date!r}")
    client_slug = slugify(args.client_slug or args.client)
    project_slug = slugify(args.project)
    base_id = date.isoformat() + (f"-{slugify(args.label)}" if args.label else "")

    sessions_dir = clients_root(args.clients_dir) / client_slug / "sessions"
    session_id, n = base_id, 2
    while (sessions_dir / session_id).exists():  # never reuse or overwrite an existing session
        session_id, n = f"{base_id}-{n}", n + 1
    session = sessions_dir / session_id
    assert_private(session)
    for raw in (f for files in kind_args(args).values() for f in files or []):
        if not Path(raw).expanduser().is_file():  # fail before creating a half-built session
            raise KitError(f"input file not found: {raw}")

    for sub in INPUT_DIRS.values():
        (session / "inputs" / sub).mkdir(parents=True, exist_ok=True)
    (session / "outputs").mkdir()
    yaml_text = string.Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        client_name=args.client.replace("\\", "\\\\").replace('"', '\\"'),
        client_slug=client_slug,
        project_slug=project_slug,
        session_id=session_id,
        session_date=date.isoformat(),
    )
    (session / "session.yaml").write_text(yaml_text, encoding="utf-8")
    copied = copy_inputs(session, kind_args(args), args.move)
    register_inputs(session)

    print(f"Created session: {session}")
    for rel in copied:
        print(f"  input: {rel}")
    if not copied:
        print("  No inputs yet. Add files to inputs/<kind>/ and run `add`, or pass --transcript/--notes/...")
    print("Next: review session.yaml (stage, attendees, commercial_assumptions), then run:")
    print(f"  /cumbre-discovery {session / 'session.yaml'}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    session = Path(args.session).expanduser().resolve()
    if session.name == "session.yaml":
        session = session.parent
    if not (session / "session.yaml").is_file():
        raise KitError(f"no session.yaml in {session}")
    assert_private(session)
    copied = copy_inputs(session, kind_args(args), args.move)
    registered = register_inputs(session)
    for rel in copied:
        print(f"{'moved:     ' if args.move else 'copied:    '} {rel}")
    for rel in sorted(set(registered) - set(copied)):
        print(f"registered: {rel} (found in inputs/)")
    if not copied and not registered:
        print("No new inputs.")
    return 0


def top_level_keys(yaml_text: str) -> set[str]:
    return set(re.findall(r"^([A-Za-z_][\w-]*):", yaml_text, flags=re.MULTILINE))


def configured_rates(yaml_text: str) -> list[str]:
    """Names of non-null values under commercial_assumptions (simple indentation scan)."""
    found, in_block = [], False
    for line in yaml_text.splitlines():
        if re.match(r"^commercial_assumptions:", line):
            in_block = True
            continue
        if in_block and re.match(r"^\S", line):
            break
        match = re.match(r"^\s+([\w-]+):\s*([^#\s][^#]*)", line) if in_block else None
        if match and match.group(2).strip().lower() not in ("null", "~", "", "[]", "{}"):
            found.append(match.group(1))
    return found


def check_session(session: Path) -> tuple[list[str], list[str], list[str]]:
    errors, warnings, info = [], [], []
    cfg = session / "session.yaml"
    if not cfg.is_file():
        return [f"no session.yaml in {session}"], [], []
    text = cfg.read_text(encoding="utf-8")

    missing = [k for k in REQUIRED_KEYS if k not in top_level_keys(text)]
    if missing:
        errors.append(f"session.yaml missing keys: {', '.join(missing)}")
    date = re.search(r"^session_date:\s*(\S+)", text, flags=re.MULTILINE)
    try:
        dt.date.fromisoformat(date.group(1) if date else "")
    except ValueError:
        errors.append("session.yaml session_date is not YYYY-MM-DD")

    # Privacy
    if inside_repo(session):
        if git_ignored(session) is not True:
            errors.append("session folder is inside the repo but NOT git-ignored")
        tracked = git_tracked_files(session)
        if tracked:
            errors.append(f"{len(tracked)} file(s) in this session are tracked by Git (.gitignore does not protect them)")

    # Inputs and source integrity
    files = input_files(session)
    by_dir: dict[str, int] = {}
    for f in files:
        top = f.relative_to(session / "inputs").parts[0]
        by_dir[top] = by_dir.get(top, 0) + 1
    if not files:
        errors.append("inputs/ is empty: nothing to analyze (never invent a transcript)")
    elif not by_dir.get("transcript"):
        warnings.append("no transcript in inputs/transcript/: analysis will rest on notes/documents only")
    unknown = sorted(set(by_dir) - set(INPUT_DIRS.values()))
    if unknown:
        warnings.append(f"unclassified input location(s): {', '.join(unknown)}")
    info.append("inputs: " + (", ".join(f"{k}={v}" for k, v in sorted(by_dir.items())) or "none"))

    manifest = load_manifest(session)
    for rel, digest in sorted(manifest.items()):
        path = session / rel
        if not path.exists():
            errors.append(f"source input removed: {rel}")
        elif sha256(path) != digest:
            errors.append(f"source input modified after registration: {rel}")
    unregistered = [f.relative_to(session).as_posix() for f in files
                    if f.relative_to(session).as_posix() not in manifest]
    if unregistered:
        info.append(f"{len(unregistered)} input(s) not yet registered (run `add`): {', '.join(unregistered)}")

    rates = configured_rates(text)
    info.append("commercial_assumptions set: " + (", ".join(rates) if rates else "none (expect TARIFA NO DEFINIDA)"))

    # Outputs
    out = session / "outputs"
    if not out.is_dir() or not any(out.iterdir()):
        info.append("outputs/: not generated yet")
        return errors, warnings, info

    for name in REQUIRED_OUTPUTS:
        if not (out / name).exists():
            errors.append(f"missing output: outputs/{name}")
    md = {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8", errors="replace")
          for p in sorted(out.rglob("*.md"))}

    cards = sorted(p.stem for p in (out / "06_opportunities").glob("*.md")) if (out / "06_opportunities").is_dir() else []
    bad_names = [c for c in cards if not re.fullmatch(r"OPP-\d{3}", c)]
    if bad_names:
        errors.append(f"06_opportunities files must be named OPP-XXX.md: {', '.join(bad_names)}")
    if len(cards) > 3:
        warnings.append(f"{len(cards)} opportunity cards; the methodology asks for the top 1-3 only")
    if (out / "06_opportunities").is_dir() and not cards:
        warnings.append("no opportunity cards: acceptable only if 05/09 explain why no opportunity is ready")

    problems = set(P_ID.findall(md.get("04_problem_register.md", "")))
    opps = set(OPP_ID.findall(md.get("05_opportunity_backlog.md", "")))
    for card in cards:
        if card not in opps:
            errors.append(f"card {card} is not listed in 05_opportunity_backlog.md")
    for rel, body in md.items():
        for pid in sorted(set(P_ID.findall(body)) - problems):
            errors.append(f"{rel} references {pid}, which is not defined in 04_problem_register.md")
        for oid in sorted(set(OPP_ID.findall(body)) - opps):
            errors.append(f"{rel} references {oid}, which is not defined in 05_opportunity_backlog.md")
        if SECRET.search(body):
            errors.append(f"{rel} contains what looks like a credential; remove it")

    analytic = "".join(md.get(n, "") for n in REQUIRED_OUTPUTS[1:6])
    if not any(tag in analytic for tag in EVIDENCE_TAGS):
        errors.append("outputs 01-05 contain no evidence tags ([CONFIRMADO], [INFERENCIA], ...)")
    if not re.search(r"cobertura|coverage", "".join(md.values()), re.IGNORECASE):
        warnings.append("no coverage checklist found (expected in 00_README.md)")

    draft = md.get("10_client_followup_draft.md", "")
    leaks = sorted({m.group(0) for m in INTERNAL_ONLY.finditer(draft)})
    if leaks:
        warnings.append(f"client-facing draft mentions internal-only terms: {', '.join(leaks)}")

    info.append(f"outputs: {len(md)} Markdown files, {len(problems)} problems, {len(opps)} opportunities, {len(cards)} cards")
    return errors, warnings, info


def cmd_check(args: argparse.Namespace) -> int:
    session = Path(args.session).expanduser().resolve()
    if session.name == "session.yaml":
        session = session.parent
    errors, warnings, info = check_session(session)
    print(f"Session: {session}")
    for line in info:
        print(f"  INFO  {line}")
    for line in warnings:
        print(f"  WARN  {line}")
    for line in errors:
        print(f"  ERROR {line}")
    print("Result: " + ("FAIL" if errors else "OK"))
    return 1 if errors else 0


def kind_args(args: argparse.Namespace) -> dict[str, list[str]]:
    return {kind: getattr(args, kind) for kind in INPUT_DIRS}


def add_input_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--move", action="store_true", help="move input files instead of copying (for inbox files)")
    for kind, folder in INPUT_DIRS.items():
        parser.add_argument(f"--{kind}", action="append", metavar="FILE", help=f"copy FILE into inputs/{folder}/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    clients = sub.add_parser("clients", help="list client folders and pending inbox files (creates clients/_inbox/)")
    clients.add_argument("--clients-dir", help="root folder for client data (default: $CUMBRE_CLIENTS_DIR or <repo>/clients)")
    clients.set_defaults(func=cmd_clients)

    inbox = sub.add_parser("inbox", help="create or inspect a client's private drop folder")
    inbox.add_argument("--client", required=True, help='client name, e.g. "ProPES"')
    inbox.add_argument("--client-slug", help="override the folder name derived from --client")
    inbox.add_argument("--clients-dir", help="root folder for client data (default: $CUMBRE_CLIENTS_DIR or <repo>/clients)")
    inbox.set_defaults(func=cmd_inbox)

    new = sub.add_parser("new", help="create a client session")
    new.add_argument("--client", required=True, help='client name, e.g. "Acme SAS"')
    new.add_argument("--date", required=True, help="session date, YYYY-MM-DD")
    new.add_argument("--project", default="discovery", help="project slug (default: discovery)")
    new.add_argument("--label", help="optional suffix to distinguish sessions on the same date")
    new.add_argument("--client-slug", help="override the folder name derived from --client")
    new.add_argument("--clients-dir", help="root folder for client data (default: $CUMBRE_CLIENTS_DIR or <repo>/clients)")
    add_input_flags(new)
    new.set_defaults(func=cmd_new)

    add = sub.add_parser("add", help="copy inputs into a session and register checksums")
    add.add_argument("session", help="session folder or its session.yaml")
    add_input_flags(add)
    add.set_defaults(func=cmd_add)

    check = sub.add_parser("check", help="validate session setup and outputs")
    check.add_argument("session", help="session folder or its session.yaml")
    check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
