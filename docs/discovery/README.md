# Discovery workflow

How Cumbre turns a client discovery session into an evidence-based decision package.

| Piece | Location |
| --- | --- |
| Company context for agents (v1.0) | [`docs/context/CUMBRE_CONTEXT.md`](../context/CUMBRE_CONTEXT.md) · original PDF in [`docs/context/source/`](../context/source/) |
| Analysis methodology (master prompt v1.0) | [`methodology/MASTER_PROMPT_v1.md`](methodology/MASTER_PROMPT_v1.md) · original PDF in [`methodology/source/`](methodology/source/) |
| Claude Code skills | [`cumbre-new`](../../.claude/skills/cumbre-new/SKILL.md) → `/cumbre-new` (new files → match/create client → session → analysis) · [`cumbre-discovery`](../../.claude/skills/cumbre-discovery/SKILL.md) → `/cumbre-discovery` (analysis of one session) |
| Session script + `session.yaml` template | [`scripts/discovery/`](../../scripts/discovery/) |
| Human meeting templates | [`templates/`](templates/) |

The Markdown files are the canonical versions that agents read. The PDFs are archived originals. To change the methodology, edit the Markdown and bump its version. See [ADR 0003](../decisions/0003-discovery-agent-workflow.md).

## Where client data lives

```text
clients/                              # git-ignored; or set CUMBRE_CLIENTS_DIR to a private folder outside the repo
├── _inbox/                           # drop zone when you don't want to pick the client; /cumbre-new matches it
└── <client-slug>/
    ├── inbox/                        # optional per-client drop folder
    └── sessions/
        └── <YYYY-MM-DD>[-label]/
            ├── session.yaml          # config: client, stage, currency, language, rates (null = not approved)
            ├── .inputs_manifest.json # checksums of the sources (used to detect edits)
            ├── inputs/               # read-only sources; the agent never edits these
            │   ├── transcript/       # TR:  client statements      -> [CONFIRMADO]
            │   ├── notes/            # NT:  Cumbre's own notes     -> [NOTA_INTERNA]
            │   ├── deck/             # SL:  slides used (what was meant to be asked, not evidence)
            │   ├── documents/        # DOC: client-supplied records
            │   ├── data/             # DOC: authorized data samples
            │   └── external/         # [EXTERNO] public research, with source and date
            └── outputs/              # written by the agent
                ├── 00_README.md … 10_client_followup_draft.md
                ├── 06_opportunities/OPP-XXX.md
                └── CHANGELOG.md      # only when a prior run is revised
```

`clients/` is git-ignored, so nothing in it can be committed by accident. Git-ignored is still not access-controlled, though. For real client data, `export CUMBRE_CLIENTS_DIR=~/Cumbre-Private/clients` (or any encrypted or access-controlled folder) keeps it outside the repo entirely. The script refuses to write client folders inside the repo unless they are git-ignored.

## Quick path: `/cumbre-new`

1. Attach the files in Claude Code, or drop them in `clients/_inbox/` (or in `clients/<client>/inbox/` if you already know the client).
2. Run `/cumbre-new`, or `/cumbre-new ProPES` to name the client.
3. The skill reads every file and works out which company it is about. It matches that company to an existing client folder, allowing for spelling and legal-suffix variations, or creates a new one. It then shows a table: file → client (existing/new) → kind → session.
4. It asks you first only if the client match, the meeting date, a file's kind, or the target session is unclear.
5. It files the documents into a session and runs `/cumbre-discovery`. Inbox files are moved; attached files are copied.

`python3 scripts/discovery/cumbre_session.py clients` shows what it matches against. Never drop documents in `FOLDERS/<Client>/`: that folder is tracked by Git and must stay public-safe.

## Step by step (manual)

**1. Create the session** (from the repo root):

```bash
python3 scripts/discovery/cumbre_session.py new \
  --client "Nombre de la Empresa" --date 2026-11-20 \
  --transcript ~/Downloads/transcripcion.txt \
  --notes ~/Downloads/notas-mario.md \
  --deck ~/Downloads/presentacion.pdf
```

The source files are copied, never moved. A second session on the same date gets a `-2` suffix, or you can pass `--label seguimiento`.

**2. Review `session.yaml`.** Set `session_stage` and `attendees` (roles rather than names). Fill in `commercial_assumptions` only with values the founders have approved. Any value left `null` produces `TARIFA NO DEFINIDA` instead of an invented price.

**3. Add more inputs later**, either with `add <session> --doc F` or by dropping files into `inputs/<kind>/` and running `add <session>` to register them.

**4. Run the analysis.** In Claude Code:

```text
/cumbre-discovery clients/nombre-de-la-empresa/sessions/2026-11-20/session.yaml
```

You can also do everything in one step: `/cumbre-discovery new "Nombre de la Empresa" 2026-11-20`, attaching the files or giving their paths.

With **Codex or another agent**, use this prompt: *"Follow `.claude/skills/cumbre-discovery/SKILL.md` for the session at `<path>/session.yaml`."*

**5. Check the result:**

```bash
python3 scripts/discovery/cumbre_session.py check clients/nombre-de-la-empresa/sessions/2026-11-20
```

The check validates the session config, privacy (the folder is git-ignored and nothing in it is tracked), that the sources are unchanged, that all required outputs exist, that every P-/OPP- ID is defined, that evidence tags are present, that no credential-like strings appear, and that the client draft contains no internal-only terms. It does **not** judge analytical quality. A founder must review that before anything goes to a client.

## Tests

```bash
python3 -m unittest discover -s scripts/discovery -p "test_*.py"
```

A synthetic transcript for end-to-end trials is in [`scripts/discovery/fixtures/`](../../scripts/discovery/fixtures/). It is fictional and public-safe.
