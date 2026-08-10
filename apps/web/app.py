"""
SignalWatch — Streamlit control plane
Works locally (via FastAPI) or on Streamlit Community Cloud (inline backend).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Ensure repo root is on sys.path (Streamlit Cloud runs apps/web/app.py directly)
def _bootstrap_path() -> str:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2],  # <repo>/apps/web/app.py → <repo>
        here.parents[1].parent if len(here.parents) > 1 else here.parent,
        Path.cwd(),
        Path("/mount/src/marketpulse-agent"),
    ]
    for root in candidates:
        if (root / "packages" / "settings.py").exists():
            root_s = str(root)
            if root_s not in sys.path:
                sys.path.insert(0, root_s)
            return root_s
    # last resort
    fallback = str(here.parents[2])
    if fallback not in sys.path:
        sys.path.insert(0, fallback)
    return fallback


_REPO_ROOT = _bootstrap_path()

import httpx
import plotly.express as px
import streamlit as st

KIND_LABELS = {
    "product_summary": "Product",
    "differentiators": "Differentiators",
    "icp": "ICP",
    "priorities": "Priorities",
}

SECRET_KEYS = [
    "DATABASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "OPENAI_EMBEDDING_MODEL",
    "SLACK_WEBHOOK_URL",
    "API_BASE_URL",
    "SIGNALWATCH_INLINE",
    "ALERT_SCORE_THRESHOLD",
    "REVIEW_BAND_LOW",
    "REVIEW_BAND_HIGH",
    "MAX_PAGES_PER_RUN",
]


def _apply_secrets() -> dict[str, str]:
    """Copy Streamlit Cloud secrets into env for packages.settings. Returns applied map."""
    applied: dict[str, str] = {}
    try:
        secrets = dict(st.secrets)
    except Exception:
        return applied

    # Support flat keys and nested {"general": {"DATABASE_URL": ...}}
    flat: dict[str, Any] = {}
    for key, value in secrets.items():
        if hasattr(value, "keys"):  # nested section
            for k2, v2 in dict(value).items():
                flat[str(k2)] = v2
        else:
            flat[str(key)] = value

    for key in SECRET_KEYS:
        if key in flat and flat[key] not in (None, ""):
            os.environ[key] = str(flat[key]).strip().strip('"').strip("'")
            applied[key] = "set"
    # Always force inline on Streamlit Cloud
    os.environ.setdefault("SIGNALWATCH_INLINE", "1")
    return applied


st.set_page_config(page_title="SignalWatch", page_icon="📡", layout="wide")
_applied = _apply_secrets()

from packages.db.models import reset_engine
from packages.settings import clear_settings_cache, get_settings

clear_settings_cache()
reset_engine()
_settings = get_settings()

# On Streamlit Cloud, run DB/agent in-process. Locally, try FastAPI then fall back.
_INLINE = os.getenv("SIGNALWATCH_INLINE", "").lower() in {"1", "true", "yes"}
_API = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
_ON_CLOUD = bool(
    os.getenv("STREAMLIT_SHARING_MODE")
    or os.getenv("STREAMLIT_RUNTIME_ENV") == "cloud"
    or "streamlit.app" in os.getenv("HOSTNAME", "")
)
if _ON_CLOUD:
    _INLINE = True

_db_ok = bool(
    _settings.database_url
    and _settings.database_url.startswith("postgresql")
    and "localhost" not in _settings.database_url
    and "127.0.0.1" not in _settings.database_url
)

if _ON_CLOUD and not _db_ok:
    st.error("Supabase DATABASE_URL is missing or still points to localhost.")
    st.markdown(
        """
### Fix in Streamlit Cloud
1. Click **Manage app** (bottom right) → **Settings** → **Secrets**
2. Paste this (replace with your real values):

