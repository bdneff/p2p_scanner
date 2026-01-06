# P2P Market Scanner

A lightweight scanner for detecting unusual activity and potential order flow in peer-to-peer betting markets. The scanner continuously polls market data, stores observations in a local SQLite database, and surfaces bets that are statistically unusual relative to recent history.

The goal is **flow detection**, not just identifying large absolute bets. Large bets only matter when they are unusual in context.

---

## Overview

The scanner works in three stages:

1. **Polling**
   - Periodically queries the P2P market API
   - Fetches recent bets and market metadata

2. **Storage**
   - Persists observations to a local SQLite database (`scanner.db`)
   - Enables time-based comparisons across polling windows

3. **Detection**
   - Scores bets based on how unusual they are relative to recent activity
   - Filters and ranks candidates using configurable thresholds

---

## Core Concepts

### What This Scanner Is (and Is Not)

**This is:**
- A rolling anomaly detector
- Designed to capture emerging flow
- Focused on relative changes over time

**This is not:**
- A simple “largest bets” leaderboard
- A one-shot snapshot of market size
- A predictive model by itself

---

## Detection Logic (High Level)

Each bet is evaluated against recent market history using metrics such as:

- Bet size relative to recent average
- Directional imbalance (buy vs sell pressure)
- Time clustering (sudden bursts of activity)

Only bets exceeding minimum thresholds are surfaced.

Typical filters include:
- Minimum bet size
- Minimum directional ratio
- Minimum anomaly score

---

## Configuration

Key parameters are defined in the scanner configuration:

```python
POLL_SECONDS = 60      # How often the market is polled
Z_MIN = 0.0            # Minimum z-score threshold
DR_MIN = 0.0           # Minimum directional ratio
H_MIN = 0.0            # Minimum heuristic score

If you change polling frequency or detection logic significantly, you should reset the database:
rm backend/scanner.db


uvicorn backend.main:app --reload
or
python backend/scanner.py

Example queries:

SELECT COUNT(*) FROM bets;
SELECT * FROM anomalies ORDER BY timestamp DESC LIMIT 10;
