import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

from sqlalchemy import create_engine, select, desc
from sqlalchemy.orm import sessionmaker

# Import your existing logic
from app.db import Base
from app.models import MarketSnapshot
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
    .muted {{ color: #6b7280; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 12px; margin-top: 14px; }}
    .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px; }}
    .title {{ font-weight: 700; margin-bottom: 6px; }}
    .row {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .pill {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; padding: 2px 6px; border: 1px solid #e5e7eb; border-radius: 999px; background:#f9fafb; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    td {{ padding: 2px 0; vertical-align: top; }}
    td:first-child {{ color:#6b7280; width: 110px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>P2P Bet Scanner</h1>
    <div class="muted">
      Static snapshot generated at <b>{generated_at}</b> (local time).
      Connector: <b>{connector}</b>.
    </div>
    <div class="muted">
      Note: “insider likelihood” here is a heuristic ranking based on unusual, market-relative flow and odds-aware weighting.
      It is not a claim of wrongdoing.
    </div>

    <div class="grid">
      {cards}
    </div>
  </div>
</body>
</html>
"""

def render_cards(results: List[Dict[str, Any]]) -> str:
    cards = []
    for r in results:
        cards.append(f"""
        <div class="card">
          <div class="title">{escape(r.get("title",""))}</div>
          <div class="row">
            <span class="pill">{escape(r.get("platform",""))}:{escape(r.get("market_id",""))}</span>
            <span class="pill">score={r.get("score",0):.4f}</span>
          </div>
          <table>
            <tr><td>p</td><td>{r.get("p",0):.3f}</td></tr>
            <tr><td>flow</td><td>{r.get("flow",0):.2f}</td></tr>
            <tr><td>depth</td><td>{r.get("depth",0):.2f}</td></tr>
            <tr><td>z_flow</td><td>{r.get("z_flow",0):.2f}</td></tr>
            <tr><td>depth_ratio</td><td>{r.get("depth_ratio",0):.3f}</td></tr>
            <tr><td>entropy</td><td>{r.get("entropy",0):.3f}</td></tr>
            <tr><td>ts</td><td>{escape(r.get("ts",""))}</td></tr>
          </table>
        </div>
        """)
    return "\n".join(cards)

def escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#39;"))

def main():
    connector = os.getenv("CONNECTOR", "mock")

    # Use the same SQLite file location each time (repo/backend/scanner.db)
    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        # Pull a few times so min_hist is satisfied even from a cold start
        n_polls = int(os.getenv("GEN_POLLS", "6"))
        for _ in range(n_polls):
            poll_once(db)

        # Rank and take top N
        limit = int(os.getenv("GEN_LIMIT", "25"))
        max_p = float(os.getenv("GEN_MAX_P", "0.98"))
        min_score = float(os.getenv("GEN_MIN_SCORE", "0.0"))
        results = get_top(db, limit=limit, max_p=max_p, min_score=min_score, min_hist=3)

    DOCS.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = HTML_TEMPLATE.format(
        generated_at=generated_at,
        connector=connector,
        cards=render_cards(results),
    )

    out_path = DOCS / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path} with {len(results)} results.")

if __name__ == "__main__":
    main()

