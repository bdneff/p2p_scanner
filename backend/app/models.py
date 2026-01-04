from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from datetime import datetime
from .db import Base

class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String, index=True)
    market_id = Column(String, index=True)
    title = Column(String)
    ts = Column(DateTime, index=True, default=datetime.utcnow)
    p = Column(Float)
    flow = Column(Float)
    depth = Column(Float)

Index("idx_market_time", MarketSnapshot.platform, MarketSnapshot.market_id, MarketSnapshot.ts)
