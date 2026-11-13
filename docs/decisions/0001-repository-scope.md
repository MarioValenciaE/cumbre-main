# 0001 — Repository scope: documentation hub first

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

Cumbre is early-stage. The business model, service catalog, pricing, and website requirements are still undefined. The repository may be public.

## Decision

Start `cumbre-main` as a **documentation and organization hub** with agent guidance (`AGENTS.md`), a company source of truth (`PROJECT_CONTEXT.md`), strategy and discovery docs, and an `apps/` placeholder. Do not build applications, select a stack, create databases, authentication, CRM, CI/CD, or cloud deployments yet.

## Rationale

- Building before the problems and offerings are clear risks wasted effort.
- Shared context lets any agent or collaborator act consistently.
- A lightweight structure is easy to extend.
- Public-safe templates can live in the repo; client data cannot.

## Consequences

- Applications will be added under `apps/` when justified, each with its own ADR for major technology choices.
- Revisit this decision when the first application (likely the website) is approved.
