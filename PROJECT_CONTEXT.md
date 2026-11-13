# Cumbre — Project Context

> Central source of truth for Cumbre. Read before significant tasks.
> **Last updated:** 2026-11-13
> Agents: update this file (and the date and decision log) when confirmed company decisions materially change. Keep it free of implementation minutiae and temporary task notes.

**Legend:** ✅ confirmed · 🧪 hypothesis / exploratory · ❓ open

## 1. Company overview

- **Name:** Cumbre ✅
- **Founders:** Mario Valencia and Gustavo ("Gus") ✅
- **Nature:** Technology solutions startup ✅
- **Initial target market:** Small and medium-sized businesses (SMBs) in Colombia ✅
- **Mission:** Help businesses close the gap between modern technological capabilities and the outdated or inefficient methods still used in many business operations. Cumbre identifies and solves practical business problems with appropriate technology. ✅
- **Founder backgrounds** (no rigid role split has been confirmed):
  - Mario: business and operational strategy, management, customer discovery, project management, commercial relationships, technology implementation.
  - Gustavo: economics and political science background, software development, technical problem-solving.

### Philosophy ✅

1. Understand a business before recommending solutions.
2. Identify meaningful problems and overlooked opportunities.
3. Prioritize measurable business value over technological novelty.
4. Prefer simple, reliable solutions when they generate sufficient value.
5. Avoid unnecessary technical complexity.
6. Consider both implementation economics and ongoing maintenance costs.
7. Build reusable components where it makes practical sense.
8. Avoid locking Cumbre into a narrow service category prematurely.

**The problem determines the solution.** Cumbre does not default to AI, dashboards, ERPs, or automation just because it can build them.

## 2. Business development status

- **Stage:** Pre-launch / early exploration. Business model, offerings, and pricing are still being defined. ✅
- **Customer discovery methodology** (initial, industry-agnostic) ✅:
  1. **CONOCER** — business model, customers and revenue, team and structure, business functions, growth history.
  2. **ENTENDER** — daily operations, processes, people and responsibilities, existing technology, data and information flows.
  3. **EXPLORAR** — bottlenecks, recurring errors, manual work, key-person dependencies, lost opportunities, BI gaps, customer problems, management ambitions.
  4. **PRÓXIMOS PASOS** — evaluate findings, identify opportunities, assess financial impact and feasibility, decide between a proposal, a pilot, or a deeper diagnosis.
- **Known prospect activity:** A discovery presentation was prepared for **ProPES**. No meeting outcomes are recorded and ProPES is **not** a customer. Do not claim otherwise.
- **Unconfirmed commercial assumptions:** everything in sections 3–5 below that is marked 🧪, plus any example rates seen in pricing spreadsheets.

## 3. Potential services 🧪

*Hypotheses only — not a finalized catalog, not launched products.* See [`docs/strategy/service-hypotheses.md`](docs/strategy/service-hypotheses.md).

- Business intelligence and data analytics
- Process automation
- AI implementation
- Custom software development
- Systems integration
- Digital transformation
- Operational optimization

## 4. Relevant experience

The founders have practical experience building technology solutions inside **Inversiones Diomardi**, a Colombian retail business. Examples:

- Centralized business dashboards and information access
- Sales, inventory, and product data processing
- ERP data integrations
- Operational KPI monitoring
- Digital wholesale systems
- Business reporting and scheduled data processing
- Product catalog management

**Constraints:**
- This is experience that may inform future offerings. Diomardi is **not** to be presented as an independent, paying Cumbre client unless that relationship is explicitly confirmed.
- No external case studies, results, revenues, testimonials, or ROI figures exist. Do not invent any.
- Lessons learned from Diomardi should be added here only once the founders confirm them. ❓

## 5. Commercial model — working hypothesis 🧪

| Component | Idea |
| --- | --- |
| One-time implementation | Fees for discovery, design, development, testing, deployment |
| Infrastructure | Hosting, databases, AI APIs, messaging, storage. Ideally owned by or billed directly to the client where appropriate |
| Optional recurring support | Monitoring, maintenance, troubleshooting, updates, ongoing technical responsibility |
| Possible future SaaS | Only if sufficiently repeatable problems appear across several customers |

No pricing or service catalog is approved. Example rates in early pricing spreadsheets are **not** official prices. See [`docs/pricing/principles.md`](docs/pricing/principles.md).

## 6. Brand identity

- **Name:** Cumbre
- **Palette:**

| Name | Hex |
| --- | --- |
| Ice White | `#F6F8F6` |
| Charcoal | `#202724` |
| Forest Green | `#153F35` |
| Muted Green | `#356456` |
| Granite Gray | `#68716D` |
| Sage Gray | `#DEE7E1` |

- **Style:** minimalist, professional, modern, restrained.
- **Typography (suggested):** Manrope and Inter.
- **Logo:** an official logo exists and will be supplied separately. **Do not alter or recreate it.**

See [`docs/brand/README.md`](docs/brand/README.md).

## 7. Website plans

An initial official website is being considered; no architecture has been decided. Possible goals:

- Explain what Cumbre does
- Establish legitimacy
- Present relevant work and capabilities
- Capture prospect inquiries
- Offer a lightweight discovery funnel
- Support basic SEO and analytics

## 8. Open decisions ❓

- Final service catalog
- Pricing methodology and rates
- Website framework
- Domain registration
- Hosting
- CRM and lead management
- Customer discovery process refinements
- Support commitments
- Future SaaS possibilities
- Source-document verification (see below)

### Outstanding source verification

The following founding documents were **not available** when this repo was initialized, so their content has not been read or cross-checked. The context above comes from the founders' initialization brief only.

- `Project_Cumbre_Summary.docx`
- `Cumbre Dream Sesh #1.pdf`
- `Cumbre - Descubrimiento PROPES.pdf`
- `Cumbre_Cotizador_y_Oportunidades.xlsx`

Once reviewed, treat brainstorming as hypotheses, prefer recent confirmed decisions where documents disagree, and keep confidential material out of public commits.

## 9. Decision log

| Date | Decision |
| --- | --- |
| 2026-11-13 | Repository starts as a documentation and organization hub; no applications, stack, or infrastructure selected yet. See [ADR 0001](docs/decisions/0001-repository-scope.md). |
