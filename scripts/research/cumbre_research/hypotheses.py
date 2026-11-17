"""Opportunity hypothesis register built from signals (Phase E -> section 9).

Hypotheses are questions to validate in discovery, never diagnosed problems.
No budgets, revenue, effort or prices are produced here.
"""

from __future__ import annotations

TEMPLATES = {
    "sales / quoting": {
        "hypothesis": "Quote preparation may be manual and slow, delaying sales responses.",
        "must_be_true": "Quotes are frequent, prepared by hand (spreadsheets/email) and response time affects win rate.",
        "validation": ["Quote volume per month", "Average time to quote", "Tools used to price", "Win/loss tracking"],
        "metrics": ["time-to-quote", "quote-to-order conversion", "hours spent quoting"],
        "interventions": ["process: standard price rules", "off-the-shelf CPQ/CRM", "integration with price list", "custom quoting app"],
        "reusable": "Quoting workflows recur across B2B distributors and manufacturers.",
    },
    "sales channels / ordering": {
        "hypothesis": "Orders arriving through chat/email may be re-keyed manually into inventory/billing.",
        "must_be_true": "A material share of orders come via WhatsApp/email and are typed again downstream.",
        "validation": ["Orders by channel", "Who enters orders and where", "Error/rework rate"],
        "metrics": ["order entry time", "order errors", "lead time order→dispatch"],
        "interventions": ["process: order template", "WhatsApp Business + catalog", "CRM/ERP integration", "order-capture app"],
        "reusable": "Chat-based ordering is common among Colombian SMEs.",
    },
    "catalog / product information": {
        "hypothesis": "Product and price data may be maintained in several places (PDF, web, ERP) without a single source.",
        "must_be_true": "Price/product updates require editing multiple artifacts by hand.",
        "validation": ["Where master data lives", "Update frequency", "Number of SKUs", "Recent pricing errors"],
        "metrics": ["time to publish a price change", "catalog inconsistencies"],
        "interventions": ["data cleanup", "PIM / ERP as master", "automated catalog generation", "ecommerce sync"],
        "reusable": "Catalog sync is a repeatable pattern for distributors and retailers.",
    },
    "B2B / distribution": {
        "hypothesis": "Customer-specific pricing, credit and orders for distributors may be managed manually.",
        "must_be_true": "Multiple price lists/credit terms exist and are applied by people rather than systems.",
        "validation": ["Number of price lists", "Credit approval process", "Distributor order channel"],
        "metrics": ["order cycle time", "pricing errors", "DSO"],
        "interventions": ["pricing rules in ERP", "B2B portal", "credit workflow automation"],
        "reusable": "Distributor portals are reusable across wholesale clients.",
    },
    "operations / multi-site": {
        "hypothesis": "Consolidating information across locations may be slow or manual.",
        "must_be_true": "Sites use separate tools or send reports manually.",
        "validation": ["Systems per site", "How consolidated reports are built", "Stock transfer process"],
        "metrics": ["time to consolidate", "stock discrepancies between sites"],
        "interventions": ["shared system configuration", "data integration", "consolidated dashboard"],
        "reusable": "Multi-site consolidation dashboards are reusable.",
    },
    "inventory": {
        "hypothesis": "Inventory visibility may be unreliable, causing stockouts or overselling.",
        "must_be_true": "Stock is reconciled infrequently or across disconnected tools.",
        "validation": ["Inventory system", "Reconciliation frequency", "Stockout incidents"],
        "metrics": ["stockouts", "inventory accuracy", "lost sales"],
        "interventions": ["cycle-count process", "inventory module", "channel stock sync", "alerts"],
        "reusable": "Stock sync between ecommerce and ERP is a common pattern.",
    },
    "payments / collections": {
        "hypothesis": "Payment reconciliation and collections may rely on manual matching of transfer receipts.",
        "must_be_true": "Many payments arrive as transfers/proofs and are matched by hand.",
        "validation": ["Payment methods mix", "Reconciliation steps", "Overdue receivables"],
        "metrics": ["hours reconciling", "DSO", "unapplied payments"],
        "interventions": ["payment links/gateway", "bank-statement matching automation", "collections reminders"],
        "reusable": "Reconciliation automation is reusable.",
    },
    "customer support / returns": {
        "hypothesis": "PQRS, warranty and returns may be tracked informally, risking response-time breaches.",
        "must_be_true": "Cases are logged in email/chat without a ticketing workflow.",
        "validation": ["Case volume", "Where cases are logged", "Response-time measurement"],
        "metrics": ["first response time", "resolution time", "repeat contacts"],
        "interventions": ["ticketing tool", "PQRS workflow", "self-service status page"],
        "reusable": "PQRS workflows are reusable across Colombian companies.",
    },
    "fulfilment / customer service": {
        "hypothesis": "Customers may need to ask for order status manually.",
        "must_be_true": "Tracking is not proactively communicated.",
        "validation": ["Status inquiries per week", "Carrier integration"],
        "metrics": ["WISMO inquiries", "delivery NPS"],
        "interventions": ["automatic notifications", "tracking page", "carrier integration"],
        "reusable": "Order notifications are reusable.",
    },
    "scheduling / bookings": {
        "hypothesis": "Appointment booking may consume staff time via calls/chat.",
        "must_be_true": "Bookings are manual and reschedules are frequent.",
        "validation": ["Bookings per week", "No-show rate", "Tools used"],
        "metrics": ["booking handling time", "no-show rate"],
        "interventions": ["online scheduler", "reminders", "calendar integration"],
        "reusable": "Scheduling is a reusable pattern.",
    },
    "data capture": {
        "hypothesis": "Downloaded/manual forms may be re-typed into internal systems.",
        "must_be_true": "Forms are processed by hand at meaningful volume.",
        "validation": ["Form volume", "Who processes them", "Error rate"],
        "metrics": ["processing time per form", "errors"],
        "interventions": ["online form", "document capture/OCR", "workflow automation"],
        "reusable": "Form digitization is reusable.",
    },
    "procurement / supplier management": {
        "hypothesis": "Supplier registration and document validation may be manual.",
        "must_be_true": "Suppliers send documents by email that are checked by hand.",
        "validation": ["New suppliers per month", "Documents required", "Expiry tracking"],
        "metrics": ["onboarding time", "expired documents"],
        "interventions": ["supplier portal", "document expiry alerts"],
        "reusable": "Supplier onboarding is reusable.",
    },
    "customer onboarding / credit": {
        "hypothesis": "New-customer onboarding and credit studies may be slow and document-heavy.",
        "must_be_true": "Onboarding requires multiple manual approvals and documents.",
        "validation": ["Onboarding steps", "Time to approve", "Drop-off"],
        "metrics": ["onboarding cycle time", "approval backlog"],
        "interventions": ["digital onboarding form", "approval workflow", "credit scoring integration"],
        "reusable": "Onboarding workflows are reusable.",
    },
    "production / operations": {
        "hypothesis": "Production planning may not be linked to orders and inventory.",
        "must_be_true": "Plans are built manually from separate order and stock data.",
        "validation": ["Planning tool", "Plan frequency", "Missed deliveries"],
        "metrics": ["on-time delivery", "WIP", "planning hours"],
        "interventions": ["MRP configuration", "planning dashboard", "integration"],
        "reusable": "Planning dashboards are partially reusable.",
    },
    "logistics / distribution": {
        "hypothesis": "Delivery planning and proof-of-delivery may be manual.",
        "must_be_true": "Routes/POD are handled by phone or paper.",
        "validation": ["Deliveries per day", "Routing method", "POD method"],
        "metrics": ["deliveries per vehicle", "failed deliveries"],
        "interventions": ["routing tool", "digital POD", "customer notifications"],
        "reusable": "Digital POD is reusable.",
    },
    "growth / change": {
        "hypothesis": "Recent growth may be straining processes designed for a smaller operation.",
        "must_be_true": "Growth is recent and real (dated evidence) and processes did not scale with it.",
        "validation": ["What changed in the last 12 months", "Where bottlenecks appeared"],
        "metrics": ["volume growth vs headcount", "backlog"],
        "interventions": ["process redesign", "system upgrade", "automation of bottleneck"],
        "reusable": "Context only; not a product by itself.",
    },
    "hiring / capability": {
        "hypothesis": "The company may be building internal data/systems capability, signalling a related initiative.",
        "must_be_true": "The role is new (not a replacement) and tied to an initiative.",
        "validation": ["Purpose of role", "Initiative scope", "Build vs buy stance"],
        "metrics": ["initiative milestones"],
        "interventions": ["augment internal team", "implementation partner", "training"],
        "reusable": "Staff-augmentation pattern.",
    },
    "hiring / back-office": {
        "hypothesis": "Back-office volume (billing, inventory, data entry) may be handled by manual effort.",
        "must_be_true": "Assistants spend most time re-typing/reconciling data.",
        "validation": ["Back-office headcount", "Time per task", "Systems used"],
        "metrics": ["hours of manual entry", "error rate"],
        "interventions": ["integration", "RPA/automation", "process simplification"],
        "reusable": "Back-office automation is reusable.",
    },
    "reporting / data": {
        "hypothesis": "Key operating reports may depend on spreadsheets maintained by hand.",
        "must_be_true": "Reports are assembled manually from exports.",
        "validation": ["Top 3 reports", "Time to produce", "Data sources"],
        "metrics": ["report preparation time", "decision latency"],
        "interventions": ["standardized templates", "BI dashboard", "data pipeline"],
        "reusable": "Dashboards for SMEs are reusable.",
    },
    "reporting / decision-making": {
        "hypothesis": "Management reporting may be slow to consolidate.",
        "must_be_true": "Reports are assembled manually.",
        "validation": ["Report list", "Preparation effort"],
        "metrics": ["report lead time"],
        "interventions": ["BI dashboard", "automated extracts"],
        "reusable": "Reusable dashboard patterns.",
    },
    "compliance / regulatory": {
        "hypothesis": "Regulatory workflows (e-invoicing, habeas data requests, sanitary registrations) may require manual tracking.",
        "must_be_true": "Compliance tasks are tracked in email/spreadsheets with deadline risk.",
        "validation": ["Compliance obligations list", "Owners", "Missed deadlines"],
        "metrics": ["compliance task hours", "late filings"],
        "interventions": ["compliance calendar", "request workflow", "document repository"],
        "reusable": "Compliance trackers are reusable in Colombia.",
    },
    "customer experience": {
        "hypothesis": "Public complaints may indicate response-time or delivery process gaps (anecdotal).",
        "must_be_true": "Complaints form a pattern, not isolated cases.",
        "validation": ["Complaint volume trend", "Root causes", "Service metrics"],
        "metrics": ["complaint rate", "response time"],
        "interventions": ["service workflow", "ticketing", "proactive notifications"],
        "reusable": "Service workflows are reusable.",
    },
    "omnichannel / CRM": {
        "hypothesis": "Customer conversations may be split across many channels without a shared history.",
        "must_be_true": "Several channels are actively used and not unified.",
        "validation": ["Channels in use", "Who answers each", "Where history lives"],
        "metrics": ["response time", "lost leads"],
        "interventions": ["shared inbox", "CRM", "channel integration"],
        "reusable": "Shared-inbox/CRM setups are reusable.",
    },
    "ecommerce": {
        "hypothesis": "The online store may not be integrated with inventory and invoicing.",
        "must_be_true": "Online orders are re-entered or stock is synced manually.",
        "validation": ["Online order share", "Integration status", "Overselling incidents"],
        "metrics": ["online order handling time", "oversells"],
        "interventions": ["platform integration", "stock sync", "e-invoice automation"],
        "reusable": "Ecommerce↔ERP sync is reusable.",
    },
    "B2B self-service": {
        "hypothesis": "The customer/supplier portal may cover only part of the journey.",
        "must_be_true": "Customers still call/email for tasks the portal could serve.",
        "validation": ["Portal features", "Adoption", "Top manual requests"],
        "metrics": ["portal adoption", "manual requests avoided"],
        "interventions": ["portal enhancement", "integration", "self-service features"],
        "reusable": "Portal features are reusable.",
    },
}

