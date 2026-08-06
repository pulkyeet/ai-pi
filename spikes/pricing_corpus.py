"""The 40-page pricing corpus shared by crawl_static.py and crawl_js.py.

Real, currently-live pricing URLs across the SaaS categories the Phase 14
benchmark will cover. `guess_paths` is always the fixed three-candidate set
the masterplan proposes; `known_path` is the actual path for that vendor
today, which may or may not be one of the guesses — that gap is exactly what
the path-guessing hit-rate measurement is checking.
"""

from __future__ import annotations

from dataclasses import dataclass

GUESS_PATHS = ("/pricing", "/plans", "/pricing-plans")


@dataclass(frozen=True)
class PricingPage:
    domain: str
    known_path: str
    category: str


CORPUS: list[PricingPage] = [
    PricingPage("asana.com", "/pricing", "project-management"),
    PricingPage("trello.com", "/pricing", "project-management"),
    PricingPage("monday.com", "/pricing", "project-management"),
    PricingPage("clickup.com", "/pricing", "project-management"),
    PricingPage("notion.so", "/pricing", "productivity"),
    PricingPage("airtable.com", "/pricing", "productivity"),
    PricingPage("hubspot.com", "/pricing", "crm"),
    PricingPage("zendesk.com", "/pricing", "support"),
    PricingPage("intercom.com", "/pricing", "support"),
    PricingPage("freshdesk.com", "/pricing", "support"),
    PricingPage("slack.com", "/pricing", "communication"),
    PricingPage("zoom.us", "/pricing", "communication"),
    PricingPage("calendly.com", "/pricing", "scheduling"),
    PricingPage("typeform.com", "/pricing", "forms"),
    PricingPage("mailchimp.com", "/pricing", "email-marketing"),
    PricingPage("sendgrid.com", "/pricing", "email-infra"),
    PricingPage("twilio.com", "/pricing", "communication-infra"),
    PricingPage("stripe.com", "/pricing", "payments"),
    PricingPage("github.com", "/pricing", "dev-tools"),
    PricingPage("gitlab.com", "/pricing", "dev-tools"),
    PricingPage("vercel.com", "/pricing", "dev-infra"),
    PricingPage("netlify.com", "/pricing", "dev-infra"),
    PricingPage("datadoghq.com", "/pricing", "observability"),
    PricingPage("newrelic.com", "/pricing", "observability"),
    PricingPage("sentry.io", "/pricing", "observability"),
    PricingPage("figma.com", "/pricing", "design"),
    PricingPage("canva.com", "/pricing", "design"),
    PricingPage("webflow.com", "/pricing", "website-builder"),
    PricingPage("squarespace.com", "/pricing", "website-builder"),
    PricingPage("shopify.com", "/pricing", "ecommerce"),
    PricingPage("bigcommerce.com", "/pricing", "ecommerce"),
    PricingPage("zapier.com", "/pricing", "automation"),
    PricingPage("make.com", "/pricing", "automation"),
    PricingPage("loom.com", "/pricing", "video"),
    PricingPage("miro.com", "/pricing", "design"),
    PricingPage("linear.app", "/pricing", "project-management"),
    PricingPage("postman.com", "/pricing", "dev-tools"),
    PricingPage("auth0.com", "/pricing", "security"),
    # Deliberate guess-miss cases: multi-product enterprise vendors whose
    # pricing lives off the three-candidate path, to keep the corpus honest.
    PricingPage("atlassian.com", "/software/jira/pricing", "project-management"),
    PricingPage("salesforce.com", "/editions-pricing/sales-cloud/", "crm"),
]

assert len(CORPUS) == 40, f"corpus must have exactly 40 pages, has {len(CORPUS)}"
