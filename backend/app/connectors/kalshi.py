# backend/app/connectors/kalshi.py
import requests
from typing import List, Dict, Any, Optional

BASE = "https://api.elections.kalshi.com/trade-api/v2"

def _first_present(d: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None

def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None

class KalshiConnector:
    """
    Unauthenticated markets + orderbook.

    p:
      - prefer mid of yes_bid/yes_ask (cents -> prob)
      - fallback to yes_price or last_price fields if present

    flow:
      - delta of cumulative volume field if available
    depth:
      - sum YES-side orderbook qty within +/- band_cents around current yes mid
    """

    def __init__(self, limit_markets: int = 50, band_cents: int = 3):
        self.limit_markets = limit_markets
        self.band_cents = band_cents
        self._prev_volume: dict[str, float] = {}

    def _get_markets(self) -> List[Dict[str, Any]]:
        url = f"{BASE}/markets?status=open&limit={self.limit_markets}"
        r = requests.get(url, timeout=10, headers={"User-Agent": "p2p-bet-scanner/0.1"})
        r.raise_for_status()
        data = r.json()
        if "markets" not in data:
            raise RuntimeError(f"Unexpected response keys={list(data.keys())} body={str(data)[:400]}")
        return data["markets"]

    def _get_orderbook(self, ticker: str) -> Dict[str, Any]:
        url = f"{BASE}/markets/{ticker}/orderbook"
        r = requests.get(url, timeout=10, headers={"User-Agent": "p2p-bet-scanner/0.1"})
        r.raise_for_status()
        data = r.json()
        return data.get("orderbook", data)  # tolerate shape differences

    def _implied_p(self, m: Dict[str, Any]) -> Optional[float]:
        # Try common Kalshi fields (in cents)
        yes_bid = _to_float(_first_present(m, ["yes_bid", "yes_best_bid", "best_yes_bid"]))
        yes_ask = _to_float(_first_present(m, ["yes_ask", "yes_best_ask", "best_yes_ask"]))
        yes_price = _to_float(_first_present(m, ["yes_price", "yes_mid", "mid_yes"]))
        last_price = _to_float(_first_present(m, ["last_price", "last_trade_price"]))

        mid_cents: Optional[float] = None
        if yes_bid is not None and yes_ask is not None and yes_bid > 0 and yes_ask > 0:
            mid_cents = 0.5 * (yes_bid + yes_ask)
        elif yes_price is not None:
            mid_cents = yes_price
        elif last_price is not None:
            mid_cents = last_price

        if mid_cents is None:
            return None

        p = mid_cents / 100.0
        if p <= 0.0 or p >= 1.0:
            # keep it bounded; Kalshi quotes should be 1..99 but be defensive
            p = max(min(p, 0.999), 0.001)
        return p

    def fetch_markets(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        markets = self._get_markets()

        for m in markets:
            ticker = _first_present(m, ["ticker", "market_ticker"])
            if ticker is None:
                continue
            ticker = str(ticker)

            title = _first_present(m, ["title", "subtitle", "question"]) or ticker
            title = str(title)

            p = self._implied_p(m)
            if p is None:
                continue

            # Volume field naming varies; try a few
            vol = _to_float(_first_present(m, ["volume", "volume_24h", "volume24h", "volume_total"])) or 0.0
            prev = self._prev_volume.get(ticker, vol)
            flow = max(vol - prev, 0.0)
            self._prev_volume[ticker] = vol

            # Depth: sum YES book within +/- band around current mid price in cents
            depth = 0.0
            try:
                # reconstruct mid_cents used for banding
                mid_cents = p * 100.0
                lo = mid_cents - self.band_cents
                hi = mid_cents + self.band_cents

                ob = self._get_orderbook(ticker)
                yes_levels = ob.get("yes") or ob.get("YES") or []
                for lvl in yes_levels:
                    # tolerate both [price, qty] and {"price":..,"quantity":..}
                    if isinstance(lvl, list) and len(lvl) >= 2:
                        px, qty = _to_float(lvl[0]), _to_float(lvl[1])
                    elif isinstance(lvl, dict):
                        px = _to_float(_first_present(lvl, ["price", "px"]))
                        qty = _to_float(_first_present(lvl, ["quantity", "qty", "size"]))
                    else:
                        continue

                    if px is None or qty is None:
                        continue
                    if lo <= px <= hi:
                        depth += qty
            except Exception:
                depth = 0.0

            out.append({
                "platform": "kalshi",
                "market_id": ticker,
                "title": title,
                "p": float(p),
                "flow": float(flow),
                "depth": float(depth),
                "volume_24h": float(vol),
            })

        return out

