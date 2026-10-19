"""SAN Zoning Manager: read-only pull, edit in the browser, generate commands. Never pushes config."""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import demo
from .model import build_model, detect_platform
from .plan import make_plan
from .parsers.live import build_live
from .ssh import PullError, live, pull

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="SAN Zoning Manager", docs_url=None, redoc_url=None)


class PullReq(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = 22
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    platform: str = "auto"
    fid: int | None = None
    vsan: int | None = None


class ParseReq(BaseModel):
    platform: str = "auto"
    texts: dict[str, str]
    vsan: int | None = None
    host: str = ""


class PlanReq(BaseModel):
    original: dict
    working: dict
    comments: bool = True


@app.get("/api/health")
def health():
    return {"ok": True, "push": False}


@app.post("/api/pull")
def api_pull(r: PullReq):
    if r.platform not in ("auto", "fos", "nxos"):
        raise HTTPException(400, "Platform must be auto, fos or nxos.")
    try:
        res = pull(r.host.strip(), r.username, r.password, r.port, r.platform, r.fid)
    except PullError as e:
        raise HTTPException(502, str(e)) from e
    model = build_model(res["platform"], res["outputs"], r.vsan, r.host.strip())
    if not model["zones"] and not model["aliases"]:
        model["notes"].insert(0, "No zoning was found in the switch output. Check the account has zoning read access.")
    return {"model": model, "raw": res["outputs"], "log": res["log"], "fingerprint": res["fingerprint"]}


@app.post("/api/parse")
def api_parse(r: ParseReq):
    platform = r.platform
    if platform == "auto":
        platform = detect_platform("\n".join(r.texts.values())) or ""
    if platform not in ("fos", "nxos"):
        raise HTTPException(400, "Could not recognise FOS or NX-OS zoning in that text.")
    model = build_model(platform, r.texts, r.vsan, r.host)
    if not model["zones"] and not model["aliases"]:
        raise HTTPException(400, "No aliases or zones found in that text.")
    return {"model": model, "raw": r.texts}


@app.post("/api/plan")
def api_plan(r: PlanReq):
    return make_plan(r.original, r.working, r.comments)


class LiveReq(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = 22
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    platform: str
    fid: int | None = None


@app.post("/api/live")
def api_live(r: LiveReq):
    try:
        res = live(r.host.strip(), r.username, r.password, r.platform, r.port, r.fid)
    except PullError as e:
        raise HTTPException(502, str(e)) from e
    out = build_live(r.platform, res["outputs"])
    out.update(at=int(time.time() * 1000), ms=res["ms"])
    return out


@app.get("/api/demo-live/{platform}")
def api_demo_live(platform: str):
    gen = {"fos": demo.fos_live, "nxos": demo.nxos_live}.get(platform)
    if gen is None:
        raise HTTPException(404, "Unknown demo.")
    out = build_live(platform, gen())
    out.update(at=int(time.time() * 1000), ms=0)
    return out


@app.get("/api/demo/{platform}")
def api_demo(platform: str, vsan: int | None = None):
    texts = {"fos": demo.FOS, "nxos": demo.NXOS}.get(platform)
    if texts is None:
        raise HTTPException(404, "Unknown demo.")
    model = build_model(platform, texts, vsan, "192.0.2.10" if platform == "fos" else "192.0.2.20")
    model["demo"] = True
    return {"model": model, "raw": texts, "log": [{"cmd": "(demo data)", "bytes": 0, "ms": 0}], "fingerprint": ""}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


if os.environ.get("ZM_DISABLE_PULL") == "1":  # optional offline-only mode
    app.router.routes = [rt for rt in app.router.routes if getattr(rt, "path", "") not in ("/api/pull", "/api/live")]
