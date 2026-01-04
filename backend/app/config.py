import os

CONNECTOR = os.getenv("CONNECTOR", "mock")  # "mock" or "polymarket" etc.
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "10"))
MAX_P_DEFAULT = float(os.getenv("MAX_P_DEFAULT", "0.98"))

