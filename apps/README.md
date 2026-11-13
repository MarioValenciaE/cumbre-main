# Applications

No applications exist yet. This folder is where they will live.

## Conventions for future apps

- One self-contained folder per app (e.g. `apps/website/`), with its own `README.md`, dependency manifest, and `.env.example`.
- The stack is chosen per project, not by default; record significant choices in [`docs/decisions/`](../docs/decisions/).
- Credentials via environment variables only; never commit secrets.
- No client data in this repository.
- Add monorepo tooling only if several apps actually need to share code.

Possible first app: the public Cumbre website (not yet decided — see [`PROJECT_CONTEXT.md`](../PROJECT_CONTEXT.md)).
