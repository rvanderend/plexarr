"""Just enough of the SABnzbd API for Sonarr and Radarr: they hand PlexArr's NZBs to this "download client"
and follow the queue and history like they do with a real SABnzbd."""
import hmac
import logging
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import auth, config, db
from .downloader import DL

log = logging.getLogger("plexarr.sab")
router = APIRouter()
SAB_VERSION = "4.5.3"


def _err(msg: str):
    return JSONResponse({"status": False, "error": msg})


def _mb(b: int) -> str:
    return f"{b / 1048576:.2f}"


def _timeleft(seconds: float | None) -> str:
    if seconds is None:
        return "0:00:00"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _categories() -> list[str]:
    used = [r["category"] for r in db.q("SELECT DISTINCT category FROM jobs")]
    return list(dict.fromkeys(["*", "radarr", "sonarr", *used]))


def parse_nzb(data: bytes) -> dict:
    root = ET.fromstring(data)
    meta = {}
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "meta":
            meta[el.get("type")] = (el.text or "").strip()
    if "plexarr-server" not in meta or "plexarr-part" not in meta:
        raise ValueError("Not a PlexArr NZB: PlexArr only downloads releases of its own indexer")
    return {"server": meta["plexarr-server"], "part_id": int(meta["plexarr-part"]), "name": meta.get("name", "")}


@router.api_route("/sabnzbd/api", methods=["GET", "POST"])
@router.api_route("/api", methods=["GET", "POST"])
async def api(request: Request):
    p = dict(request.query_params)
    form = None
    if request.method == "POST":
        form = await request.form()
        p.update({k: v for k, v in form.items() if isinstance(v, str)})
    mode = p.get("mode", "")
    if mode == "version":
        return {"version": SAB_VERSION}
    key = p.get("apikey") or ""
    if not key:
        return _err("API Key Required")
    if not hmac.compare_digest(key, auth.api_key()):
        return _err("API Key Incorrect")

    if mode == "get_config":
        return {"config": {
            "misc": {"complete_dir": str(config.DOWNLOAD_DIR), "tv_categories": [], "enable_tv_sorting": False,
                     "movie_categories": [], "enable_movie_sorting": False, "date_categories": [],
                     "enable_date_sorting": False, "pre_check": False, "history_retention": "",
                     "history_retention_option": "all", "history_retention_number": 0},
            "categories": [{"name": c, "order": i, "pp": "3", "script": "None", "dir": "" if c == "*" else c,
                            "newzbin": "", "priority": -100} for i, c in enumerate(_categories())],
            "servers": [], "sorters": []}}
    if mode == "get_cats":
        return {"categories": _categories()}
    if mode == "fullstatus":
        return {"status": {"completedir": str(config.DOWNLOAD_DIR), "paused": DL.paused}}
    if mode in ("addfile", "addlocalfile", "addurl"):
        return await _add(mode, p, form)
    if mode == "queue":
        return _queue(p)
    if mode == "history":
        return _history(p)
    if mode == "retry":
        ok = DL.retry(p.get("value", ""))
        return {"status": ok, "nzo_id": p.get("value", "")}
    if mode in ("pause", "resume"):
        DL.set_paused(mode == "pause")
        return {"status": True}
    if mode == "warnings":
        return {"warnings": []}
    if mode == "server_stats":
        return {"total": 0, "month": 0, "week": 0, "day": 0, "servers": {}}
    return _err(f"not implemented: {mode}")


async def _add(mode: str, p: dict, form):
    try:
        if mode == "addfile":
            upload = (form.get("name") or form.get("nzbfile")) if form else None
            if upload is None or isinstance(upload, str):
                return _err("No NZB file")
            data = await upload.read()
        else:
            url = p.get("value") or p.get("name") or ""
            async with httpx.AsyncClient(timeout=30) as c:
                data = (await c.get(url)).content
        info = parse_nzb(data)
        name = p.get("nzbname") or info["name"]
        nzo = DL.add(info["server"], info["part_id"], name, p.get("cat") or "*")
        return {"status": True, "nzo_ids": [nzo]}
    except (ValueError, LookupError, ET.ParseError, httpx.HTTPError) as e:
        log.warning("add: %s", e)
        return _err(str(e))


