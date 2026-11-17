"""Run folder, evidence store (SQLite + FTS5 + JSONL), stable IDs, checkpoints."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from pathlib import Path

from .util import REPO_ROOT, canonical_url, iso, read_jsonl, short_hash, utc_now, write_json, write_jsonl

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sources (source_id TEXT PRIMARY KEY, canonical_url TEXT UNIQUE, data TEXT);
CREATE TABLE IF NOT EXISTS claims (claim_id TEXT PRIMARY KEY, claim_key TEXT UNIQUE, data TEXT);
CREATE TABLE IF NOT EXISTS signals (signal_id TEXT PRIMARY KEY, signal_key TEXT UNIQUE, data TEXT);
CREATE TABLE IF NOT EXISTS edges (edge_id TEXT PRIMARY KEY, edge_key TEXT UNIQUE, data TEXT);
CREATE TABLE IF NOT EXISTS incidents (incident_id TEXT PRIMARY KEY, incident_key TEXT UNIQUE, data TEXT);
CREATE TABLE IF NOT EXISTS observations (obs_id TEXT PRIMARY KEY, obs_key TEXT UNIQUE, kind TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS queries (query_id TEXT PRIMARY KEY, query_key TEXT UNIQUE, wave INTEGER,
                                    status TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS url_queue (canonical_url TEXT PRIMARY KEY, status TEXT, priority REAL,
                                      depth INTEGER, wave INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS provider_cache (k TEXT PRIMARY KEY, v TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS waves (wave INTEGER PRIMARY KEY, data TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(source_id UNINDEXED, section UNINDEXED,
                                                       facet UNINDEXED, text, tokenize='unicode61 remove_diacritics 2');
"""

ID_FORMATS = {"SRC": "SRC-{:06d}", "CLM": "CLM-{:05d}", "SIG": "SIG-{:05d}", "REL": "REL-{:04d}",
              "INC": "INC-{:04d}", "OBS": "OBS-{:05d}", "OPP": "OPP-{:03d}", "Q": "Q-{:05d}"}


class KitError(Exception):
    pass


# ---------------------------------------------------------------------------
# Privacy guard (same policy as scripts/discovery/cumbre_session.py)
# ---------------------------------------------------------------------------

def clients_root(cli_value: str | None = None) -> Path:
    raw = cli_value or os.environ.get("CUMBRE_CLIENTS_DIR") or str(REPO_ROOT / "clients")
    return Path(raw).expanduser().resolve()


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def assert_private(path: Path) -> None:
    """Refuse to write research output anywhere Git could pick it up."""
    if not _inside_repo(path):
        return
    try:
        res = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", str(path)], capture_output=True)
        ignored = {0: True, 1: False}.get(res.returncode)
    except FileNotFoundError:
        ignored = None
    if ignored is not True:
        raise KitError(f"refusing to write research to {path}: inside the repository and not git-ignored. "
                       "Use clients/ or set CUMBRE_CLIENTS_DIR to a private folder.")


# ---------------------------------------------------------------------------
# Evidence store
# ---------------------------------------------------------------------------

