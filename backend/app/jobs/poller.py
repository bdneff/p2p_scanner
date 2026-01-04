# backend/app/jobs/poller.py

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..models import MarketSnapshot
from ..scoring import compute_score

from ..connectors.mock import MockConnector

# If you created this file: backend/app/connectors/kalshi.py
# (safe import: only used if CONNECTOR=kalshi)
try:
    from ..connectors.kalshi import KalshiConnector
except Exception:
    KalshiConnector = None  # type: ignore


# Rolling window length for per-market baseline stats
ROLLING_N = int(os.getenv("ROLLING_N", "60"))

# Which connector to use: "mock" (default) or "kalshi"
CONNECTOR_NAME = os.getenv("CONNECTOR", "mock").lower()


def get_connector():
    """
    Initialize connector once so it can keep state (e.g., Kalshi volume deltas).
    """
    if CONNECTOR_NAME == "kalshi":
        if KalshiConnector is None:
            raise RuntimeError(
                "CONNECTOR=kalshi but KalshiConnector import failed. "
                "Did you create backend/app/connectors/kalshi.py?"
            )
        limit_markets = int(os.getenv("KALSHI_LIMIT_MARKETS", "50"))
        band_cents = int(os.getenv("KALSHI_BAND_CENTS", "3"))
        return KalshiConnector(limit_markets=limit_markets, band_cents=band_cents)

    # default
    n_markets = int(os.getenv("MOCK_N_MARKETS", "60"))
    seed = int(os.getenv("MOCK_SEED", "7"))
    return MockConnector(n_markets=n_markets, seed=seed)


# IMPORTANT: module-level init so connector persists across polling intervals
connector = get_connector()


def poll_once(db: Session) -> int:
    """
    Fetch current market data from the selected connector and write snapshots.
    Returns number of markets ingested.
    """
    markets: List[Dict[str, Any]] = connector.fetch_markets()

    now = datetime.utcnow()
    for m in markets:
        db.add(
            MarketSnapshot(
                platform=str(m["platform"]),
                market_id=str(m["market_id"]),
                title=str(m.get("title") or m["market_id"]),
                ts=now,
                p=float(m["p"]),
                flow=float(m.get("flow", 0.0)),
                depth=float(m.get("depth", 0.0)),
                # Optional fields (only work if your model has these columns)
                bid=_opt_float(m.get("bid")),
                ask=_opt_float(m.get("ask")),
                mid=_opt_float(m.get("mid")),
                volume_24h=_opt_float(m.get("volume_24h")),
                open_interest=_opt_float(m.get("open_interest")),
            )
        )

    db.commit()
    return len(markets)


def get_top(
    db: Session,
    limit: int = 25,
    max_p: float = 0.98,
    min_score: float = 0.0,
    min_hist: int = 3,
):
    """
    Rank markets by odds-aware anomaly score using latest snapshot + rolling baseline.
    """
    markets = db.execute(
        select(MarketSnapshot.platform, MarketSnapshot.market_id).distinct()
    ).all()

    results = []
    for platform, market_id in markets:
        latest = db.execute(
            select(MarketSnapshot)
            .where(
                MarketSnapshot.platform == platform,
                MarketSnapshot.market_id == market_id,
            )
            .order_by(desc(MarketSnapshot.ts))
            .limit(1)
        ).scalar_one_or_none()

        if latest is None:
            continue
        if latest.p is None or latest.flow is None or latest.depth is None:
            continue
        if latest.p > max_p:
            continue

        hist = db.execute(
            select(MarketSnapshot.flow)
            .where(
                MarketSnapshot.platform == platform,
                MarketSnapshot.market_id == market_id,
            )
            .order_by(desc(MarketSnapshot.ts))
            .limit(ROLLING_N)
        ).scalars().all()

        if len(hist) < min_hist:
            continue

        mu = sum(hist) / len(hist)
        var = sum((x - mu) ** 2 for x in hist) / max(len(hist) - 1, 1)
        sigma = var ** 0.5

        bd = compute_score(latest.p, latest.flow, latest.depth, mu, sigma)
        if bd.score < min_score:
            continue

        results.append(
            {
                "platform": latest.platform,
                "market_id": latest.market_id,
                "title": latest.title,
                "ts": latest.ts.isoformat(timespec="seconds"),
                "p": float(latest.p),
                "flow": float(latest.flow),
                "depth": float(latest.depth),
                "z_flow": float(bd.z_flow),
                "depth_ratio": float(bd.depth_ratio),
                "entropy": float(bd.H),
                "score": float(bd.score),
                # Optional fields if present in model
                "bid": _opt_float(getattr(latest, "bid", None)),
                "ask": _opt_float(getattr(latest, "ask", None)),
                "mid": _opt_float(getattr(latest, "mid", None)),
                "volume_24h": _opt_float(getattr(latest, "volume_24h", None)),
                "open_interest": _opt_float(getattr(latest, "open_interest", None)),
            }
        )

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def _opt_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None