def _page(rows, p):
    start = int(p.get("start") or 0) if str(p.get("start") or "0").isdigit() else 0
    limit = int(p.get("limit") or 0) if str(p.get("limit") or "0").isdigit() else 0
    return rows[start:start + limit] if limit else rows[start:]


def _queue(p: dict):
    if p.get("name") == "delete":
        ids = [i for i in p.get("value", "").split(",") if i]
        if ids == ["all"]:
            ids = [r["nzo_id"] for r in db.q("SELECT nzo_id FROM jobs WHERE status IN ('Queued','Downloading','Paused')")]
        for i in ids:
            DL.remove(i, p.get("del_files") == "1")
        return {"status": True, "nzo_ids": ids}
    if p.get("name") in ("pause", "resume"):
        for i in p.get("value", "").split(","):
            DL.pause_job(i, p["name"] == "pause")
        return {"status": True, "nzo_ids": p.get("value", "").split(",")}
    cat = p.get("category") or p.get("cat")
    rows = db.q("SELECT * FROM jobs WHERE status IN ('Queued','Downloading','Paused') ORDER BY created")
    if cat:
        rows = [r for r in rows if r["category"] == cat]
    slots, speed_total, left_total = [], 0.0, 0
    for i, r in enumerate(_page(rows, p)):
        pr = DL.progress(r)
        speed_total += pr["speed"]
        left_total += pr["left"]
        slots.append({"status": r["status"], "index": i, "timeleft": _timeleft(pr["eta"]), "mb": _mb(r["size"]),
                      "mbleft": _mb(pr["left"]), "filename": r["name"], "priority": "Normal", "cat": r["category"],
                      "percentage": str(int(pr["done"] * 100 / r["size"])) if r["size"] else "0",
                      "nzo_id": r["nzo_id"], "size": f"{r['size'] / 1e9:.1f} GB"})
    return {"queue": {"paused": DL.paused, "slots": slots, "noofslots": len(rows), "noofslots_total": len(rows),
                      "speed": f"{speed_total / 1024:.0f} K", "kbpersec": f"{speed_total / 1024:.2f}",
                      "mbleft": _mb(left_total), "status": "Paused" if DL.paused else ("Downloading" if slots else "Idle"),
                      "version": SAB_VERSION, "my_home": str(config.DOWNLOAD_DIR)}}


def _history(p: dict):
    if p.get("name") == "delete":
        value = p.get("value", "")
        if value in ("all", "failed", "completed"):
            st = {"all": ("Completed", "Failed"), "failed": ("Failed",), "completed": ("Completed",)}[value]
            ids = [r["nzo_id"] for r in db.q(f"SELECT nzo_id FROM jobs WHERE removed=0 AND status IN "
                                             f"({','.join('?' * len(st))})", st)]
        else:
            ids = [i for i in value.split(",") if i]
        for i in ids:
            DL.remove(i, p.get("del_files") == "1")
        return {"status": True}
    cat = p.get("category") or p.get("cat")
    rows = db.q("SELECT * FROM jobs WHERE removed=0 AND status IN ('Completed','Failed') ORDER BY finished DESC")
    if cat:
        rows = [r for r in rows if r["category"] == cat]
    slots = [{"fail_message": r["fail_message"] or "", "bytes": r["size"], "category": r["category"],
              "nzb_name": r["name"] + ".nzb", "download_time": int((r["finished"] or 0) - (r["started"] or r["finished"] or 0)),
              "storage": r["storage"] or "", "status": r["status"], "nzo_id": r["nzo_id"], "name": r["name"],
              "completed": int(r["finished"] or 0)} for r in _page(rows, p)]
    return {"history": {"slots": slots, "noofslots": len(rows)}}