```toml
SIGNALWATCH_INLINE = "1"

DATABASE_URL = "postgresql+psycopg://postgres.YOUR_REF:YOUR_PASSWORD@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"

OPENAI_API_KEY = "your-key"
OPENAI_BASE_URL = "https://api.deepseek.com"
OPENAI_MODEL = "deepseek-chat"
OPENAI_EMBEDDING_MODEL = "local-hash"

SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/..."
```

3. Click **Save**, then **Reboot app**.
        """
    )
    st.stop()


def _inline_api(method: str, path: str, **kwargs: Any) -> Any:
    from apps.web import backend as b

    b.ensure_db()
    parsed = urlparse(path)
    route = parsed.path
    qs = parse_qs(parsed.query)
    body = kwargs.get("json") or {}

    if method == "GET" and route == "/alerts":
        return b.list_alerts()
    if method == "GET" and route.startswith("/runs"):
        limit = int(qs.get("limit", ["20"])[0])
        return b.list_runs(limit=limit)
    if method == "POST" and route == "/runs/trigger":
        return b.trigger_run()
    if method == "GET" and route == "/metrics/quality":
        return b.quality_metrics()
    if method == "GET" and route == "/competitors":
        return b.list_competitors()
    if method == "POST" and route == "/competitors":
        return b.create_competitor(body.get("name", ""), body.get("website"))
    if method == "POST" and "/urls" in route and route.startswith("/competitors/"):
        cid = route.split("/")[2]
        return b.add_competitor_url(cid, body.get("url", ""), body.get("label", "other"), body.get("enabled", True))
    if method == "GET" and route == "/company-context":
        return b.list_company_context()
    if method == "POST" and route == "/company-context":
        return b.add_company_context(body["title"], body["content"], body.get("kind", "product_summary"))
    if method == "GET" and route == "/importance-factors":
        return b.list_importance_factors()
    if method == "POST" and route == "/importance-factors":
        return b.add_importance_factor(
            body["name"],
            body.get("keywords") or [],
            float(body.get("weight", 1.0)),
            body.get("description") or "",
        )
    if method == "POST" and route == "/feedback":
        return b.submit_feedback(str(body["alert_id"]), body["label"], body.get("note"))
    if method == "GET" and route == "/weights":
        return b.get_weights()
    if method == "GET" and route == "/settings/public":
        return b.public_settings()
    if method == "GET" and route.startswith("/audit"):
        limit = int(qs.get("limit", ["30"])[0])
        return b.audit_log(limit=limit)
    raise ValueError(f"Unsupported inline route: {method} {route}")


def api(method: str, path: str, **kwargs: Any) -> Any:
    use_inline = _INLINE or os.getenv("SIGNALWATCH_INLINE", "").lower() in {"1", "true", "yes"}
    if use_inline:
        try:
            return _inline_api(method, path, **kwargs)
        except Exception as e:
            st.error(f"Backend error: {e}")
            return None

    url = f"{_API}{path}"
    try:
        r = httpx.request(method, url, timeout=120, **kwargs)
        r.raise_for_status()
        return r.json()
    except Exception:
        # Fallback to inline for local demos when API isn't running
        try:
            return _inline_api(method, path, **kwargs)
        except Exception as e2:
            st.error("Can't reach API and inline backend failed.")
            st.caption(str(e2))
            return None


def status_badge(status: str) -> str:
    s = (status or "").lower()
    if s in ("ok", "sent", "completed"):
        return f"🟢 {status}"
    if s in ("review", "completed_with_errors"):
        return f"🟡 {status}"
    if s in ("failed", "error", "degraded"):
        return f"🔴 {status}"
    return status or "—"


st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Source+Sans+3:wght@400;600&display=swap');

      .stApp {
        background:
          radial-gradient(1200px 500px at 10% -10%, #d8efe6 0%, transparent 55%),
          radial-gradient(900px 400px at 100% 0%, #e7f0ea 0%, transparent 50%),
          linear-gradient(180deg, #f4f7f5 0%, #eef2f0 100%);
      }
      html, body, [class*="css"] { font-family: 'Source Sans 3', sans-serif; color: #1c2b26; }
      h1, h2, h3 { font-family: 'Fraunces', serif !important; letter-spacing: -0.02em; }

      .sw-hero {
        background: linear-gradient(125deg, #0f2e28 0%, #1a4d3e 48%, #2f6b4f 100%);
        color: #eef7f2;
        padding: 1.75rem 1.8rem 1.5rem;
        border-radius: 18px;
        margin-bottom: 1.25rem;
        box-shadow: 0 12px 40px rgba(15, 46, 40, 0.22);
      }
      .sw-hero h1 { color: #f4fbf7 !important; margin: 0; font-size: 2.1rem; font-weight: 700; }
      .sw-hero p { opacity: 0.88; margin: 0.45rem 0 0; font-size: 1.05rem; max-width: 36rem; }

      .sw-card {
        background: rgba(255,255,255,0.78);
        border: 1px solid rgba(28, 43, 38, 0.08);
        border-radius: 14px;
        padding: 1rem 1.15rem;
        margin-bottom: 0.85rem;
      }
      .sw-kicker {
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #4d6b5f;
        font-weight: 600;
        margin-bottom: 0.25rem;
      }
      .sw-muted { color: #5b6f67; font-size: 0.92rem; }
      .sw-chip {
        display: inline-block;
        background: #e4f0ea;
        color: #1f4338;
        border-radius: 999px;
        padding: 0.15rem 0.55rem;
        margin: 0.15rem 0.25rem 0.15rem 0;
        font-size: 0.78rem;
        font-weight: 600;
      }
      div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.75);
        border: 1px solid rgba(28, 43, 38, 0.08);
        border-radius: 14px;
        padding: 0.75rem 1rem;
      }
      .stTabs [data-baseweb="tab-list"] { gap: 0.4rem; }
      .stTabs [data-baseweb="tab"] {
        background: rgba(255,255,255,0.55);
        border-radius: 999px;
        padding: 0.4rem 1rem;
      }
    </style>
    <div class="sw-hero">
      <h1>SignalWatch</h1>
      <p>Watch competitors. Score what matters. Alert with sources — then learn from your feedback.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

tabs = st.tabs(
    ["Dashboard", "Competitors", "Company context", "Alerts", "Runs", "Scoring"]
)

# --- Dashboard ---
with tabs[0]:
    alerts = api("GET", "/alerts") or []
    runs = api("GET", "/runs?limit=8") or []
    metrics = api("GET", "/metrics/quality") or {"series": [], "overall_useful_pct": 0, "total_feedback": 0}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Alerts", len(alerts))
    c2.metric("Recent runs", len(runs))
    c3.metric("Useful feedback", f"{metrics.get('overall_useful_pct', 0)}%")
    c4.metric("Feedback count", metrics.get("total_feedback", 0))

    run_col, _ = st.columns([1, 2])
    with run_col:
        if st.button("Run intel cycle", type="primary", use_container_width=True):
            with st.spinner("Fetching competitors → diff → score → alert..."):
                result = api("POST", "/runs/trigger")
            if result:
                st.success(f"{result.get('status')}: {result.get('summary')}")
                st.rerun()

    series = metrics.get("series") or []
    if series:
        fig = px.line(
            series,
            x="day",
            y="useful_pct",
            markers=True,
            title="Alert quality over time",
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(255,255,255,0.5)",
            yaxis_title="% useful",
            xaxis_title="",
            margin=dict(l=20, r=20, t=50, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Latest signals")
    if not alerts:
        st.caption("No alerts yet. Live pages only alert when something changes.")
    for a in alerts[:6]:
        st.markdown(
            f"""
            <div class="sw-card">
              <div class="sw-kicker">{a.get('status', '')} · score {a.get('score', 0):.2f}</div>
              <strong>{a.get('title', 'Alert')}</strong>
              <div class="sw-muted" style="margin-top:0.35rem">{a.get('what_changed', '')}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# --- Competitors ---
