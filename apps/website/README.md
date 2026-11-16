# Cumbre website MVP

A static, Spanish-first marketing site and diagnostic funnel for Cumbre. It is intentionally dependency-free: the page can be hosted on GitHub Pages, Cloudflare Pages, Vercel, or any static host.

## Run locally

From this folder, serve the files with any static server. For example, if Python is installed:

```powershell
python -m http.server 4173
```

Then visit `http://localhost:4173`.

## Before publishing

1. A logo provided by the founders is used in the MVP without alteration. Confirm it is the final approved logo before launch.
2. Set one or both contact fields in [`site.config.js`](site.config.js): WhatsApp and email are both supported.
3. Set analytics IDs only after the privacy copy, consent behavior, and providers are confirmed.
4. Add the production domain to the canonical URL, Open Graph image, JSON-LD organization data, and `sitemap.xml`.
5. Replace the privacy draft with reviewed company information and a contact address.
6. Test the diagnostic form, every contact path, mobile layout, and cookie preference behavior in the deployed site.

## Tracking events

The browser emits these events after analytics consent: `cta_diagnostic_clicked`, `diagnostic_step_completed`, `diagnostic_completed`, and `lead_submitted`. It deliberately does **not** send name, email, company, or individual diagnostic answers to analytics tools.

## Scope

This MVP does not include a database, CRM, authentication, server-side form storage, or advertising account configuration. Those need an approved lead-management and privacy decision first.
