from sqlalchemy import select, desc
from datetime import datetime
from ..models import MarketSnapshot
from ..scoring import compute_score
from ..connectors.mock import MockConnector

ROLLING_N = 60
connector = MockConnector()

def poll_once(db):
    markets = connector.fetch_markets()
    for m in markets:
        db.add(MarketSnapshot(
            platform=m["platform"],
            market_id=m["market_id"],
            title=m["title"],
            ts=datetime.utcnow(),
            p=m["p"],
            flow=m["flow"],
            depth=m["depth"],
        ))
    db.commit()

def get_top(db, limit=25, max_p=0.98):
    rows = db.execute(
        select(MarketSnapshot.platform, MarketSnapshot.market_id).distinct()
    ).all()

    results = []
    for platform, market_id in rows:
        latest = db.execute(
            select(MarketSnapshot)
            .where(MarketSnapshot.platform == platform, MarketSnapshot.market_id == market_id)
            .order_by(desc(MarketSnapshot.ts))
            .limit(1)
        ).scalar_one_or_none()
        if not latest or latest.p > max_p:
            continue

        hist = db.execute(
            select(MarketSnapshot.flow)
            .where(MarketSnapshot.platform == platform, MarketSnapshot.market_id == market_id)
            .order_by(desc(MarketSnapshot.ts))
            .limit(ROLLING_N)
        ).scalars().all()

        if len(hist) < 8:
            continue

        mu = sum(hist) / len(hist)
        var = sum((x - mu) ** 2 for x in hist) / max(len(hist) - 1, 1)
        sigma = var ** 0.5

        bd = compute_score(latest.p, latest.flow, latest.depth, mu, sigma)
        results.append({
            "title": latest.title,
            "p": latest.p,
            "flow": latest.flow,
            "depth": latest.depth,
            "score": bd.score,
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]