class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self._counters = {}
        for prefix in ID_FORMATS:
            self._counters[prefix] = int(self.kv_get(f"counter:{prefix}") or 0)

    # key/value ----------------------------------------------------------------
    def kv_get(self, key: str):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def kv_set(self, key: str, value) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value, default=str)))

    def close(self) -> None:
        with self.lock:
            self.db.commit()
            self.db.close()

    def commit(self) -> None:
        with self.lock:
            self.db.commit()

    # ids ------------------------------------------------------------------------
    def next_id(self, prefix: str) -> str:
        with self.lock:
            self._counters[prefix] += 1
            self.kv_set(f"counter:{prefix}", self._counters[prefix])
            return ID_FORMATS[prefix].format(self._counters[prefix])

    def reserve_ids(self, prefix: str, used_numbers: list[int]) -> None:
        """After importing stable IDs from a prior run, never reissue them."""
        if used_numbers:
            with self.lock:
                self._counters[prefix] = max(self._counters[prefix], max(used_numbers))
                self.kv_set(f"counter:{prefix}", self._counters[prefix])

    # generic upsert -----------------------------------------------------------------
    def _upsert(self, table: str, id_col: str, key_col: str, key: str, data: dict, prefix: str,
                preset_id: str | None = None, extra: dict | None = None) -> tuple[str, bool]:
        with self.lock:
            row = self.db.execute(f"SELECT {id_col}, data FROM {table} WHERE {key_col}=?", (key,)).fetchone()
            if row:
                merged = {**json.loads(row[1]), **{k: v for k, v in data.items() if v is not None}}
                merged[id_col] = row[0]
                self.db.execute(f"UPDATE {table} SET data=? WHERE {id_col}=?",
                                (json.dumps(merged, ensure_ascii=False, default=str), row[0]))
                return row[0], False
            new_id = preset_id or self.next_id(prefix)
            data = {**data, id_col: new_id}
            cols = [id_col, key_col, "data"] + list((extra or {}).keys())
            vals = [new_id, key, json.dumps(data, ensure_ascii=False, default=str)] + list((extra or {}).values())
            self.db.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            return new_id, True

    def _get(self, table: str, id_col: str, value: str):
        row = self.db.execute(f"SELECT data FROM {table} WHERE {id_col}=?", (value,)).fetchone()
        return json.loads(row[0]) if row else None

    def _all(self, table: str, order: str) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute(f"SELECT data FROM {table} ORDER BY {order}")]

    # sources ---------------------------------------------------------------------
    def upsert_source(self, url: str, data: dict, preset_id: str | None = None) -> tuple[str, bool]:
        canon = canonical_url(url)
        data = {**data, "url": data.get("url") or url, "canonical_url": canon}
        return self._upsert("sources", "source_id", "canonical_url", canon, data, "SRC", preset_id)

    def source_by_url(self, url: str):
        row = self.db.execute("SELECT data FROM sources WHERE canonical_url=?", (canonical_url(url),)).fetchone()
        return json.loads(row[0]) if row else None

    def source(self, source_id: str):
        return self._get("sources", "source_id", source_id)

    def update_source(self, source_id: str, **fields) -> None:
        with self.lock:
            cur = self.source(source_id) or {}
            cur.update(fields)
            self.db.execute("UPDATE sources SET data=? WHERE source_id=?",
                            (json.dumps(cur, ensure_ascii=False, default=str), source_id))

    def sources(self) -> list[dict]:
        return self._all("sources", "source_id")

    # claims / signals / edges / incidents / observations ---------------------------
    def upsert_claim(self, key: str, data: dict, preset_id=None):
        with self.lock:
            existing = self.db.execute("SELECT data FROM claims WHERE claim_key=?", (key,)).fetchone()
            if existing:
                cur = json.loads(existing[0])
                for list_key in ("supporting_source_ids", "contradicting_source_ids", "locations"):
                    merged = list(dict.fromkeys((cur.get(list_key) or []) + (data.get(list_key) or [])))
                    data[list_key] = merged
        return self._upsert("claims", "claim_id", "claim_key", key, data, "CLM", preset_id)

    def claims(self):
        return self._all("claims", "claim_id")

    def update_claim(self, claim_id: str, **fields):
        with self.lock:
            cur = self._get("claims", "claim_id", claim_id) or {}
            cur.update(fields)
            self.db.execute("UPDATE claims SET data=? WHERE claim_id=?", (json.dumps(cur, ensure_ascii=False), claim_id))

    def upsert_signal(self, key: str, data: dict, preset_id=None):
        with self.lock:
            existing = self.db.execute("SELECT data FROM signals WHERE signal_key=?", (key,)).fetchone()
            if existing:
                cur = json.loads(existing[0])
                data = {**data, "source_ids": list(dict.fromkeys(cur.get("source_ids", []) + data.get("source_ids", []))),
                        "location": cur.get("location"), "section": cur.get("section"),
                        "observed_statement": cur.get("observed_statement")}
        return self._upsert("signals", "signal_id", "signal_key", key, data, "SIG", preset_id)

    def signals(self):
        return self._all("signals", "signal_id")

    def upsert_edge(self, key: str, data: dict):
        return self._upsert("edges", "edge_id", "edge_key", key, data, "REL")

    def edges(self):
        return self._all("edges", "edge_id")

    def upsert_incident(self, key: str, data: dict):
        return self._upsert("incidents", "incident_id", "incident_key", key, data, "INC")

    def incidents(self):
        return self._all("incidents", "incident_id")

    def upsert_observation(self, kind: str, key: str, data: dict):
        return self._upsert("observations", "obs_id", "obs_key", f"{kind}:{key}", {**data, "kind": kind}, "OBS",
                            extra={"kind": kind})

    def observations(self, kind: str | None = None) -> list[dict]:
        if kind:
            return [json.loads(r[0]) for r in self.db.execute(
                "SELECT data FROM observations WHERE kind=? ORDER BY obs_id", (kind,))]
        return self._all("observations", "obs_id")

    # passages (full-text search) -------------------------------------------------------
    def add_passages(self, source_id: str, passages: list[tuple[str, str, str]]) -> None:
        with self.lock:
            self.db.execute("DELETE FROM passages WHERE source_id=?", (source_id,))
            self.db.executemany("INSERT INTO passages (source_id, section, facet, text) VALUES (?,?,?,?)",
                                [(source_id, s, f, t) for s, f, t in passages])

    def search_passages(self, query: str, limit: int = 20) -> list[dict]:
        safe = " ".join(f'"{w}"' for w in query.replace('"', " ").split() if w)
        rows = self.db.execute(
            "SELECT source_id, section, facet, snippet(passages, 3, '«', '»', '…', 18) FROM passages "
            "WHERE passages MATCH ? ORDER BY rank LIMIT ?", (safe, limit)).fetchall()
        return [{"source_id": r[0], "section": r[1], "facet": r[2], "snippet": r[3]} for r in rows]

    # queries ---------------------------------------------------------------------------
    def add_query(self, data: dict) -> tuple[str, bool]:
        key = data["provider_hint"] + "|" + data["query"].lower().strip()
        with self.lock:
            row = self.db.execute("SELECT query_id FROM queries WHERE query_key=?", (key,)).fetchone()
            if row:
                return row[0], False
            qid = self.next_id("Q")
            rec = {**data, "query_id": qid}
            self.db.execute("INSERT INTO queries VALUES (?,?,?,?,?)",
                            (qid, key, data.get("wave", 1), data.get("status", "planned"), json.dumps(rec, ensure_ascii=False)))
            return qid, True

    def update_query(self, qid: str, **fields) -> None:
        with self.lock:
            row = self.db.execute("SELECT data FROM queries WHERE query_id=?", (qid,)).fetchone()
            rec = {**json.loads(row[0]), **fields}
            self.db.execute("UPDATE queries SET status=?, data=? WHERE query_id=?",
                            (rec.get("status"), json.dumps(rec, ensure_ascii=False), qid))

    def queries(self, status: str | None = None) -> list[dict]:
        sql = "SELECT data FROM queries" + (" WHERE status=?" if status else "") + " ORDER BY query_id"
        return [json.loads(r[0]) for r in self.db.execute(sql, (status,) if status else ())]

    # URL queue ---------------------------------------------------------------------------
    def enqueue(self, url: str, priority: float, depth: int, wave: int, data: dict) -> bool:
        canon = canonical_url(url)
        with self.lock:
            row = self.db.execute("SELECT status, priority FROM url_queue WHERE canonical_url=?", (canon,)).fetchone()
            if row:
                if row[0] == "queued" and priority > row[1]:
                    self.db.execute("UPDATE url_queue SET priority=? WHERE canonical_url=?", (priority, canon))
                return False
            self.db.execute("INSERT INTO url_queue VALUES (?,?,?,?,?,?)",
                            (canon, "queued", priority, depth, wave, json.dumps({**data, "url": url})))
            return True

    def dequeue_batch(self, n: int, external: bool | None = None) -> list[dict]:
        sql = "SELECT canonical_url, priority, depth, wave, data FROM url_queue WHERE status='queued'"
        args: list = []
        if external is not None:
            sql += " AND COALESCE(json_extract(data, '$.external'), 0) = ?"
            args.append(1 if external else 0)
        sql += " ORDER BY priority DESC, canonical_url LIMIT ?"
        args.append(n)
        return [{"canonical_url": r[0], "priority": r[1], "depth": r[2], "wave": r[3], **json.loads(r[4])}
                for r in self.db.execute(sql, args).fetchall()]

    def mark_url(self, url: str, status: str) -> None:
        with self.lock:
            self.db.execute("UPDATE url_queue SET status=? WHERE canonical_url=?", (status, canonical_url(url)))

    def skip_host(self, host: str, status: str) -> int:
        """Mark every still-queued URL of a failing host (circuit breaker); returns how many."""
        with self.lock:
            rows = [r[0] for r in self.db.execute("SELECT canonical_url FROM url_queue WHERE status='queued'")]
            hit = [u for u in rows if (u.split("/")[2] if u.count("/") >= 2 else "") in (host, host.removeprefix("www."))]
            for u in hit:
                self.db.execute("UPDATE url_queue SET status=? WHERE canonical_url=?", (status, u))
                row = self.db.execute("SELECT source_id, data FROM sources WHERE canonical_url=?", (u,)).fetchone()
                if row:
                    d = json.loads(row[1])
                    d.update(processing_status=status, exclusion_reason="host unavailable: repeated failures (circuit breaker)")
                    self.db.execute("UPDATE sources SET data=? WHERE source_id=?", (json.dumps(d, ensure_ascii=False), row[0]))
            return len(hit)

    def queue_counts(self) -> dict:
        return dict(self.db.execute("SELECT status, COUNT(*) FROM url_queue GROUP BY status").fetchall())

    # provider cache ---------------------------------------------------------------------
    def get(self, key: str):
        row = self.db.execute("SELECT v FROM provider_cache WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO provider_cache VALUES (?,?,?)",
                            (key, json.dumps(value, ensure_ascii=False, default=str), iso(utc_now())))
            self.db.commit()  # paid responses must survive a crash (no duplicate paid calls)

    # waves ------------------------------------------------------------------------------
    def save_wave(self, wave: int, data: dict) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO waves VALUES (?,?)", (wave, json.dumps(data, default=str)))

    def waves(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM waves ORDER BY wave")]


# ---------------------------------------------------------------------------
# Run folders and prior runs
# ---------------------------------------------------------------------------

def new_run_dir(root: Path, slug: str, now=None) -> tuple[Path, str]:
    now = now or utc_now()
    run_id = "r" + short_hash(f"{slug}{now.isoformat()}{os.getpid()}{id(now)}", 6)
    path = root / slug / "research" / f"{now.date().isoformat()}-{run_id}"
    assert_private(path)
    (path / "sources").mkdir(parents=True, exist_ok=True)
    for sub in ("data", "history", "exports"):
        (path / sub).mkdir(exist_ok=True)
    return path, run_id


def prior_runs(root: Path, slug: str, exclude: Path | None = None) -> list[Path]:
    base = root / slug / "research"
    if not base.is_dir():
        return []
    runs = sorted(p for p in base.iterdir() if p.is_dir() and (p / "run_manifest.json").is_file()
                  and (exclude is None or p.resolve() != exclude.resolve()))
    return runs


def export_jsonl(store: Store, run_dir: Path) -> None:
    src = run_dir / "sources"
    sources = store.sources()
    write_jsonl(src / "source_registry.jsonl", sources)
    write_jsonl(src / "claims.jsonl", store.claims())
    write_jsonl(src / "signals.jsonl", store.signals())
    write_jsonl(src / "relationship_edges.jsonl", store.edges())
    write_jsonl(src / "incidents.jsonl", store.incidents())
    write_jsonl(src / "observations.jsonl", store.observations())
    write_jsonl(src / "processed_page_metadata.jsonl",
                [s for s in sources if s.get("processing_status", "").startswith("extracted")])
    write_jsonl(src / "inaccessible_or_excluded.jsonl",
                [{k: s.get(k) for k in ("source_id", "url", "processing_status", "exclusion_reason",
                                         "retrieved_at_utc", "relevance_score")}
                 for s in sources if not s.get("processing_status", "").startswith("extracted")])
    write_jsonl(src / "queries.jsonl", store.queries())


def load_prior_registry(run_dir: Path) -> dict:
    return {
        "sources": read_jsonl(run_dir / "sources" / "source_registry.jsonl"),
        "claims": read_jsonl(run_dir / "sources" / "claims.jsonl"),
        "signals": read_jsonl(run_dir / "sources" / "signals.jsonl"),
        "manifest": json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        if (run_dir / "run_manifest.json").is_file() else {},
    }


def write_manifest(run_dir: Path, manifest: dict) -> None:
    write_json(run_dir / "run_manifest.json", manifest)