with tabs[1]:
    st.markdown("#### Watched competitors")
    comps = api("GET", "/competitors") or []
    if not comps:
        st.caption("No competitors yet. Seed with Notion + Linear or add one below.")
    for c in comps:
        with st.expander(f"{c['name']} · {len(c.get('urls') or [])} pages", expanded=True):
            if c.get("website"):
                st.markdown(f"Website: [{c['website']}]({c['website']})")
            for u in c.get("urls") or []:
                st.markdown(
                    f"- **{u.get('label')}** — [{u.get('url')}]({u.get('url')})  \n"
                    f"  {status_badge(str(u.get('last_status') or 'pending'))}"
                )

    st.markdown("#### Add competitor")
    with st.form("add_comp", clear_on_submit=True):
        n1, n2 = st.columns(2)
        name = n1.text_input("Name", placeholder="Notion")
        website = n2.text_input("Website", placeholder="https://www.notion.com")
        url = st.text_input("Public page to watch", placeholder="https://www.notion.com/pricing")
        label = st.selectbox("Page type", ["pricing", "changelog", "blog", "product", "other"])
        if st.form_submit_button("Add", type="primary") and name and url:
            created = api("POST", "/competitors", json={"name": name, "website": website or None})
            if created:
                api(
                    "POST",
                    f"/competitors/{created['id']}/urls",
                    json={"url": url, "label": label, "enabled": True},
                )
                st.success(f"Added {name}")
                st.rerun()

