"""PlexArr: download from friends' Plex servers through Sonarr and Radarr, as a Newznab indexer plus a
SABnzbd-like download client."""
import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, config, db, newznab, plex, releases, sab, subsarr, sweep
from .arr import RADARR, SONARR, ArrError
from .downloader import DL
from .plex import LIB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("plexarr")


class _HideKeys(logging.Filter):
    """Sonarr/Radarr send the API key in the query string, Bazarr in the path: keep it out of the access log."""
    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(re.sub(r"(apikey=|/subsarr/)[^&/\s]+", r"\1…", a) if isinstance(a, str) else a
                                for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_HideKeys())
STATIC = Path(__file__).parent / "static"
RES_ORDER = {"480p": 1, "576p": 2, "720p": 3, "1080p": 4, "2160p": 5}


async def _refresh_loop():
    while True:
        if db.get_settings()["servers"]:
            await LIB.refresh()
            _lib_cache.clear()
        await asyncio.sleep(max(5, db.get_settings()["refresh_minutes"]) * 60)


@asynccontextmanager
async def lifespan(_app):
    db.conn()
    auth.api_key()
    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tasks = [asyncio.create_task(_refresh_loop()), asyncio.create_task(DL.run()), asyncio.create_task(sweep.loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="PlexArr", version=config.VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(newznab.router)
app.include_router(sab.router)
app.include_router(subsarr.router)

PUBLIC_API = ("/api/auth/", "/api/health")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    # /api itself is the SABnzbd API (with its own key); /api/... is the web UI.
    if path.startswith("/api/") and not path.startswith(PUBLIC_API) and auth.enabled():
        key = request.headers.get("x-api-key") or request.query_params.get("apikey")
        if not auth.check_cookie(request.cookies.get(auth.COOKIE)) and not auth.check_api_key(key):
            return JSONResponse({"detail": "login"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def no_stale_assets(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/") and request.url.path.endswith((".js", ".css", ".html")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return {"name": "PlexArr", "short_name": "PlexArr", "start_url": "/", "display": "standalone",
            "background_color": "#0e1016", "theme_color": "#0e1016",
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}]}


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"ok": True, "version": config.VERSION}


# --- login -------------------------------------------------------------------------------------------
class LoginReq(BaseModel):
    user: str
    password: str


@app.get("/api/auth/status")
async def auth_status(request: Request):
    user = auth.check_cookie(request.cookies.get(auth.COOKIE))
    return {"enabled": auth.enabled(), "loggedIn": bool(user) or not auth.enabled(), "user": user}


@app.post("/api/auth/login")
async def auth_login(req: LoginReq, request: Request):
    s = db.get_settings()
    stored = db.get_state("auth_hash") or ""
    if not (req.user.strip() == s["auth_user"] and auth.check_password(req.password, stored)):
        await asyncio.sleep(1.5)
        raise HTTPException(401, "wrong")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, auth.make_cookie(s["auth_user"]), max_age=auth.MAX_AGE, httponly=True,
                    samesite="lax", secure=request.url.scheme == "https")
    return resp


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


class AuthSetReq(BaseModel):
    enabled: bool
    user: str = ""
    password: str = ""


