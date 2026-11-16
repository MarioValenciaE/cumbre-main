# 0003 — Discovery agent workflow (`/cumbre-discovery`)

- **Status:** Accepted
- **Date:** 2026-11-16

## Context

The founders supplied two reference documents: `CUMBRE_CONTEXT.pdf` (company context v1.0) and `Cumbre_Master_Prompt_v1.pdf` (the discovery analysis methodology). They want a reusable, repeatable way for agents to process client discovery sessions. The Discovery Kit scripts that `CUMBRE_CONTEXT` mentions were not present in this repository, so a minimal kit was built to the master prompt's contract.

## Decision

- **Canonical text is Markdown.** `docs/context/CUMBRE_CONTEXT.md` and `docs/discovery/methodology/MASTER_PROMPT_v1.md` are faithful transcriptions. The PDFs are kept under `source/` as originals, with narrow `.gitignore` exceptions.
- **The skill only orchestrates.** `.claude/skills/cumbre-discovery/SKILL.md` references those documents and holds no company facts and no copy of the methodology. Rates come from each session's `session.yaml`, never from the skill.
- **Client data lives in `clients/<slug>/sessions/<date>/`**, which is git-ignored, or in `$CUMBRE_CLIENTS_DIR` outside the repo. The script refuses to create sessions in un-ignored repo paths.
- **Tooling is a single Python 3 stdlib script** (`scripts/discovery/cumbre_session.py`: `new`, `add`, `check`). It has no dependencies and works on any machine that has Python 3.9+. It is not an application, so it lives in `scripts/`, not `apps/`.

## Consequences

- Claude Code users run `/cumbre-discovery`. Other agents follow the same `SKILL.md`.
- `check` enforces structure, privacy, source integrity and ID consistency. It does not enforce analytical quality, so founder review stays mandatory before anything reaches a client.
- The methodology is changed by editing the Markdown and bumping its version. If the PDFs are reissued, re-transcribe them.
- Not yet validated with a real session (see `CUMBRE_CONTEXT` §6). Revisit after the first real runs.
