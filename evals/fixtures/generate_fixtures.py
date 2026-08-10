"""Generate 24 before/after HTML fixture pairs + labels.json for the eval harness."""
from __future__ import annotations

import json
from pathlib import Path

PAGES = Path(__file__).parent / "pages"
PAGES.mkdir(parents=True, exist_ok=True)

CASES: list[dict] = []


def add(
    name: str,
    before: str,
    after: str,
    *,
    should_alert: bool,
    event_type: str,
    entities: dict | None = None,
    competitor: str = "EvalCo",
    label: str = "product",
) -> None:
    (PAGES / f"{name}_before.html").write_text(before, encoding="utf-8")
    (PAGES / f"{name}_after.html").write_text(after, encoding="utf-8")
    CASES.append(
        {
            "id": name,
            "competitor": competitor,
            "page_label": label,
            "before": f"fixture://{name}/before",
            "after": f"fixture://{name}/after",
            "should_alert": should_alert,
            "event_type": event_type,
            "key_entities": entities or {},
        }
    )


# --- Pricing signals ---
add(
    "eval_price_cut_pro",
    "<html><body><h1>Pricing</h1><p>Pro $99/mo</p><p>Team $149/mo</p><footer>cookie consent</footer></body></html>",
    "<html><body><h1>Pricing</h1><p>Pro $79/mo</p><p>Team $149/mo</p><footer>cookie consent</footer></body></html>",
    should_alert=True,
    event_type="pricing_change",
    entities={"plan": "Pro", "old_price": "99", "new_price": "79"},
    label="pricing",
)
add(
    "eval_price_hike_starter",
    "<html><body><h1>Plans</h1><p>Starter $19/month</p></body></html>",
    "<html><body><h1>Plans</h1><p>Starter $29/month</p></body></html>",
    should_alert=True,
    event_type="pricing_change",
    entities={"plan": "Starter", "old_price": "19", "new_price": "29"},
    label="pricing",
)
add(
    "eval_enterprise_contact_only",
    "<html><body><h1>Pricing</h1><p>Enterprise: custom</p></body></html>",
    "<html><body><h1>Pricing</h1><p>Enterprise: Contact sales</p></body></html>",
    should_alert=False,
    event_type="other",
    label="pricing",
)
add(
    "eval_new_plan_added",
    "<html><body><h1>Pricing</h1><p>Pro $49</p></body></html>",
    "<html><body><h1>Pricing</h1><p>Pro $49</p><p>Business $99 — for mid-market</p></body></html>",
    should_alert=True,
    event_type="plan_change",
    entities={"plan": "Business", "new_price": "99", "target_segment": "mid-market"},
    label="pricing",
)

# --- Feature launches ---
add(
    "eval_ai_copilot_launch",
    "<html><body><h1>Product</h1><ul><li>Dashboards</li></ul></body></html>",
    "<html><body><h1>Product</h1><ul><li>Dashboards</li><li>Introducing AI Copilot</li></ul></body></html>",
    should_alert=True,
    event_type="feature_launch",
    entities={"feature_area": "AI Copilot"},
)
add(
    "eval_sso_launch",
    "<html><body><h1>Security</h1><p>SAML coming soon</p></body></html>",
    "<html><body><h1>Security</h1><p>SSO / SAML now available</p></body></html>",
    should_alert=True,
    event_type="feature_launch",
    entities={"feature_area": "SSO"},
)
add(
    "eval_feature_removal",
    "<html><body><h1>Features</h1><ul><li>CSV export</li><li>PDF reports</li></ul></body></html>",
    "<html><body><h1>Features</h1><ul><li>CSV export</li></ul><p>PDF reports removed</p></body></html>",
    should_alert=True,
    event_type="feature_removal",
    entities={"feature_area": "PDF reports"},
)

# --- Changelog / blog ---
add(
    "eval_changelog_minor",
    "<html><body><h1>Changelog</h1><li>Fix typo</li></body></html>",
    "<html><body><h1>Changelog</h1><li>Fix typo</li><li>Bump dependency</li></body></html>",
    should_alert=False,
    event_type="changelog_entry",
    label="changelog",
)
add(
    "eval_changelog_major_ai",
    "<html><body><h1>Changelog</h1><li>Perf tweaks</li></body></html>",
    "<html><body><h1>Changelog</h1><li>Launch: LLM insight summaries</li><li>Perf tweaks</li></body></html>",
    should_alert=True,
    event_type="feature_launch",
    entities={"feature_area": "LLM insight summaries"},
    label="changelog",
)
add(
    "eval_blog_pricing_experiment",
    "<html><body><h1>Blog</h1><p>Old post</p></body></html>",
    "<html><body><h1>Blog</h1><p>Announcing usage-based pricing for mid-market</p></body></html>",
    should_alert=True,
    event_type="blog_announcement",
    label="blog",
)
add(
    "eval_blog_culture",
    "<html><body><h1>Blog</h1><p>Hello</p></body></html>",
    "<html><body><h1>Blog</h1><p>Our engineering culture retreat</p></body></html>",
    should_alert=False,
    event_type="blog_announcement",
    label="blog",
)

