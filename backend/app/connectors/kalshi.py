# backend/app/connectors/kalshi.py
#
# Politics-only Kalshi connector using:
#   /series  -> find politics-related series tickers
#   /events?series_ticker=...&with_nested_markets=true -> get markets under those series
#
# This avoids paging /markets (which your host returns mostly sports/SGP).
#
# Env knobs (optional):
#   KALSHI_BASE=https://api.elections.kalshi.com/trade-api/v2
#   KALSHI_SERIES_PAGES=25
#   KALSHI_EVENTS_PAGES=10
#   KALSHI_PAGE_SIZE=200
#   KALSHI_LIMIT_MARKETS=50
#   KALSHI_BAND_CENTS=3

import os
import requests
from typing import List, Dict, Any, Optional, Iterable
import time
import random
from requests.exceptions import HTTPError



BASE = os.getenv("KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")
UA = {"User-Agent": "p2p-bet-scanner/0.1"}

SERIES_PAGES = int(os.getenv("KALSHI_SERIES_PAGES", "25"))
EVENTS_PAGES = int(os.getenv("KALSHI_EVENTS_PAGES", "10"))
PAGE_SIZE = int(os.getenv("KALSHI_PAGE_SIZE", "200"))


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


def _get_json(url: str, max_retries: int = 6) -> Dict[str, Any]:
    delay = 0.5
    for attempt in range(max_retries):
        r = requests.get(url, headers=UA, timeout=25)

        # Handle rate limits
        if r.status_code == 429:
            # Kalshi may send Retry-After header (seconds)
            ra = r.headers.get("Retry-After")
            if ra:
                try:
                    sleep_s = float(ra)
                except Exception:
                    sleep_s = delay
            else:
                sleep_s = delay

            # jitter so we don't synchronize with rate-limit window
            sleep_s = sleep_s + random.uniform(0, 0.25)
            time.sleep(sleep_s)
            delay = min(delay * 2, 8.0)
            continue

        r.raise_for_status()
        return r.json()

    raise HTTPError(f"429 Too Many Requests after {max_retries} retries for url={url}")



def _iter_paged(endpoint: str, limit: int, max_pages: int) -> Iterable[Dict[str, Any]]:
    cursor = None
    for _ in range(max_pages):
        url = f"{BASE}{endpoint}"
        sep = "&" if "?" in endpoint else "?"
        url = f"{url}{sep}limit={limit}"
        if cursor:
            url += f"&cursor={cursor}"

        data = _get_json(url)
        yield data

        cursor = data.get("cursor")
        if not cursor:
            break


def _looks_politics_series(s: Dict[str, Any]) -> bool:
    cat = (s.get("category") or "").lower()
    title = (s.get("title") or s.get("name") or s.get("ticker") or "").lower()
    text = (cat + " " + title).strip()

    # broad match; you can tighten later once you see series metadata
    keys = [
        "politic", "election", "president", "presidential", "senate", "house", "congress",
        "governor", "mayor", "ballot", "referendum", "approval", "poll", "impeach",
        "democrat", "republican", "gop",
    ]
    return any(k in text for k in keys)


