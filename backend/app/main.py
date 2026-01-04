from .config import POLL_SECONDS
from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from .db import Base, engine, SessionLocal
from .jobs.poller import poll_once, get_top

app = FastAPI(title="P2P Bet Scanner (MVP)")
Base.metadata.create_all(bind=engine)

scheduler = BackgroundScheduler()


def _poll():
    print("poll tick")
    db = SessionLocal()
    try:
        n = poll_once(db)
        print("ingested", n)
    finally:
        db.close()

#def _poll():
#    db = SessionLocal()
#    try:
#        poll_once(db)
#    finally:
#        db.close()

@app.on_event("startup")
def startup():
    _poll()
    scheduler.add_job(_poll, "interval", seconds=POLL_SECONDS, id="poller", replace_existing=True)
    scheduler.start()

@app.get("/top")
def top(limit: int = 25, max_p: float = 0.98):
    db = SessionLocal()
    try:
        return {"results": get_top(db, limit, max_p)}
    finally:
        db.close()