DEFAULT_TEMPLATE = {
    "hypothesis": "Public signals suggest a process worth exploring.",
    "must_be_true": "The observed signal reflects a recurring internal process.",
    "validation": ["Process walkthrough"], "metrics": ["time spent"], "interventions": ["to be determined"],
    "reusable": "Unknown.",
}


def build_hypotheses(signals: list[dict], sources_by_id: dict[str, dict]) -> list[dict]:
    by_func: dict[str, list[dict]] = {}
    for s in signals:
        by_func.setdefault(s["business_function"], []).append(s)
    hyps = []
    for func, sigs in by_func.items():
        t = TEMPLATES.get(func, DEFAULT_TEMPLATE)
        src_ids = sorted({sid for s in sigs for sid in s["source_ids"]})
        independent = {sources_by_id.get(sid, {}).get("source_type") for sid in src_ids}
        official_only = independent <= {"first_party_website", "first_party_document"}
        strength = sum(s.get("strength", 1) for s in sigs)
        if len(src_ids) >= 3 and strength >= 6:
            conf = "medium"
        elif len(src_ids) >= 2 or strength >= 4:
            conf = "low-medium"
        else:
            conf = "low"
        priority_score = strength * 2 + len(src_ids) * 3 + (2 if not official_only else 0)
        hyps.append({
            "business_function": func,
            "hypothesis": t["hypothesis"],
            "signal_ids": [s["signal_id"] for s in sigs],
            "source_ids": src_ids,
            "signal_quality_note": ("Single-source signal; weak." if len(src_ids) == 1 else
                                    f"{len(src_ids)} sources; " + ("first-party only (self-presentation)." if official_only
                                                                   else "includes independent sources.")),
            "what_must_be_true": t["must_be_true"],
            "meeting_questions": list(dict.fromkeys(s["meeting_question"] for s in sigs))[:4],
            "validation_data_needed": t["validation"],
            "potential_benefits_metrics": t["metrics"],
            "possible_interventions": t["interventions"],
            "intervention_tradeoffs": "No-code/process changes are cheapest but limited; off-the-shelf tools need fit "
                                      "checks; integration/custom work needs system access and data-quality review.",
            "possible_prototype": "Map the current process with real samples, then a small proof on one workflow "
                                  "(minimum access: sample exports, no production credentials).",
            "productization": t["reusable"],
            "hypothesis_confidence": conf,
            "potential_value": "unknown until validated (qualitative: depends on volume confirmed in discovery)",
            "discovery_priority_score": priority_score,
            "feasibility_unknowns": ["current systems and access", "data quality", "process volume", "owner availability"],
            "disconfirming_evidence": "Company confirms the workflow is already automated, low-volume, or outsourced.",
            "alternative_interpretation": "; ".join(sorted({s["alternative_interpretations"] for s in sigs}))[:300],
            "reasons_not_to_pursue": "Low volume, existing adequate tool, or no owner for the change.",
            "evidence_basis": "PUBLIC_WEB → HYPOTHESIS",
        })
    hyps.sort(key=lambda h: -h["discovery_priority_score"])
    for i, h in enumerate(hyps, 1):
        h["opp_id"] = f"OPP-{i:03d}"
        h["discovery_priority_rank"] = i
    return hyps