@app.put("/api/auth")
async def auth_set(req: AuthSetReq, request: Request):
    if req.enabled:
        if not req.user.strip():
            raise HTTPException(400, "user")
        if req.password:
            if len(req.password) < 8:
                raise HTTPException(400, "short")
            auth.set_credentials(req.user, req.password)
        elif not db.get_state("auth_hash"):
            raise HTTPException(400, "password")
        else:
            db.set_settings({"auth_user": req.user.strip()})
    db.set_settings({"auth_enabled": req.enabled})
    resp = JSONResponse({"ok": True, "enabled": auth.enabled()})
    if auth.enabled():
        resp.set_cookie(auth.COOKIE, auth.make_cookie(db.get_settings()["auth_user"]), max_age=auth.MAX_AGE,
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return resp


# --- library ---------------------------------------------------------------------------------------------
_lib_cache: dict[str, tuple[float, dict]] = {}


def _res_of_quality(q: dict | None) -> str:
    r = ((q or {}).get("quality") or {}).get("resolution") or 0
    return f"{r}p" if r else ""


def _best(rows):
    return max(rows, key=lambda r: (RES_ORDER.get(releases.resolution(r), 0), r["size"] or 0))


async def _movies_view() -> list[dict]:
    groups = defaultdict(list)
    for r in db.q("SELECT * FROM releases WHERE kind='movie'"):
        groups[(r["server"], r["rating_key"])].append(r)
    radarr = {}
    if RADARR.configured:
        try:
            radarr = await RADARR.movies()
        except ArrError as e:
            log.warning("library: %s", e)
    by_imdb = {m.get("imdbId"): m for m in radarr.values() if m.get("imdbId")}
    out = []
    for (server, rk), rows in groups.items():
        b = _best(rows)
        m = radarr.get(b["tmdb"]) or by_imdb.get(b["imdb"])
        mine = {"state": "none"}
        if m:
            mine = {"state": "have" if m.get("hasFile") else "missing",
                    "res": _res_of_quality((m.get("movieFile") or {}).get("quality")), "id": m["id"]}
        res = releases.resolution(b)
        out.append({"kind": "movie", "server": server, "key": rk, "title": b["title"], "year": b["year"],
                    "added": max(r["added_at"] or 0 for r in rows), "size": b["size"], "res": res,
                    "versions": len(rows), "mine": mine,
                    "better": mine["state"] == "have" and RES_ORDER.get(res, 0) > RES_ORDER.get(mine.get("res"), 0)})
    return out


async def _shows_view() -> list[dict]:
    groups = defaultdict(list)
    for r in db.q("SELECT server, show_key, title, year, tvdb, season, episode, size, resolution, added_at "
                  "FROM releases WHERE kind='episode'"):
        groups[(r["server"], r["show_key"])].append(r)
    sonarr = {}
    if SONARR.configured:
        try:
            sonarr = await SONARR.series()
        except ArrError as e:
            log.warning("library: %s", e)
    sem = asyncio.Semaphore(6)

    async def view(server, sk, rows):
        b = rows[0]
        s = sonarr.get(b["tvdb"])
        res_count = defaultdict(int)
        for r in rows:
            res_count[releases.resolution(r)] += 1
        mine, lacking = {"state": "none"}, None
        if s:
            async with sem:
                try:
                    eps = await SONARR.episodes(s["id"])
                except ArrError:
                    eps = {}
            lacking = sum(1 for r in rows if not (eps.get((r["season"], r["episode"])) or {}).get("hasFile"))
            st = s.get("statistics") or {}
            mine = {"state": "have" if lacking == 0 else "partial" if st.get("episodeFileCount") else "missing",
                    "have": st.get("episodeFileCount", 0), "id": s["id"]}
        return {"kind": "show", "server": server, "key": sk, "title": b["title"], "year": b["year"],
                "added": max(r["added_at"] or 0 for r in rows), "size": sum(r["size"] or 0 for r in rows),
                "res": max(res_count, key=res_count.get), "episodes": len(rows),
                "seasons": len({r["season"] for r in rows}), "mine": mine, "lacking": lacking}

    return list(await asyncio.gather(*(view(s, k, rows) for (s, k), rows in groups.items())))


@app.get("/api/library")
async def library():
    s = db.get_settings()
    hit = _lib_cache.get("lib")
    if hit and time.time() - hit[0] < 300:
        data = hit[1]
    else:
        data = {"items": await _movies_view() + await _shows_view()}
        _lib_cache["lib"] = (time.time(), data)
    return {**data, "configured": bool(s["plex_token"] and s["servers"]), "refreshing": LIB.refreshing,
            "indexedAt": db.get_state("indexed_at"), "servers": _server_status()}


def _server_status() -> list[dict]:
    names = db.get_state("server_names", {})
    out = []
    for sid in db.get_settings()["servers"]:
        st = LIB.status.get(sid, {})
        n = db.q1("SELECT COUNT(*) AS n FROM releases WHERE server=?", (sid,))["n"]
        out.append({"id": sid, "name": st.get("name") or names.get(sid, sid), "owner": st.get("owner", ""),
                    "error": st.get("error", ""), "at": st.get("at"), "files": n})
    return out


@app.post("/api/library/refresh")
async def library_refresh():
    async def run():
        await LIB.refresh()
        _lib_cache.clear()
    asyncio.create_task(run())
    RADARR.forget()
    SONARR.forget()
    return {"ok": True}


_meta_cache: dict[tuple, dict] = {}


async def _plex_meta(server: str, rk: str) -> dict:
    if (server, rk) not in _meta_cache:
        try:
            srv = await LIB.server(server)
            md = (await srv.get(f"/library/metadata/{rk}", timeout=15)).get("Metadata", [{}])[0]
            _meta_cache[(server, rk)] = {"summary": md.get("summary", ""), "art": bool(md.get("art")),
                                         "rating": md.get("audienceRating") or md.get("rating"),
                                         "genres": [g["tag"] for g in md.get("Genre", [])][:4],
                                         "duration": md.get("duration")}
        except (ConnectionError, httpx.HTTPError):
            return {}
    return _meta_cache[(server, rk)]


def _job_of(server: str, part_id: int):
    j = db.q1("SELECT nzo_id, status, done, size FROM jobs WHERE server=? AND part_id=? ORDER BY created DESC LIMIT 1",
              (server, part_id))
    if not j:
        return None
    pr = DL.progress(j)
    return {"status": j["status"], "pct": round(pr["done"] * 100 / j["size"]) if j["size"] else 0}


@app.get("/api/item/movie/{server}/{rk}")
async def movie_detail(server: str, rk: str):
    rows = db.q("SELECT * FROM releases WHERE server=? AND rating_key=? AND kind='movie'", (server, rk))
    if not rows:
        raise HTTPException(404, "not found")
    b = _best(rows)
    versions = []
    for r in sorted(rows, key=lambda r: -(r["size"] or 0)):
        t, synthetic = await releases.title(r)
        versions.append({"partId": r["part_id"], "file": (r["file"] or "").rsplit("/", 1)[-1], "size": r["size"],
                         "res": releases.resolution(r), "vcodec": r["vcodec"], "acodec": r["acodec"],
                         "channels": r["channels"], "bitrate": r["bitrate"], "container": r["container"],
                         "release": t, "synthetic": synthetic, "job": _job_of(server, r["part_id"])})
    ok, why = await releases.eligible(b)
    mine = None
    if RADARR.configured:
        m = await RADARR.movie(b["tmdb"], b["imdb"])
        if m:
            mf = m.get("movieFile") or {}
            mine = {"id": m["id"], "hasFile": m.get("hasFile"), "monitored": m.get("monitored"),
                    "quality": ((mf.get("quality") or {}).get("quality") or {}).get("name"), "size": mf.get("size"),
                    "file": (mf.get("relativePath") or "")}
    return {"kind": "movie", "server": server, "key": rk, "title": b["title"], "year": b["year"],
            "ids": {"tmdb": b["tmdb"], "imdb": b["imdb"]}, "versions": versions, "mine": mine,
            "eligible": {"ok": ok, "why": why}, "meta": await _plex_meta(server, rk),
            "requested": releases.requested(releases.request_key(b))}


@app.get("/api/item/show/{server}/{sk}")
async def show_detail(server: str, sk: str):
    rows = db.q("SELECT * FROM releases WHERE server=? AND show_key=? AND kind='episode' ORDER BY season, episode",
                (server, sk))
    if not rows:
        raise HTTPException(404, "not found")
    b = rows[0]
    series, eps = None, {}
    if SONARR.configured:
        series = (await SONARR.series()).get(b["tvdb"])
        if series:
            eps = await SONARR.episodes(series["id"])
    seasons = defaultdict(list)
    for r in rows:
        e = eps.get((r["season"], r["episode"])) or {}
        ok, why = await releases.eligible(r)
        ef = e.get("episodeFile") or {}
        seasons[r["season"]].append({
            "season": r["season"], "episode": r["episode"], "title": r["episode_title"], "size": r["size"],
            "res": releases.resolution(r), "partId": r["part_id"], "file": (r["file"] or "").rsplit("/", 1)[-1],
            "mine": {"hasFile": e.get("hasFile", False), "monitored": e.get("monitored"),
                     "quality": ((ef.get("quality") or {}).get("quality") or {}).get("name")} if e else None,
            "eligible": {"ok": ok, "why": why}, "job": _job_of(server, r["part_id"])})
    return {"kind": "show", "server": server, "key": sk, "title": b["title"], "year": b["year"],
            "ids": {"tvdb": b["tvdb"], "imdb": b["imdb"]}, "meta": await _plex_meta(server, sk),
            "mine": {"id": series["id"], "title": series["title"], "monitored": series.get("monitored")} if series else None,
            "seasons": [{"season": k, "episodes": v} for k, v in sorted(seasons.items())]}


@app.get("/api/poster/{server}/{rk}")
async def poster(server: str, rk: str, w: int = 300):
    w = 600 if w > 300 else 300
    cache = config.DATA_DIR / "posters" / f"{server}_{rk}_{w}.jpg"
    if not cache.exists():
        r = db.q1("SELECT thumb FROM releases WHERE server=? AND (rating_key=? OR show_key=?) AND thumb IS NOT NULL LIMIT 1",
                  (server, rk, rk))
        if not r:
            raise HTTPException(404, "not found")
        try:
            srv = await LIB.server(server)
            await srv.connect()
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(srv.url("/photo/:/transcode"), headers=plex.headers(srv.token),
                                   params={"width": w, "height": w * 3 // 2, "minSize": 1, "upscale": 1, "url": r["thumb"]})
            if resp.status_code != 200 or not resp.content:
                raise HTTPException(404, "not found")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(resp.content)
        except (ConnectionError, httpx.HTTPError):
            raise HTTPException(502, "plex")
    return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


# --- fetching through Sonarr/Radarr ----------------------------------------------------------------------
class FetchReq(BaseModel):
    kind: str                  # movie | show
    server: str
    key: str
    season: int | None = None
    episode: int | None = None


def _request(key: str, hours: int = 24):
    db.x("INSERT INTO requests(key, until) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET until=excluded.until",
         (key, time.time() + hours * 3600))


@app.post("/api/fetch")
async def fetch(req: FetchReq):
    """Lets PlexArr offer the item for a day and asks Sonarr/Radarr to search it now."""
    if req.kind == "movie":
        rows = db.q("SELECT * FROM releases WHERE server=? AND rating_key=? AND kind='movie'", (req.server, req.key))
        if not rows:
            raise HTTPException(404, "not found")
        if not RADARR.configured:
            raise HTTPException(400, "no_radarr")
        m = await RADARR.movie(rows[0]["tmdb"], rows[0]["imdb"])
        if not m:
            raise HTTPException(400, "not_in_radarr")
        _request(releases.request_key(rows[0]))
        await RADARR.command("MoviesSearch", movieIds=[m["id"]])
        RADARR.forget("failed:")
        what = f"{m['title']} ({m.get('year')})"
        db.event("request", f"{what}: search in Radarr", app="Radarr", what=what)
        return {"ok": True, "count": 1}
    rows = db.q("SELECT * FROM releases WHERE server=? AND show_key=? AND kind='episode'", (req.server, req.key))
    if not rows:
        raise HTTPException(404, "not found")
    if not SONARR.configured:
        raise HTTPException(400, "no_sonarr")
    series = (await SONARR.series()).get(rows[0]["tvdb"])
    if not series:
        raise HTTPException(400, "not_in_sonarr")
    eps = await SONARR.episodes(series["id"])
    pick = []
    for r in rows:
        if req.season is not None and r["season"] != req.season:
            continue
        if req.episode is not None and r["episode"] != req.episode:
            continue
        e = eps.get((r["season"], r["episode"]))
        if e and (not e.get("hasFile") or req.episode is not None):
            pick.append((r, e))
    if not pick:
        raise HTTPException(400, "nothing")
    for r, _ in pick:
        _request(releases.request_key(r))
    by_season = defaultdict(list)
    for r, e in pick:
        by_season[r["season"]].append(e["id"])
    for season, ids in by_season.items():
        if len(ids) >= 3:
            await SONARR.command("SeasonSearch", seriesId=series["id"], seasonNumber=season)
        else:
            await SONARR.command("EpisodeSearch", episodeIds=ids)
    SONARR.forget(f"episodes:{series['id']}")
    what = series["title"] + (f" S{req.season:02d}" if req.season is not None else "") + \
        (f"E{req.episode:02d}" if req.episode is not None else "")
    db.event("request", f"{what}: {len(pick)} episode(s), search in Sonarr", app="Sonarr", what=what, n=len(pick))
    return {"ok": True, "count": len(pick)}


# --- downloads ---------------------------------------------------------------------------------------------
def _sources() -> dict:
    """Per Plex server: its name and the friend who shares it (also for servers no longer in use, when known)."""
    out = {sid: {"name": name, "owner": ""} for sid, name in db.get_state("server_names", {}).items()}
    for sid, srv in LIB.servers.items():
        out[sid] = {"name": srv.name, "owner": srv.owner}
    for sid, st in LIB.status.items():
        if st.get("name"):
            out[sid] = {"name": st["name"], "owner": st.get("owner") or out.get(sid, {}).get("owner", "")}
    return out


def _job_view(j, sources: dict | None = None) -> dict:
    pr = DL.progress(j)
    return {"id": j["nzo_id"], "source": (sources or {}).get(j["server"]), "name": j["name"], "label": j["label"], "sublabel": j["sublabel"], "kind": j["kind"],
            "server": j["server"], "key": j["rating_key"] if j["kind"] == "movie" else j["show_key"],
            "category": j["category"], "status": j["status"], "size": j["size"], "done": pr["done"],
            "speed": pr["speed"], "eta": pr["eta"], "message": j["fail_message"], "created": j["created"],
            "started": j["started"], "finished": j["finished"], "removed": bool(j["removed"]),
            "attempts": j["attempts"], "subs": json.loads(j["subs"]) if j["subs"] else []}


@app.get("/api/downloads")
async def downloads():
    active = db.q("SELECT * FROM jobs WHERE status IN ('Queued','Downloading','Paused') ORDER BY created")
    done = db.q("SELECT * FROM jobs WHERE status IN ('Completed','Failed','Deleted') ORDER BY finished DESC LIMIT 200")
    total = db.q1("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM jobs WHERE status='Completed'")
    subs = db.q("SELECT * FROM subs ORDER BY at DESC LIMIT 200")
    src = _sources()
    return {"paused": DL.paused, "active": [_job_view(j, src) for j in active], "done": [_job_view(j, src) for j in done],
            "subs": [{"id": s["id"], "at": s["at"], "source": src.get(s["server"]), "kind": s["kind"], "label": s["label"], "sublabel": s["sublabel"],
                      "lang": s["lang"], "hi": bool(s["hi"]), "ext": s["ext"], "size": s["size"]} for s in subs],
            "totals": {"count": total["n"], "bytes": total["b"]}}


@app.post("/api/downloads/{nzo}/{action}")
async def download_action(nzo: str, action: str):
    if action == "pause":
        DL.pause_job(nzo, True)
    elif action == "resume":
        DL.pause_job(nzo, False)
    elif action == "retry":
        DL.retry(nzo)
    elif action == "delete":
        DL.remove(nzo, True)
    else:
        raise HTTPException(400, "action")
    return {"ok": True}


class PauseReq(BaseModel):
    paused: bool


@app.post("/api/pause")
async def pause(req: PauseReq):
    DL.set_paused(req.paused)
    return {"paused": DL.paused}


@app.get("/api/events")
async def events(limit: int = 200):
    return [{"at": r["at"], "kind": r["kind"], "text": r["text"], "data": json.loads(r["data"]) if r["data"] else None}
            for r in db.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(limit, 1000),))]


# --- settings --------------------------------------------------------------------------------------------
def _mask(v: str) -> str:
    return ("•" * 8 + v[-4:]) if v else ""


def _unmask(value: str, key: str) -> str:
    return db.get_settings()[key] if value and "•" in value else value


@app.get("/api/settings")
async def get_settings():
    s = db.get_settings()
    for k in config.SECRET_SETTINGS:
        s[k] = _mask(s[k])
    return {**s, "downloadDir": str(config.DOWNLOAD_DIR), "selfUrlDefault": f"http://plexarr:{config.PORT}"}


@app.put("/api/settings")
async def put_settings(values: dict):
    s = db.get_settings()
    clean = {}
    for k, v in values.items():
        if k not in config.DEFAULT_SETTINGS or k in ("auth_enabled", "auth_user", "setup_done"):
            continue
        if k in config.SECRET_SETTINGS:
            v = _unmask(v or "", k)
        default = config.DEFAULT_SETTINGS[k]
        if isinstance(default, bool):
            v = bool(v)
        elif isinstance(default, int):
            try:
                v = max(0, int(v))
            except (TypeError, ValueError):
                raise HTTPException(400, k)
        elif isinstance(default, str):
            v = str(v or "").strip()
            if k.endswith("_url"):
                v = v.rstrip("/")
        clean[k] = v
    if clean.get("mode", s["mode"]) not in ("fallback", "equal"):
        raise HTTPException(400, "mode")
    db.set_settings(clean)
    if any(k in clean for k in ("radarr_url", "radarr_api_key")):
        RADARR.forget()
    if any(k in clean for k in ("sonarr_url", "sonarr_api_key")):
        SONARR.forget()
    if clean.get("servers", s["servers"]) != s["servers"] or clean.get("plex_token", s["plex_token"]) != s["plex_token"]:
        asyncio.create_task(library_refresh())
    _lib_cache.clear()
    return await get_settings()


@app.get("/api/status")
async def status():
    s = db.get_settings()
    links = {}
    for a in (RADARR, SONARR):
        if a.configured:
            try:
                links[a.app] = await a.cached("link", 60, a.link_status)
            except (ArrError, httpx.HTTPError) as e:
                links[a.app] = {"error": str(e)}
    active = db.q1("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('Queued','Downloading','Paused')")["n"]
    return {"version": config.VERSION, "mode": s["mode"], "paused": DL.paused, "active": active,
            "configured": bool(s["plex_token"] and s["servers"]), "servers": _server_status(), "links": links,
            "radarr": RADARR.configured, "sonarr": SONARR.configured, "sweepAt": db.get_state("sweep_at"),
            "sweep": s["sweep_enabled"] and s["mode"] == "fallback", "refreshing": LIB.refreshing,
            "auth": auth.enabled()}


class PlexReq(BaseModel):
    token: str = ""


@app.post("/api/plex/servers")
async def plex_servers(req: PlexReq):
    token = _unmask(req.token, "plex_token") or db.get_settings()["plex_token"]
    if not token:
        raise HTTPException(400, "token")
    try:
        servers = await plex.shared_servers(token)
    except httpx.HTTPStatusError as e:
        raise HTTPException(400, "plex_token" if e.response.status_code == 401 else f"plex.tv {e.response.status_code}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"plex.tv: {e}")
    db.set_state("server_names", {**db.get_state("server_names", {}), **{s["id"]: s["name"] for s in servers}})
    out = []
    for info in servers:
        srv = plex.Server(info)
        try:
            await srv.connect()
            mc = await srv.get("/")
            out.append({"id": srv.id, "name": srv.name, "owner": srv.owner, "ok": True,
                        "downloads": bool(mc.get("allowSync")), "via": srv.base.split("//")[1].split(":")[0]})
        except (ConnectionError, httpx.HTTPError) as e:
            out.append({"id": srv.id, "name": srv.name, "owner": srv.owner, "ok": False, "error": str(e)})
    return out


class ArrTestReq(BaseModel):
    app: str
    url: str
    key: str


@app.post("/api/arr/test")
async def arr_test(req: ArrTestReq):
    key = _unmask(req.key, f"{req.app}_api_key")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(req.url.rstrip("/") + "/api/v3/system/status", headers={"X-Api-Key": key})
        if r.status_code == 401:
            return {"ok": False, "error": "api_key"}
        r.raise_for_status()
        d = r.json()
        return {"ok": True, "app": d.get("appName"), "version": d.get("version")}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e) or type(e).__name__}


