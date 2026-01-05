# backend/scripts/generate_site.py
#
# Generates a static GitHub Pages site at docs/index.html by:
# 1) polling the selected connector a few times to warm up history
# 2) ranking markets with get_top()
# 3) filtering down to "potentially informed flow" candidates
# 4) writing a self-contained HTML page
#
# Run (from backend/):
#   source venv/bin/activate
#   export CONNECTOR=kalshi
#   python -m scripts.generate_site
#
# Useful env vars:
#   CONNECTOR=kalshi|mock
#   GEN_POLLS=6
#   GEN_LIMIT=80
#   GEN_MAX_P=0.98
#   GEN_MIN_SCORE=0.0
#
# Publish filter knobs:
#   PUBLISH_Z_MIN=2.5
#   PUBLISH_DEPTH_RATIO_MIN=0.05
#   PUBLISH_ENTROPY_MIN=0.45
#   PUBLISH_P_MIN=0.05
#   PUBLISH_P_MAX=0.95

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# These imports assume you run as a module: python -m scripts.generate_site
from app.db import Base
from app.jobs.poller import poll_once, get_top


ROOT = Path(__file__).resolve().parents[2]  # repo root
DOCS = ROOT / "docs"
DB_PATH = ROOT / "backend" / "scanner.db"


HTML_TEMPLATE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>P2P Bet Scanner</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system; margin: 0; background: #fafafa; }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
    h1 {{ margin: 0 0 6px 0; font-size: 22px; }}
    .muted {{ color: #6b7280; font-size: 13px; line-height: 1.35; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 12px; margin-top: 14px; }}
    .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px; }}
    .title {{ font-weight: 700; margin-bottom: 6px; }}
    .row {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom: 8px; }}
    .pill {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; padding: 2px 6px; border: 1px solid #e5e7eb; border-radius: 999px; background:#f9fafb; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    td {{ padding: 2px 0; vertical-align: top; }}
    td:first-child {{ color:#6b7280; width: 120px; }}
    .empty {{ margin-top: 16px; padding: 12px; border: 1px dashed #d1d5db; border-radius: 12px; background: #fff; }}
    .small {{ font-size: 12px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>P2P Bet Scanner</h1>
    <div class="muted">
      Static snapshot generated at <b>{generated_at}</b> (local time). Connector: <b>{connector}</b>.
    </div>
    <div class="muted">
      Showing markets with unusually aggressive, odds-aware flow (heuristic ranking). This is not a claim of wrongdoing.
    </div>
    <div class="muted small">
      Publish thresholds: z_flow ≥ {z_min}, depth_ratio ≥ {dr_min}, entropy ≥ {h_min}, p ∈ [{p_min}, {p_max}]
    </div>

    {body}
  </div>
</body>
</html>
"""


def escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#39;"))


def _opt_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def render_cards(results: List[Dict[str, Any]]) -> str:
    cards = []
    for r in results:
        title = escape(str(r.get("title", "")))
        pid = escape(str(r.get("platform", ""))) + ":" + escape(str(r.get("market_id", "")))

        p = float(r.get("p", 0.0))
        flow = float(r.get("flow", 0.0))
        depth = float(r.get("depth", 0.0))
        z = float(r.get("z_flow", 0.0))
        dr = float(r.get("depth_ratio", 0.0))
        H = float(r.get("entropy", 0.0))
        score = float(r.get("score", 0.0))
        ts = escape(str(r.get("ts", "")))

        cards.append(f"""
        <div class="card">
          <div class="title">{title}</div>
          <div class="row">
            <span class="pill">{pid}</span>
            <span class="pill">score={score:.4f}</span>
          </div>
          <table>
            <tr><td>p</td><td>{p:.3f}</td></tr>
            <tr><td>flow</td><td>{flow:.2f}</td></tr>
            <tr><td>depth</td><td>{depth:.2f}</td></tr>
            <tr><td>z_flow</td><td>{z:.2f}</td></tr>
            <tr><td>depth_ratio</td><td>{dr:.3f}</td></tr>
            <tr><td>entropy</td><td>{H:.3f}</td></tr>
            <tr><td>timestamp</td><td>{ts}</td></tr>
          </table>
        </div>
        """)
    return '<div class="grid">' + "\n".join(cards) + "</div>"


def main():
    connector = os.getenv("CONNECTOR", "mock")

    # Generation knobs
    n_polls = int(os.getenv("GEN_POLLS", "6"))
    limit = int(os.getenv("GEN_LIMIT", "80"))
    max_p = float(os.getenv("GEN_MAX_P", "0.98"))
    min_score = float(os.getenv("GEN_MIN_SCORE", "0.0"))

    # Publish filter knobs ("potentially informed flow")
    Z_MIN = float(os.getenv("PUBLISH_Z_MIN", "2.5"))
    DR_MIN = float(os.getenv("PUBLISH_DEPTH_RATIO_MIN", "0.05"))
    H_MIN = float(os.getenv("PUBLISH_ENTROPY_MIN", "0.45"))
    P_MIN = float(os.getenv("PUBLISH_P_MIN", "0.05"))
    P_MAX = float(os.getenv("PUBLISH_P_MAX", "0.95"))

    # Use the same SQLite file location each time (repo/backend/scanner.db)
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        # Warm-up: get enough history for z-scores
        for _ in range(n_polls):
            poll_once(db)

        # Pull candidate set
        results = get_top(
            db,
            limit=limit,
            max_p=max_p,
            min_score=min_score,
            min_hist=3,  # keep this low for static generation
        )

    # Apply publish filter
    filtered: List[Dict[str, Any]] = []
    for r in results:
        p = _opt_float(r.get("p")) or 0.0
        if not (P_MIN <= p <= P_MAX):
            continue
        if (_opt_float(r.get("z_flow")) or 0.0) < Z_MIN:
            continue
        if (_opt_float(r.get("depth_ratio")) or 0.0) < DR_MIN:
            continue
        if (_opt_float(r.get("entropy")) or 0.0) < H_MIN:
            continue
        filtered.append(r)

    results = filtered

    DOCS.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if len(results) == 0:
        body = f"""
        <div class="empty">
          <b>No markets passed the publish filter.</b>
          <div class="muted">
            This often means markets were quiet during the sampling window. Try loosening thresholds or increasing GEN_LIMIT/GEN_POLLS.
          </div>
        </div>
        """
    else:
        body = render_cards(results)

    html = HTML_TEMPLATE.format(
        generated_at=generated_at,
        connector=escape(connector),
        z_min=Z_MIN,
        dr_min=DR_MIN,
        h_min=H_MIN,
        p_min=P_MIN,
        p_max=P_MAX,
        body=body,
    )

    out_path = DOCS / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path} with {len(results)} results.")


if __name__ == "__main__":
    main()