# --- Noise that must NOT alert ---
add(
    "eval_noise_cookie",
    "<html><body><h1>Home</h1><p>Welcome</p></body></html>",
    "<html><body><h1>Home</h1><p>Welcome</p><div class='cookie'>We use cookies. Consent?</div></body></html>",
    should_alert=False,
    event_type="noise",
)
add(
    "eval_noise_footer_year",
    "<html><body><h1>Home</h1><p>Product</p><footer>© 2024 Acme</footer></body></html>",
    "<html><body><h1>Home</h1><p>Product</p><footer>© 2025 Acme</footer></body></html>",
    should_alert=False,
    event_type="noise",
)
add(
    "eval_noise_last_updated",
    "<html><body><h1>Docs</h1><p>Guide</p><p>Last updated: Jan 1, 2024</p></body></html>",
    "<html><body><h1>Docs</h1><p>Guide</p><p>Last updated: Feb 1, 2025</p></body></html>",
    should_alert=False,
    event_type="noise",
)
add(
    "eval_noise_careers",
    "<html><body><h1>Company</h1><p>About us</p></body></html>",
    "<html><body><h1>Company</h1><p>About us</p><p>We're hiring engineers</p></body></html>",
    should_alert=False,
    event_type="noise",
)
add(
    "eval_noise_privacy",
    "<html><body><h1>Legal</h1><p>Terms of Service v1</p></body></html>",
    "<html><body><h1>Legal</h1><p>Terms of Service v2</p><p>Privacy Policy update</p></body></html>",
    should_alert=False,
    event_type="noise",
)

# --- Positioning / misc ---
add(
    "eval_positioning_midmarket",
    "<html><body><h1>Product</h1><p>Built for startups</p></body></html>",
    "<html><body><h1>Product</h1><p>Built for mid-market operations teams</p></body></html>",
    should_alert=True,
    event_type="positioning_change",
    entities={"target_segment": "mid-market"},
)
add(
    "eval_analytics_automation",
    "<html><body><h1>Platform</h1><ul><li>Reports</li></ul></body></html>",
    "<html><body><h1>Platform</h1><ul><li>Reports</li><li>Analytics automation workflows</li></ul></body></html>",
    should_alert=True,
    event_type="feature_launch",
    entities={"feature_area": "Analytics automation"},
)
add(
    "eval_free_tier_added",
    "<html><body><h1>Pricing</h1><p>Pro $39</p></body></html>",
    "<html><body><h1>Pricing</h1><p>Free $0</p><p>Pro $39</p></body></html>",
    should_alert=True,
    event_type="plan_change",
    entities={"plan": "Free", "new_price": "0"},
    label="pricing",
)
add(
    "eval_tiny_whitespace",
    "<html><body><h1>Home</h1><p>Hello world</p></body></html>",
    "<html><body><h1>Home</h1><p>Hello  world</p></body></html>",
    should_alert=False,
    event_type="noise",
)
add(
    "eval_newsletter_cta",
    "<html><body><h1>Home</h1><p>Product</p></body></html>",
    "<html><body><h1>Home</h1><p>Product</p><p>Subscribe to our newsletter</p></body></html>",
    should_alert=False,
    event_type="noise",
)
add(
    "eval_target_segment_enterprise",
    "<html><body><h1>Solutions</h1><p>For SMBs</p></body></html>",
    "<html><body><h1>Solutions</h1><p>For enterprise security teams</p></body></html>",
    should_alert=True,
    event_type="positioning_change",
    entities={"target_segment": "enterprise"},
)
add(
    "eval_price_and_feature",
    "<html><body><h1>Pro</h1><p>$70/mo</p><ul><li>API</li></ul></body></html>",
    "<html><body><h1>Pro</h1><p>$55/mo</p><ul><li>API</li><li>New feature: realtime alerts</li></ul></body></html>",
    should_alert=True,
    event_type="pricing_change",
    entities={"plan": "Pro", "old_price": "70", "new_price": "55"},
    label="pricing",
)
add(
    "eval_empty_to_content",
    "<html><body><h1>Beta</h1></body></html>",
    "<html><body><h1>Beta</h1><p>Now available: collaborative notebooks</p></body></html>",
    should_alert=True,
    event_type="feature_launch",
    entities={"feature_area": "collaborative notebooks"},
)

# Demo fixtures already written separately — register labels too
for name, et, alert, ents, label in [
    ("acme_pricing", "pricing_change", True, {"plan": "Pro", "old_price": "79", "new_price": "59"}, "pricing"),
    ("acme_changelog", "feature_launch", True, {"feature_area": "AI Copilot"}, "changelog"),
    ("northstar_product", "feature_launch", True, {"feature_area": "LLM-powered insight summaries"}, "product"),
    ("northstar_blog", "blog_announcement", True, {}, "blog"),
]:
    if not (PAGES / f"{name}_before.html").exists():
        continue
    CASES.append(
        {
            "id": name,
            "competitor": "Demo",
            "page_label": label,
            "before": f"fixture://{name}/before",
            "after": f"fixture://{name}/after",
            "should_alert": alert,
            "event_type": et,
            "key_entities": ents,
        }
    )

out = Path(__file__).parent / "labels.json"
out.write_text(json.dumps(CASES, indent=2), encoding="utf-8")
print(f"Wrote {len(CASES)} cases -> {out}")
