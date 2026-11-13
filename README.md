# Cumbre

**Practical technology for Colombian businesses.**

Cumbre is a technology solutions startup founded by **Mario Valencia** and **Gustavo ("Gus")**. We help small and medium-sized businesses in Colombia close the gap between what modern technology makes possible and the manual or outdated methods still used in daily operations.

We start by understanding the business, then pick the simplest solution that creates measurable value. The problem determines the solution — not the other way around.

## What this repository is

The central workspace for Cumbre. Today it is a **documentation and organization hub**: strategy, discovery templates, pricing principles, brand guidance, and decision records. Software projects and the public website will be added under [`apps/`](apps/README.md) when they are justified.

> This repository may be public. Do not commit client data, credentials, or confidential documents. See [AGENTS.md](AGENTS.md).

## Where to look

| You want… | Go to |
| --- | --- |
| The company's source of truth | [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) |
| Rules for AI coding agents | [`AGENTS.md`](AGENTS.md) |
| Positioning and service hypotheses | [`docs/strategy/`](docs/strategy/) |
| Discovery meeting templates | [`docs/discovery/templates/`](docs/discovery/templates/) |
| Pricing philosophy | [`docs/pricing/principles.md`](docs/pricing/principles.md) |
| Brand guidance | [`docs/brand/README.md`](docs/brand/README.md) |
| Why things are the way they are | [`docs/decisions/`](docs/decisions/) |

## Structure

```
cumbre-main/
├── AGENTS.md
├── PROJECT_CONTEXT.md
├── README.md
├── docs/
│   ├── strategy/      positioning, service hypotheses
│   ├── discovery/     reusable templates
│   ├── pricing/       pricing principles
│   ├── brand/         brand guidance
│   └── decisions/     decision records
├── assets/brand/      official brand assets (supplied separately)
└── apps/              future applications
```

## Working with AI agents

Agents (Claude Code, Codex, etc.) should:

1. Read [`AGENTS.md`](AGENTS.md), then [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md).
2. Inspect existing files before changing them.
3. Make small, scoped changes and not commit or push without explicit approval.

## Future applications

Each app will live in its own folder under `apps/` with its own README, dependencies, and `.env.example`. No stack has been chosen; it will be selected per project and recorded as a decision. See [`apps/README.md`](apps/README.md).

## Maintainers

Mario Valencia · Gustavo ("Gus")
