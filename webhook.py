"""Single-instance durable inbox. No automatic replay of uncertain external writes."""
import asyncio
import hashlib
import hmac
import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from threading import Event, Thread
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from review import run_review


@contextmanager
def database():
    db = sqlite3.connect(os.getenv("REVIEW_DB", "reviews.sqlite"), timeout=8)
    try:
        with db:
            yield db
    finally:
        db.close()


def process_one():
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id,job FROM inbox WHERE state='Queued' ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return False
        db.execute("UPDATE inbox SET state='Processing' WHERE id=?", (row[0],))
    job = json.loads(row[1])
    if job["received_live"] != (os.getenv("LIVE_MODE") == "true"):
        result = {**job, "action":"Stopped: mode changed after delivery"}
    else:
        result = run_review(job)
    with database() as db:
        db.execute("UPDATE inbox SET state='Finished',result=? WHERE id=?", (json.dumps(result), row[0]))
    return True


def worker(stop):
    while not stop.is_set():
        if not process_one():
            stop.wait(0.3)


@asynccontextmanager
async def lifespan(app):
    with database() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS inbox
          (id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, state TEXT, job TEXT, result TEXT)""")
        db.execute("UPDATE inbox SET state='Interrupted: inspect GitHub before a new event' WHERE state='Processing'")
    stop = Event()
    thread = Thread(target=worker, args=(stop,), daemon=True)
    thread.start()
    yield
    stop.set()
    await asyncio.to_thread(thread.join, 2)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/health")
def health():
    return {"status":"ok"}


@app.get("/reviews")
def reviews(request: Request):
    key = os.getenv("RESULTS_KEY")
    if not key or not hmac.compare_digest(request.headers.get("authorization","").encode(),
                                         ("Bearer "+key).encode()):
        raise HTTPException(403, "Read access denied")
    with database() as db:
        rows = db.execute("SELECT id,state,job,result FROM inbox ORDER BY rowid DESC LIMIT 30").fetchall()
    return [{"delivery":i, "state":s, **json.loads(result or job)} for i,s,job,result in rows]


@app.post("/webhook")
async def webhook(request: Request):
    key = os.getenv("WEBHOOK_SECRET")
    if not key:
        raise HTTPException(503, "Configure webhook secret")
    body = bytearray()
    async for chunk in request.stream():
        if len(body)+len(chunk)>2_000_000:
            raise HTTPException(413, "Event too large")
        body.extend(chunk)
    signature = "sha256="+hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode(), request.headers.get("x-hub-signature-256","").encode()):
        raise HTTPException(401, "Invalid signature")
    if request.headers.get("x-github-event") != "pull_request":
        return {"state":"Ignored"}
    try:
        event = json.loads(body)
        if event["action"] not in {"opened","reopened","synchronize","edited","ready_for_review"}:
            return {"state":"Ignored"}
        delivery = str(UUID(request.headers["x-github-delivery"]))
        p = event["pull_request"]
        job = {"repo":event["repository"]["full_name"],"number":event["number"],"title":p["title"],
               "branch":p["head"]["ref"],"sha":p["head"]["sha"],"base":p["base"]["ref"],
               "base_sha":p["base"]["sha"],"received_live":os.getenv("LIVE_MODE")=="true"}
        if (type(job["number"]) is not int or job["number"] < 1 or
            not all(isinstance(job[k],str) for k in ("title","branch","base","repo")) or
            not all(re.fullmatch("[0-9a-f]{40}",job[k]) for k in ("sha","base_sha"))):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "Malformed pull request event") from None
    if job["repo"].lower()!=os.getenv("TEST_REPO","").lower():
        raise HTTPException(403, "Outside configured test repository")
    with database() as db:
        inserted = db.execute("INSERT OR IGNORE INTO inbox VALUES(?,?,'Queued',?,NULL)",
                              (delivery, hashlib.sha256(body).hexdigest(), json.dumps(job))).rowcount
    return JSONResponse({"state":"Queued" if inserted else "Duplicate","delivery":delivery},status_code=202)
