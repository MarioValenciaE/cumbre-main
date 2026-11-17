# AGENTS.md — Operating guide for AI coding agents

This file applies to every AI agent (Claude Code, Codex, others) working in this repository.
Cumbre is an early-stage startup; this repo is currently a **documentation and organization hub**, not a software product.

## Start here

1. Read [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) before any significant task. It is the source of truth for the company. For strategy, analysis, or client work, also read the agent context [`docs/context/CUMBRE_CONTEXT.md`](docs/context/CUMBRE_CONTEXT.md).
2. Inspect the files you are about to change. Do not overwrite existing work without reading it.
3. If a task touches strategy, pricing, or positioning, treat existing content as **hypotheses** unless marked confirmed.

## Operating principles

- Build the smallest maintainable solution that satisfies the request.
- Prefer practical results over extra documentation. No filler files.
- Preserve established decisions (see `docs/decisions/` and the decision log in `PROJECT_CONTEXT.md`).
- Ask questions only when essential information is genuinely missing; otherwise choose a sensible default and state it.
- Separate **verified facts**, **assumptions**, and **hypotheses** in everything you write.
- Do not silently make business-strategy decisions (pricing, service catalog, positioning, client commitments). Propose them and flag them for the founders.
- Never invent customers, revenues, testimonials, ROI figures, or meeting outcomes.
- Document important architectural decisions as short ADRs in `docs/decisions/` (next number, e.g. `0002-...md`).
- When confirmed company decisions materially change, update `PROJECT_CONTEXT.md` (content + last-updated date + decision log).
- Communicate concisely.

## Technical principles

- Prefer reliable, well-supported technologies. Keep infrastructure costs controlled and deployments simple.
- Avoid unnecessary packages, services, and abstractions. Justify each new dependency.
- Design for maintainability; Cumbre will have to support what it builds.
- Consider security, accessibility, and privacy from the start.
- Credentials go in environment variables. Commit only `.env.example` with placeholder values.
- **Never commit secrets or credentials.**
- Add tests when business logic justifies them (calculations, pricing, data transforms), not for boilerplate.
- Reuse the conventions already present in the project you are editing.

## Technology selection

**No stack has been selected.** Do not assume Next.js, React, Python, Supabase, Cloudflare, or anything else.
Candidates that may fit a given project: React / Next.js, TypeScript, Python, Supabase / PostgreSQL, Cloudflare Pages / Workers, Google Cloud Run, AI APIs.
Choose based on the actual requirements of the task, explain major choices briefly, and record significant ones as an ADR.

## Repository layout

| Path | Purpose |
| --- | --- |
| `PROJECT_CONTEXT.md` | Company source of truth |
| `docs/strategy/` | Positioning, service hypotheses |
| `docs/context/` | Agent company context (`CUMBRE_CONTEXT.md`, canonical) and original PDF |
| `docs/discovery/` | Discovery workflow guide, methodology (`methodology/MASTER_PROMPT_v1.md`), templates |
| `.claude/skills/cumbre-discovery/` | `/cumbre-discovery` skill: analyzes a client session end to end |
| `.claude/skills/cumbre-new/` | `/cumbre-new` skill: matches new documents to an existing or new client folder, files them into a session, runs discovery |
| `scripts/discovery/` | Session script (`clients` / `inbox` / `new` / `add` / `check`) and `session.yaml` template |
| `scripts/research/`, `docs/research/` | Company intelligence engine (`cumbre.py research …`) and its guide; `/cumbre-company-research` skill |
| `clients/` (git-ignored) | Confidential client sessions; never committed |
| `docs/pricing/` | Pricing principles (no official price list exists) |
| `docs/brand/`, `assets/brand/` | Brand guidance and (separately supplied) official assets |
| `docs/decisions/` | Architecture/scope decision records |
| `apps/` | Future applications; one folder per app, each self-contained |

## Company research before/after meetings

For public research on a prospect, follow [`.claude/skills/cumbre-company-research/SKILL.md`](.claude/skills/cumbre-company-research/SKILL.md) (`/cumbre-company-research`). Rules and outputs: [`docs/research/README.md`](docs/research/README.md). Passive public research only; outputs stay in `clients/`.

## Client discovery analysis

When a founder shares new client documents (attached, or dropped in `clients/_inbox/` or `clients/<client>/inbox/`), follow [`.claude/skills/cumbre-new/SKILL.md`](.claude/skills/cumbre-new/SKILL.md) (`/cumbre-new [client]`). To process an existing session, follow [`.claude/skills/cumbre-discovery/SKILL.md`](.claude/skills/cumbre-discovery/SKILL.md) (Claude Code: `/cumbre-discovery <session.yaml>`). Do not improvise a different methodology or output layout. See [`docs/discovery/README.md`](docs/discovery/README.md).

## Brand rules

Do not alter, redraw, or recreate the official Cumbre logo. It is supplied separately. Use the palette in `PROJECT_CONTEXT.md`.

## Confidentiality (the repo may be public)

- Do not commit client transcripts, customer financials, personal data, confidential operational details, credentials, or proprietary documents without founder approval.
- Real client data belongs in a private location with access controls, outside this repo.
- Templates must stay generic. Use placeholders, not real client names or figures.
- `.gitignore` does **not** protect files already tracked by Git. If something sensitive was committed, tell the user; do not just add it to `.gitignore`.

## Git discipline

- Run `git status` before working.
- Never force-push. Never overwrite another person's changes without permission.
- **Do not commit or push without explicit user authorization.**
- Keep changes scoped to the requested task.
- Summarize what you modified.

## End-of-task report

Keep it short:

1. What was implemented.
2. Files changed.
3. What was verified.
4. What remains pending.
5. Significant risks or decisions needed.
