# Applications

No applications exist yet. This folder is where they will live.

## Conventions for future apps

- One self-contained folder per app (e.g. `apps/website/`), with its own `README.md`, dependency manifest, and `.env.example`.
- The stack is chosen per project, not by default; record significant choices in [`docs/decisions/`](../docs/decisions/).
- Credentials via environment variables only; never commit secrets.
- No client data in this repository.
- Add monorepo tooling only if several apps actually need to share code.

## Current app

[`website/`](website/README.md) is Cumbre's static public website MVP. It remains intentionally self-contained and dependency-free while the domain, host, lead channel, CRM, and final website architecture are decided. See [ADR 0002](../docs/decisions/0002-website-mvp.md).
