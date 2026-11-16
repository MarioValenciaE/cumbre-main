# 0002 — Static public website MVP

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

Cumbre needs an initial public presence that communicates its problem-first approach, establishes legitimacy, supports basic organic search, and starts qualifying prospective conversations. A final service catalogue, CRM, domain, hosting provider, and lead channel are still open decisions.

## Decision

Create a static, Spanish-first website MVP in `apps/website/` using semantic HTML, custom CSS, and browser-native JavaScript. It includes a scroll-led marketing narrative, a short diagnostic funnel, consent-gated optional analytics hooks, and public configuration for one future lead destination.

No third-party package, database, authentication, server, CRM, or production analytics ID is included at this stage.

## Rationale

- Static output is fast, accessible, SEO-friendly, inexpensive to host, and deployable before a domain is selected.
- Native interactions deliver a deliberate motion experience without committing the project to an application framework or animation dependency.
- The diagnostic creates a useful conversion path while the commercial model is still being validated.
- Deferring lead storage and tracking accounts avoids collecting personal data before ownership and privacy processes are defined.

## Consequences

- A contact channel must be configured in `apps/website/site.config.js` before publication; otherwise the form deliberately does not claim to send leads.
- Add canonical URLs, an Open Graph image, sitemap, approved logo, and final privacy details once the domain and legal/contact details are available.
- Revisit the stack only if the website needs server-side forms, a CRM integration, personalization, localization, or substantial content expansion.
