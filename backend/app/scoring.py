import math
from dataclasses import dataclass

EPS = 1e-9

def entropy(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return -(p * math.log(p) + (1 - p) * math.log(1 - p))

def softplus(x: float) -> float:
    if x > 30:
        return x
    return math.log1p(math.exp(x))

@dataclass
class ScoreBreakdown:
    z_flow: float
    depth_ratio: float
    H: float
    score: float

def compute_score(p, flow, depth, mu, sigma):
    z = (flow - mu) / (sigma + EPS)
    r = flow / (depth + EPS)
    H = entropy(p)
    score = softplus(z) * math.log1p(max(r, 0.0)) * H
    return ScoreBreakdown(z, r, H, score)
