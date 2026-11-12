"""The FastAPI app. Run with: uvicorn app.main:app"""
from fastapi import FastAPI
from fastapi.responses import FileResponse

from app import config
from app.setup_db import setup_database
from app.routers import activity, me, points, rewards

app = FastAPI(title="Shared Workout Tracker")
setup_database()

app.include_router(activity.router)
app.include_router(me.router)
app.include_router(rewards.router)
app.include_router(points.router)


@app.get("/")
def dashboard():
    # The page itself contains no secrets; it asks for your key and sends it
    # with each request to the /api endpoints.
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"ok": True}
