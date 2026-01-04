import random
from .base import Connector

class MockConnector(Connector):
    def __init__(self, n_markets=60, seed=7):
        random.seed(seed)
        self.markets = []
        for i in range(n_markets):
            self.markets.append({
                "platform": "mock",
                "market_id": f"m{i}",
                "title": f"Mock Market {i}",
                "p": random.random(),
                "flow": 0.0,
                "depth": random.uniform(200, 8000),
            })

    def fetch_markets(self):
        out = []
        for m in self.markets:
            base = random.uniform(0, 50)
            spike = 0 if random.random() < 0.85 else random.uniform(200, 2500)
            m["flow"] = base + spike
            m["p"] = min(max(m["p"] + random.uniform(-0.015, 0.015), 0.001), 0.999)
            out.append(dict(m))
        return out