# --- Company context ---
with tabs[2]:
    st.markdown("#### Your company context")
    st.caption("Used to explain why a competitor move matters.")
    ctx = api("GET", "/company-context") or []
    # Deduplicate by title+kind showing latest-ish content (API already newest-first)
    seen: set[str] = set()
    shown = 0
    for c in ctx:
        key = f"{c.get('kind')}:{c.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        shown += 1
        kind = KIND_LABELS.get(c.get("kind", ""), c.get("kind", "Context"))
        st.markdown(
            f"""
            <div class="sw-card">
              <div class="sw-kicker">{kind}</div>
              <strong>{c.get('title', '')}</strong>
              <div style="margin-top:0.45rem; line-height:1.45">{c.get('content', '')}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    if shown == 0:
        st.caption("No company context yet.")

    with st.expander("Add company context"):
        with st.form("add_ctx", clear_on_submit=True):
            title = st.text_input("Title")
            kind = st.selectbox(
                "Type",
                ["product_summary", "differentiators", "icp", "priorities"],
                format_func=lambda k: KIND_LABELS.get(k, k),
            )
            content = st.text_area("Content", height=120)
            if st.form_submit_button("Save") and title and content:
                api("POST", "/company-context", json={"title": title, "kind": kind, "content": content})
                st.success("Saved")
                st.rerun()

    st.markdown("#### Watch keywords")
    st.caption("When page changes mention these words, alerts score higher.")
    factors = api("GET", "/importance-factors") or []
    if not factors:
        st.caption("No importance factors configured.")
    for f in factors:
        chips = "".join(f'<span class="sw-chip">{k}</span>' for k in (f.get("keywords") or [])[:18])
        extra = len(f.get("keywords") or []) - 18
        if extra > 0:
            chips += f'<span class="sw-chip">+{extra} more</span>'
        st.markdown(
            f"""
            <div class="sw-card">
              <div class="sw-kicker">weight {f.get('weight', 1):.2f}</div>
              <strong>{f.get('name', '').replace('_', ' ').title()}</strong>
              <div class="sw-muted" style="margin:0.35rem 0 0.55rem">{f.get('description', '')}</div>
              <div>{chips}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with st.expander("Add watch keywords"):
        with st.form("add_factor", clear_on_submit=True):
            fname = st.text_input("Name", placeholder="pricing_and_offers")
            fkw = st.text_input("Keywords", placeholder="sale, offer, discount, new price")
            fw = st.slider("Weight", 0.1, 2.0, 1.0)
            if st.form_submit_button("Add") and fname:
                api(
                    "POST",
                    "/importance-factors",
                    json={
                        "name": fname,
                        "keywords": [k.strip() for k in fkw.split(",") if k.strip()],
                        "weight": fw,
                        "description": "",
                    },
                )
                st.rerun()

# --- Alerts ---
with tabs[3]:
    st.markdown("#### Alerts")
    alerts = api("GET", "/alerts") or []
    if not alerts:
        st.caption("No alerts yet.")
    for a in alerts:
        review = " · needs review" if a.get("needs_human_review") else ""
        with st.expander(f"{a.get('title')} · {a.get('score', 0):.2f}{review}"):
            st.markdown(f"**What changed**  \n{a.get('what_changed')}")
            st.markdown(f"**Why it matters**  \n{a.get('why_it_matters')}")
            st.markdown(f"**Suggested action**  \n{a.get('suggested_action')}")
            st.markdown(f"[Open source]({a.get('source_url')})")
            st.text_area(
                "Evidence snippet",
                a.get("quoted_snippet") or "",
                height=100,
                disabled=True,
                label_visibility="collapsed",
                key=f"snippet-{a.get('id')}",
            )

            bd = a.get("score_breakdown") or {}
            expl = bd.get("explanation") or []
            if expl:
                st.markdown("**Why this score**")
                for line in expl:
                    st.markdown(f"- {line}")

            cols = st.columns(3)
            for i, label in enumerate(["useful", "meh", "noise"]):
                if cols[i].button(label.title(), key=f"fb-{a['id']}-{label}", use_container_width=True):
                    result = api("POST", "/feedback", json={"alert_id": a["id"], "label": label})
                    if result:
                        notes = ((result.get("weight_change") or {}).get("notes")) or []
                        st.success(f"Marked {label}")
                        for n in notes[:6]:
                            st.caption(n)

# --- Runs ---
with tabs[4]:
    st.markdown("#### Agent runs")
    runs = api("GET", "/runs") or []
    if not runs:
        st.caption("No runs yet.")
    for r in runs:
        with st.expander(f"{r.get('started_at', '')[:19]} · {r.get('status')} · {r.get('summary')}"):
            cost = r.get("cost") or {}
            if cost:
                m1, m2, m3 = st.columns(3)
                m1.metric("Scrape ms", cost.get("scrape_ms", 0))
                m2.metric("LLM calls", cost.get("llm_calls", 0))
                m3.metric("Tokens in", cost.get("llm_tokens_in", 0))
            st.markdown("**Timeline**")
            for step in r.get("trace") or []:
                st.markdown(
                    f"- `{step.get('step')}` · **{step.get('status')}** — {step.get('detail')}"
                )

    with st.expander("Fetch audit log"):
        audit = api("GET", "/audit?limit=20") or []
        if audit:
            rows = [
                {
                    "When": (a.get("created_at") or "")[:19],
                    "URL": a.get("url"),
                    "Result": a.get("detail"),
                }
                for a in audit
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption("No audit entries.")

# --- Scoring ---
with tabs[5]:
    st.markdown("#### Scoring weights")
    weights = api("GET", "/weights") or {}
    current = weights.get("current") or {}
    if current:
        rows = [{"Signal": k.replace("_", " "), "Weight": round(float(v), 3)} for k, v in current.items()]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No weights loaded.")

    settings = api("GET", "/settings/public") or {}
    if settings:
        st.markdown("#### Thresholds")
        t1, t2, t3 = st.columns(3)
        t1.metric("Alert threshold", settings.get("alert_score_threshold"))
        band = settings.get("review_band") or [0, 0]
        t2.metric("Review band", f"{band[0]} – {band[1]}")
        t3.metric("Max pages / run", settings.get("max_pages_per_run"))
        st.caption(
            f"Model: {settings.get('model')} · LLM {'on' if settings.get('llm_enabled') else 'off (heuristics)'}"
        )
