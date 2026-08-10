"""Seed demo data with live Notion + Linear competitors (optional fixture mode)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.agent import run_intel_cycle
from packages.connectors import fetch_fixture, fetch_page
from packages.db.models import (
    Competitor,
    CompetitorURL,
    ImportanceFactor,
    NoiseRule,
    Snapshot,
    get_session_factory,
    init_db,
)
from packages.rag import upsert_context
from packages.scoring.importance_defaults import DEFAULT_IMPORTANCE_FACTORS

# Live public pages — no login required
LIVE_COMPETITORS = [
    {
        "name": "Notion",
        "website": "https://www.notion.com",
        "notes": "Live competitor — workspace / docs / AI",
        "urls": [
            {"url": "https://www.notion.com/pricing", "label": "pricing"},
            {"url": "https://www.notion.com/releases", "label": "changelog"},
        ],
    },
    {
        "name": "Linear",
        "website": "https://linear.app",
        "notes": "Live competitor — issue tracking / product workflow",
        "urls": [
            {"url": "https://linear.app/pricing", "label": "pricing"},
            {"url": "https://linear.app/changelog", "label": "changelog"},
        ],
    },
]

# Offline fixture mode (reliable Slack demos / CI without network)
FIXTURE_COMPETITORS = [
    {
        "name": "Acme Cloud",
        "website": "https://example.com",
        "notes": "fixture demo",
        "urls": [
            {"url": "fixture://acme_pricing/before", "label": "pricing", "after": "fixture://acme_pricing/after"},
            {"url": "fixture://acme_changelog/before", "label": "changelog", "after": "fixture://acme_changelog/after"},
        ],
    },
    {
        "name": "Northstar Analytics",
        "website": "https://example.com",
        "notes": "fixture demo",
        "urls": [
            {"url": "fixture://northstar_product/before", "label": "product", "after": "fixture://northstar_product/after"},
            {"url": "fixture://northstar_blog/before", "label": "blog", "after": "fixture://northstar_blog/after"},
        ],
    },
]

COMPANY_DOCS = [
    {
        "title": "Product summary",
        "kind": "product_summary",
        "content": (
            "SignalWatch Co builds competitive intelligence for B2B SaaS product and GTM teams. "
            "We monitor public competitor pages (pricing, changelog, product) and send source-grounded alerts. "
            "We care especially about Notion and Linear moves in productivity, AI assistants, and plan packaging."
        ),
    },
    {
        "title": "Differentiators",
        "kind": "differentiators",
        "content": (
            "Differentiator: source-grounded alerts with company-context RAG and explainable scoring. "
            "We emphasize pricing changes, AI feature launches, and ICP-matched overlap — not raw HTML dumps."
        ),
    },
    {
        "title": "ICP",
        "kind": "icp",
        "content": (
            "ICP: Series A–C B2B SaaS, 50–500 employees, product-led growth. "
            "Buyers: product ops, competitive intel, and GTM. Sensitive to competitor pricing cuts and AI workspace features."
        ),
    },
    {
        "title": "Strategic priorities",
        "kind": "priorities",
        "content": (
            "Priorities this quarter: track Notion AI / pricing packaging, Linear plan changes and changelog launches, "
            "defend mid-market positioning, and ship SSO."
        ),
    },
]


def _seed_live(db) -> None:
    for comp in LIVE_COMPETITORS:
        c = Competitor(name=comp["name"], website=comp["website"], notes=comp.get("notes"))
        db.add(c)
        db.flush()
        for u in comp["urls"]:
            row = CompetitorURL(
                competitor_id=c.id,
                url=u["url"],
                label=u["label"],
                enabled=True,
            )
            db.add(row)
            db.flush()
            print(f"  + {comp['name']} watching [{u['label']}] {u['url']}")
            # Baseline snapshot so the next cycle can diff
            fr = fetch_page(u["url"], prefer_playwright=True)
            if fr.ok:
                db.add(
                    Snapshot(
                        url_id=row.id,
                        content_hash=fr.content_hash,
                        normalized_text=fr.normalized_text,
                        raw_html=(fr.raw_html[:500000] if fr.raw_html else None),
                        fetch_method=fr.method,
                        http_status=fr.status_code,
                    )
                )
                row.last_status = "ok"
                print(f"      baseline ok via {fr.method} ({len(fr.normalized_text)} chars)")
            else:
                row.last_status = f"failed: {fr.error}"
                print(f"      ! baseline failed: {fr.error}")
        db.commit()


def _seed_fixtures(db) -> None:
    for comp in FIXTURE_COMPETITORS:
        c = Competitor(name=comp["name"], website=comp["website"], notes=comp.get("notes"))
        db.add(c)
        db.flush()
        for u in comp["urls"]:
            row = CompetitorURL(
                competitor_id=c.id,
                url=u["after"],
                label=u["label"],
                enabled=True,
            )
            db.add(row)
            db.flush()
            before = fetch_fixture(u["url"])
            if before.ok:
                db.add(
                    Snapshot(
                        url_id=row.id,
                        content_hash=before.content_hash,
                        normalized_text=before.normalized_text,
                        raw_html=before.raw_html,
                        fetch_method="fixture-seed",
                        http_status=200,
                    )
                )
                print(f"  + {comp['name']} baseline <- {u['url']}")
            else:
                print(f"  ! missing fixture {u['url']}: {before.error}")
        db.commit()


def seed(run_cycle: bool = False, fixtures: bool = False) -> None:
    print("Initializing database...")
    init_db()
    Session = get_session_factory()
    with Session() as db:
        for model in (Snapshot, CompetitorURL, Competitor, ImportanceFactor, NoiseRule):
            db.query(model).delete()
        db.commit()

        for doc in COMPANY_DOCS:
            upsert_context(db, doc["title"], doc["content"], doc["kind"])
            print(f"  + context: {doc['title']}")

        for factor in DEFAULT_IMPORTANCE_FACTORS:
            db.add(
                ImportanceFactor(
                    name=factor["name"],
                    description=factor["description"],
                    weight=factor["weight"],
                    keywords=factor["keywords"],
                )
            )
            print(f"  + importance factor: {factor['name']} ({len(factor['keywords'])} keywords)")
        db.add(NoiseRule(pattern=r"we('re| are) hiring", description="Ignore careers blurbs"))
        db.commit()

        if fixtures:
            print("Seeding FIXTURE competitors (Acme / Northstar)...")
            _seed_fixtures(db)
        else:
            print("Seeding LIVE competitors (Notion / Linear)...")
            _seed_live(db)

        print("Seed complete.")

        if run_cycle:
            print("\nRunning one intel cycle...")
            print("(Live pages often unchanged vs baseline → 0 alerts is normal.)")
            run = run_intel_cycle(db)
            print(f"Run {run.id}: {run.status}")
            print(f"Summary: {run.summary}")
            print(f"Cost: {run.cost}")
            print("Trace steps:")
            for step in run.trace or []:
                print(f"  - {step.get('step')}: {step.get('status')} — {step.get('detail')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed SignalWatch demo data")
    parser.add_argument("--run-cycle", action="store_true", help="Run one agent intel cycle after seeding")
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="Use offline Acme/Northstar fixtures instead of live Notion/Linear",
    )
    args = parser.parse_args()
    seed(run_cycle=args.run_cycle, fixtures=args.fixtures)


if __name__ == "__main__":
    main()