class SetupReq(BaseModel):
    app: str
    active: bool


@app.post("/api/arr/setup")
async def arr_setup(req: SetupReq):
    a = {"radarr": RADARR, "sonarr": SONARR}.get(req.app)
    if not a or not a.configured:
        raise HTTPException(400, "no_" + req.app)
    try:
        out = await a.setup(req.active)
    except ArrError as e:
        raise HTTPException(400, str(e))
    a.forget("link")
    db.event("setup", f"{req.app}: indexer + download client {'active' if req.active else 'inactive'}",
             app=req.app.capitalize(), active=bool(req.active))
    return out


@app.post("/api/sweep")
async def sweep_now():
    try:
        return await sweep.run_once()
    except ArrError as e:
        raise HTTPException(400, str(e))


# --- setup wizard --------------------------------------------------------------------------------------
@app.get("/api/setup")
async def setup_info():
    s = db.get_settings()
    return {"setupDone": s["setup_done"] or bool(s["plex_token"] and s["servers"]), "version": config.VERSION}


@app.post("/api/setup/complete")
async def setup_complete():
    db.set_settings({"setup_done": True})
    return {"ok": True}


@app.post("/api/plex/pin")
async def plex_pin():
    try:
        return await plex.new_pin()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"plex.tv: {e}")


@app.get("/api/plex/pin/{pin_id}")
async def plex_pin_check(pin_id: int):
    """Saves the account token once the user approved the PIN on app.plex.tv."""
    try:
        token = await plex.pin_token(pin_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(400 if e.response.status_code == 404 else 502, "pin")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"plex.tv: {e}")
    if token:
        db.set_settings({"plex_token": token})
    return {"ok": bool(token), "token": _mask(token)}


@app.post("/api/check/download-dir")
async def check_download_dir():
    """Per connected app: does it see PlexArr's download folder at the same path?"""
    out = {}
    for a in (RADARR, SONARR):
        if not a.configured:
            continue
        try:
            out[a.app] = {"ok": await a.sees(config.DOWNLOAD_DIR)}
        except (ArrError, httpx.HTTPError, OSError) as e:
            out[a.app] = {"ok": False, "error": str(e)}
    return {"dir": str(config.DOWNLOAD_DIR), "apps": out}


@app.get("/api/apikey")
async def get_apikey():
    return {"key": auth.api_key()}


@app.post("/api/apikey")
async def new_apikey():
    return {"key": auth.new_api_key()}
