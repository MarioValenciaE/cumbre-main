---
name: cumbre-discovery
description: Analyze a Cumbre client discovery session (transcript, notes, documents) and write the evidence-based decision package (business profile, process maps, problem register, opportunities, delivery/pricing, validation requests, internal decision, client follow-up draft) as Markdown in that session's outputs/ folder. Also creates new client sessions. Use when the user asks to process, analyze, or set up a discovery meeting/session for a prospective client.
argument-hint: "<path/to/session.yaml> | new <client name> <YYYY-MM-DD>"
---

# /cumbre-discovery

This skill only orchestrates the work. The rules for the analysis live in two documents, which you must read in full and follow:

| Read | Why |
| --- | --- |
| [`docs/discovery/methodology/MASTER_PROMPT_v1.md`](../../../docs/discovery/methodology/MASTER_PROMPT_v1.md) | **The methodology**: role, analytical rules, Passes 1–6, required outputs, coverage checklist, final response. Overrides anything in this skill if they conflict. |
| [`docs/context/CUMBRE_CONTEXT.md`](../../../docs/context/CUMBRE_CONTEXT.md) | Who Cumbre is, its capabilities, stage and source hierarchy. Use it **only** to judge Cumbre fit, capacity and reusability, **never** as evidence about the client. |
| [`docs/pricing/principles.md`](../../../docs/pricing/principles.md) | Cumbre's pricing guardrails (no official rates exist). |

Facts about Cumbre come from those files and from the session's `commercial_assumptions`, never from memory or from this skill. Facts about the client come only from that session's `inputs/`.

Helper script (Python 3 stdlib): `python3 scripts/discovery/cumbre_session.py {new|add|check} --help`, run from the repo root.

## 1. Resolve the session

- **Given a path** to a `session.yaml` file or session folder: use that path.
- **`new <client> <date>`** or "set up a session for X": run
  `python3 scripts/discovery/cumbre_session.py new --client "<name>" --date <YYYY-MM-DD> [--transcript F] [--notes F] [--deck F] [--doc F] [--data F]`.
  You need both the client name and the date. If either is missing, ask; never invent them. If no input files were provided, stop after creating the folder and tell the user where to put the files (`inputs/<kind>/`), then to run `add <session>` and `/cumbre-discovery <session.yaml>`.
- **Only a client name**: list `clients/<slug>/sessions/` (or `$CUMBRE_CLIENTS_DIR`) for that one client and ask which session to use. Do not open other clients' folders.

## 2. Preflight

1. Run `python3 scripts/discovery/cumbre_session.py add <session>`. This registers checksums for any inputs dropped into the folder by hand, so later edits to a source file show up.
2. Run `python3 scripts/discovery/cumbre_session.py check <session>`. If it reports an `ERROR` about privacy (the folder is not git-ignored, or files are tracked by Git) or says `inputs/` is empty, **stop** and report it.
3. Read `session.yaml`, then every file under `inputs/`. Choose the evidence tag from the folder each file is in (see the comments in `session.yaml`). `deck/` shows only what was meant to be asked. `notes/` holds Cumbre's own notes (`[NOTA_INTERNA]`), not client facts. If a file cannot be read, record it in `00_README.md` and continue.
4. If `outputs/` already has files, read them first. Revise them rather than starting over, and add a dated entry to `outputs/CHANGELOG.md`.

## 3. Execute the methodology

Run Passes 1–6 from the master prompt in order and write every required output to `<session>/outputs/`, using the exact file names it lists. For efficiency:

- Write the outputs in order 01 → 05, then the cards in `06_opportunities/`, then 07 → 10, and write `00_README.md` last, with the source inventory and the coverage checklist (`Cubierto` / `Parcial` / `No discutido` for each area).
- Define every `P-XXX` in `04_problem_register.md` and every `OPP-XXX` in `05_opportunity_backlog.md` before you reference it anywhere else.
- IDs are stable. Card numbers follow the backlog, so gaps are expected (for example OPP-001, OPP-002, OPP-004). Never renumber IDs to close the gaps.
- Citation conventions, in addition to those in the master prompt: `NT:<file>:L<line>` for notes (`NT:L<line>` if there is only one notes file), `DOC:<file>:p<page>` for documents, and `TR:encabezado` for untimestamped transcript header text.
- Effort hours, working assumptions and root-cause hypotheses are Cumbre estimates. Label them `[INFERENCIA]` and state the confidence.
- Client draft (`10_…`): use placeholders (`{nombre}`) for people's names and for any detail not in the inputs.
- Pricing: use only the non-null `commercial_assumptions`. Where a value is null, write `TARIFA NO DEFINIDA` and show the formula with the missing input named.
- `external_research_allowed: false` (the default) means no web research about the client. Even when it is `true`, never send transcript content or personal data to external services.

## 4. Quality gate

Run `python3 scripts/discovery/cumbre_session.py check <session>` and fix every `ERROR`. Review each `WARN` and either fix it or explain it in `00_README.md`. Then carry out the cross-file consistency review that Pass 6 asks for (process names, numbers, IDs, pricing arithmetic). The script cannot do that review for you.

## 5. Boundaries

- Write only inside `<session>/outputs/`. Never edit `inputs/` or `session.yaml` by hand. Only the script's `new` and `add` commands may create or update `session.yaml`, `inputs/` and `.inputs_manifest.json`. Never edit `docs/context/` or the methodology during a client run.
- Never commit, push, `git add -f`, upload, publish, send, or share client material. Never copy client details into tracked repo files such as `FOLDERS/`, `docs/`, or `PROJECT_CONTEXT.md`.
- Do not mix information across clients. Cross-client patterns may be noted only in generic, de-identified terms.

## 6. Final response

Use the short format from the master prompt: output path; the 2–3 top opportunities with confidence; the biggest information gaps; whether prices can be estimated or remain conditional; and the immediate next step. Add the result of the `check` run. Do not paste the reports into the chat.
