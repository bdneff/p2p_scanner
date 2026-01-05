import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "p2p-bet-scanner/0.1"}

def get_json(url: str) -> dict:
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.json()

def iter_paged(endpoint: str, limit: int = 200, max_pages: int = 25):
    cursor = None
    for _ in range(max_pages):
        url = f"{BASE}{endpoint}"
        sep = "&" if "?" in endpoint else "?"
        url += f"{sep}limit={limit}"
        if cursor:
            url += f"&cursor={cursor}"
        data = get_json(url)
        yield data
        cursor = data.get("cursor")
        if not cursor:
            break

def looks_politics_series(s: dict) -> bool:
    # category may exist here even if market.category is blank
    cat = (s.get("category") or "").lower()
    title = (s.get("title") or s.get("name") or s.get("ticker") or "").lower()

    # broad match; tighten after you see real series metadata
    return any(k in (cat + " " + title) for k in [
        "politic", "election", "president", "senate", "house", "congress",
        "governor", "campaign", "approval", "ballot"
    ])

def main():
    # 1) find candidate series tickers for politics
    politics_series = []
    for page in iter_paged("/series", limit=200, max_pages=25):
        for s in page.get("series", []) or page.get("data", []) or []:
            if looks_politics_series(s):
                ticker = s.get("ticker") or s.get("series_ticker")
                if ticker:
                    politics_series.append(str(ticker))
        if len(politics_series) >= 10:
            break

    print(f"Found {len(politics_series)} candidate politics series tickers (showing up to 10):")
    for t in politics_series[:10]:
        print("  ", t)

    if not politics_series:
        print("\nNo politics-looking series found via /series. This likely means:")
        print("  - the series payload uses different fields (need to inspect raw keys), or")
        print("  - the host/environment you're hitting doesn't currently list politics series.")
        return

    # 2) pull events for those series and print markets
    printed = 0
    seen = set()

    for st in politics_series[:10]:
        # docs: /events supports filtering; series_ticker is a common filter in Kalshi APIs
        # If this returns empty, we’ll inspect and adjust.
        for page in iter_paged(f"/events?series_ticker={st}&with_nested_markets=true", limit=100, max_pages=10):
            for ev in page.get("events", []) or page.get("data", []) or []:
                markets = ev.get("markets") or []
                for m in markets:
                    ticker = m.get("ticker") or m.get("market_ticker")
                    title = m.get("title") or m.get("subtitle") or m.get("question") or ""
                    if not ticker or ticker in seen:
                        continue
                    seen.add(ticker)
                    print(f"{ticker}\t{title}")
                    printed += 1
                    if printed >= 10:
                        return

    print("\nPrinted fewer than 10 markets. Either:")
    print("  - series_ticker filter isn't correct for this endpoint, or")
    print("  - with_nested_markets isn’t returning markets for these events.")
    print("Next step: inspect one /series object and one /events object keys.")

if __name__ == "__main__":
    main()