class KalshiConnector:
    """
    Politics-only ingestion by construction:
      series -> events -> nested markets

    p:
      - mid of yes_bid/yes_ask (cents -> prob)
      - fallback yes_price / last_price
      - if still missing: fetch market detail /markets/{ticker}

    flow:
      - delta of cumulative volume (when available)

    depth:
      - sum YES-side orderbook qty within +/- band_cents around current mid (in cents)
    """

    def __init__(
        self,
        limit_markets: int = int(os.getenv("KALSHI_LIMIT_MARKETS", "50")),
        band_cents: int = int(os.getenv("KALSHI_BAND_CENTS", "3")),
    ):
        self.limit_markets = limit_markets
        self.band_cents = band_cents
        self._prev_volume: dict[str, float] = {}
        self._politics_series = self._discover_politics_series()

    def _discover_politics_series(self) -> List[str]:
        tickers: List[str] = []
        seen = set()

        for page in _iter_paged("/series", limit=PAGE_SIZE, max_pages=SERIES_PAGES):
            series_list = page.get("series") or page.get("data") or []
            for s in series_list:
                if not isinstance(s, dict):
                    continue
                if not _looks_politics_series(s):
                    continue
                t = s.get("ticker") or s.get("series_ticker")
                if not t:
                    continue
                t = str(t)
                if t in seen:
                    continue
                seen.add(t)
                tickers.append(t)

        # keep deterministic order
        tickers.sort()
        return tickers

    def _get_market_detail(self, ticker: str) -> Dict[str, Any]:
        return _get_json(f"{BASE}/markets/{ticker}").get("market", {})

    def _get_orderbook(self, ticker: str) -> Dict[str, Any]:
        data = _get_json(f"{BASE}/markets/{ticker}/orderbook")
        return data.get("orderbook", data)

    def _implied_p(self, m: Dict[str, Any]) -> Optional[float]:
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
        return max(min(p, 0.999), 0.001)

    def _volume(self, m: Dict[str, Any]) -> float:
        return _to_float(_first_present(m, ["volume", "volume_24h", "volume24h", "volume_total"])) or 0.0

    def fetch_markets(self) -> List[Dict[str, Any]]:
        if not self._politics_series:
            # If this happens, your /series endpoint isn't returning politics metadata
            return []

        collected: List[Dict[str, Any]] = []
        seen = set()

        # Walk politics series until we collect enough markets
        for st in self._politics_series:
            if len(collected) >= self.limit_markets:
                break

            # Pull events for this series, with nested markets
            ep = f"/events?series_ticker={st}&with_nested_markets=true"
            for page in _iter_paged(ep, limit=100, max_pages=EVENTS_PAGES):
                ev_list = page.get("events") or page.get("data") or []
                for ev in ev_list:
                    if not isinstance(ev, dict):
                        continue
                    markets = ev.get("markets") or []
                    for m in markets:
                        if not isinstance(m, dict):
                            continue
                        ticker = m.get("ticker") or m.get("market_ticker")
                        if not ticker:
                            continue
                        ticker = str(ticker)
                        if ticker in seen:
                            continue
                        seen.add(ticker)

                        # Title
                        title = str(_first_present(m, ["title", "subtitle", "question"]) or ticker)

                        # p: use nested market fields; if missing, fetch detail once
                        p = self._implied_p(m)
                        detail = None
                        if p is None:
                            detail = self._get_market_detail(ticker)
                            p = self._implied_p(detail)
                        if p is None:
                            continue

                        # Volume/flow
                        vol = self._volume(m)
                        if vol == 0.0 and detail is None:
                            # sometimes nested markets omit volume; try detail
                            detail = self._get_market_detail(ticker)
                            vol = self._volume(detail)

                        prev = self._prev_volume.get(ticker, vol)
                        flow = max(vol - prev, 0.0)
                        self._prev_volume[ticker] = vol

                        # Bid/ask (cents)
                        bid = _to_float(_first_present(m, ["yes_bid", "yes_best_bid", "best_yes_bid"]))
                        ask = _to_float(_first_present(m, ["yes_ask", "yes_best_ask", "best_yes_ask"]))
                        if (bid is None or ask is None) and detail is not None:
                            bid = bid if bid is not None else _to_float(_first_present(detail, ["yes_bid", "yes_best_bid", "best_yes_bid"]))
                            ask = ask if ask is not None else _to_float(_first_present(detail, ["yes_ask", "yes_best_ask", "best_yes_ask"]))

                        mid_cents = p * 100.0

                        # Depth from orderbook (YES side within band)
                        depth = 0.0
                        try:
                            lo = mid_cents - self.band_cents
                            hi = mid_cents + self.band_cents
                            ob = self._get_orderbook(ticker)
                            yes_levels = ob.get("yes") or ob.get("YES") or []
                            for lvl in yes_levels:
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

                        collected.append({
                            "platform": "kalshi",
                            "market_id": ticker,
                            "title": title,
                            # category is blank on your host; set a stable label
                            "category": "politics",
                            "p": float(p),
                            "flow": float(flow),
                            "depth": float(depth),
                            "bid": bid,
                            "ask": ask,
                            "mid": float(mid_cents),
                            "volume_24h": float(vol),
                        })

                        if len(collected) >= self.limit_markets:
                            break
                    if len(collected) >= self.limit_markets:
                        break
                if len(collected) >= self.limit_markets:
                    break

        return collected

