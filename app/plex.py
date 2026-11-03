"""Plex: the friends' servers (through plex.tv), the connection to each of them, and the index of their libraries."""
import asyncio
import logging
import re
import time
import uuid
from urllib.parse import urlencode

import httpx

from . import config, db

log = logging.getLogger("plexarr.plex")
PAGE = 500


def client_id() -> str:
    cid = db.get_state("client_id")
    if not cid:
        cid = "plexarr-" + uuid.uuid4().hex[:16]
        db.set_state("client_id", cid)
    return cid


def headers(token: str) -> dict:
    return {"Accept": "application/json", "X-Plex-Token": token, "X-Plex-Client-Identifier": client_id(),
            "X-Plex-Product": "PlexArr", "X-Plex-Version": config.VERSION}


async def shared_servers(token: str | None = None) -> list[dict]:
    """Plex servers shared with this account (owned servers are left out)."""
    token = token or db.get_settings()["plex_token"]
    if not token:
        return []
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://plex.tv/api/v2/resources", params={"includeHttps": 1, "includeRelay": 0},
                        headers=headers(token))
        r.raise_for_status()
    out = []
    for res in r.json():
        if "server" not in (res.get("provides") or "") or res.get("owned"):
            continue
        out.append({"id": res["clientIdentifier"], "name": res["name"], "owner": res.get("sourceTitle") or "",
                    "online": bool(res.get("presence")), "token": res.get("accessToken") or "",
                    "connections": [c for c in res.get("connections", []) if not c.get("relay")]})
    return out


# --- signing in with Plex (a PIN the user approves on app.plex.tv) ------------------------------------
async def new_pin() -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://plex.tv/api/v2/pins", params={"strong": "true"}, headers=headers(""))
        r.raise_for_status()
    pin = r.json()
    query = urlencode({"clientID": client_id(), "code": pin["code"], "context[device][product]": "PlexArr"})
    return {"id": pin["id"], "url": f"https://app.plex.tv/auth#?{query}"}


async def pin_token(pin_id: int) -> str:
    """The account token once the user approved the PIN, else ""."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"https://plex.tv/api/v2/pins/{pin_id}", headers=headers(""))
        r.raise_for_status()
    return r.json().get("authToken") or ""


class Server:
    """One friend's server: picks a working direct connection and keeps it for a while."""

    def __init__(self, info: dict):
        self.id = info["id"]
        self.name = info["name"]
        self.owner = info["owner"]
        self.token = info["token"]
        self.connections = info["connections"]
        self.base = None
        self.checked = 0.0
        self.error = ""

    def update(self, info: dict):
        self.name, self.owner, self.token, self.connections = info["name"], info["owner"], info["token"], info["connections"]

    async def connect(self, force=False) -> str:
        if self.base and not force and time.time() - self.checked < 1800:
            return self.base
        # Remote direct connections first (plex.direct before own domains), local ones last: those only
        # work when PlexArr runs in the same network as the server.
        order = sorted(self.connections, key=lambda c: (c.get("local", False), "plex.direct" not in c["uri"]))
        errors = []
        async with httpx.AsyncClient(timeout=8) as c:
            for con in order:
                try:
                    r = await c.get(con["uri"] + "/identity", headers=headers(self.token))
                    if r.status_code == 200:
                        self.base, self.checked, self.error = con["uri"], time.time(), ""
                        return self.base
                    errors.append(f"{r.status_code}")
                except httpx.HTTPError as e:
                    errors.append(type(e).__name__)
        self.base, self.error = None, "no direct connection (" + ", ".join(errors) + ")"
        raise ConnectionError(f"{self.name}: {self.error}")

    def url(self, path: str) -> str:
        return self.base + path

    async def get(self, path: str, params: dict | None = None, timeout=60) -> dict:
        for attempt in (0, 1):
            await self.connect(force=attempt == 1)
            try:
                async with httpx.AsyncClient(timeout=timeout) as c:
                    r = await c.get(self.url(path), params=params, headers=headers(self.token))
                r.raise_for_status()
                return r.json().get("MediaContainer", {})
            except httpx.TransportError:
                if attempt:
                    raise
        return {}

    async def all_pages(self, path: str, params: dict) -> list[dict]:
        out, start = [], 0
        while True:
            mc = await self.get(path, {**params, "X-Plex-Container-Start": start, "X-Plex-Container-Size": PAGE})
            items = mc.get("Metadata", [])
            out += items
            start += len(items)
            if not items or start >= int(mc.get("totalSize", start)):
                return out


# --- ids -----------------------------------------------------------------------------------------
_LEGACY = re.compile(r"com\.plexapp\.agents\.(\w+)://([^/?]+)")


def ids_of(item: dict) -> dict:
    """imdb/tmdb/tvdb from the new agents' Guid list or from a legacy agent guid."""
    out = {"imdb": None, "tmdb": None, "tvdb": None}
    for g in item.get("Guid", []):
        src, _, val = g.get("id", "").partition("://")
        if src in out and val:
            out[src] = val
    m = _LEGACY.match(item.get("guid", ""))
    if m:
        agent, val = m.groups()
        if agent == "imdb" or val.startswith("tt"):
            out["imdb"] = out["imdb"] or val
        elif agent == "themoviedb":
            out["tmdb"] = out["tmdb"] or val
        elif agent in ("thetvdb", "thetvdbdvdorder"):
            out["tvdb"] = out["tvdb"] or val
    for k in ("tmdb", "tvdb"):
        out[k] = int(out[k]) if out[k] and str(out[k]).isdigit() else None
    return out


def _parts(server_id: str, item: dict, base: dict) -> list[tuple]:
    rows = []
    for m in item.get("Media", []):
        parts = m.get("Part", [])
        if len(parts) != 1:          # multi-part files (CD1/CD2) can't be offered as one release
            continue
        p = parts[0]
        if not p.get("key") or not p.get("size"):
            continue
        rows.append((
            p["id"], server_id, base["kind"], item["ratingKey"], base.get("show_key"), base["title"],
            base.get("original_title"), base.get("year"), base.get("season"), base.get("episode"),
            base.get("episode_title"), base.get("imdb"), base.get("tmdb"), base.get("tvdb"),
            item.get("addedAt"), p["key"], p.get("file", ""), p["size"], m.get("container") or p.get("container"),
            m.get("videoResolution"), m.get("videoCodec"), m.get("audioCodec"), m.get("audioChannels"),
            m.get("bitrate"), base.get("thumb"),
        ))
    return rows


async def index_server(srv: Server) -> list[tuple]:
    rows = []
    sections = (await srv.get("/library/sections")).get("Directory", [])
    for sec in sections:
        key = sec["key"]
        if sec["type"] == "movie":
            for it in await srv.all_pages(f"/library/sections/{key}/all", {"type": 1, "includeGuids": 1}):
                ids = ids_of(it)
                rows += _parts(srv.id, it, {"kind": "movie", "title": it.get("title", ""), "year": it.get("year"),
                                            "original_title": it.get("originalTitle"), "thumb": it.get("thumb"),
                                            "imdb": ids["imdb"], "tmdb": ids["tmdb"]})
        elif sec["type"] == "show":
            shows = {s["ratingKey"]: s for s in await srv.all_pages(f"/library/sections/{key}/all",
                                                                   {"type": 2, "includeGuids": 1})}
            sids = {k: ids_of(s) for k, s in shows.items()}
            for ep in await srv.all_pages(f"/library/sections/{key}/all", {"type": 4, "includeGuids": 1}):
                sk = ep.get("grandparentRatingKey")
                show, ids = shows.get(sk, {}), sids.get(sk, {})
                if not ids.get("tvdb") and not ids.get("imdb"):
                    continue
                rows += _parts(srv.id, ep, {
                    "kind": "episode", "show_key": sk, "title": show.get("title") or ep.get("grandparentTitle", ""),
                    "original_title": show.get("originalTitle"), "year": show.get("year"),
                    "season": ep.get("parentIndex"), "episode": ep.get("index"), "episode_title": ep.get("title"),
                    "imdb": ids.get("imdb"), "tmdb": ids.get("tmdb"), "tvdb": ids.get("tvdb"),
                    "thumb": show.get("thumb") or ep.get("grandparentThumb")})
    return rows


class Library:
    def __init__(self):
        self.servers: dict[str, Server] = {}
        self.status: dict[str, dict] = {}
        self.refreshing = False
        self.lock = asyncio.Lock()

    async def load_servers(self) -> list[Server]:
        wanted = set(db.get_settings()["servers"])
        found = {s["id"]: s for s in await shared_servers()} if wanted else {}
        for sid in list(self.servers):
            if sid not in wanted:
                del self.servers[sid]
        for sid in wanted:
            if sid in found:
                if sid in self.servers:
                    self.servers[sid].update(found[sid])
                else:
                    self.servers[sid] = Server(found[sid])
        return [self.servers[s] for s in wanted if s in self.servers]

    async def server(self, sid: str) -> Server:
        if sid not in self.servers:
            await self.load_servers()
        if sid not in self.servers:
            raise ConnectionError(f"Plex server {sid} is not available to this account")
        return self.servers[sid]

    async def refresh(self):
        if self.lock.locked():
            return
        async with self.lock:
            self.refreshing = True
            try:
                try:
                    servers = await self.load_servers()
                except httpx.HTTPError as e:
                    log.warning("plex.tv: %s", e)
                    return
                keep = [s.id for s in servers]
                db.x(f"DELETE FROM releases WHERE server NOT IN ({','.join('?' * len(keep))})", keep)
                for srv in servers:
                    t0 = time.time()
                    try:
                        rows = await index_server(srv)
                    except Exception as e:  # noqa: BLE001 - one server failing must not stop the others
                        log.warning("index %s: %s", srv.name, e)
                        self.status[srv.id] = {**self.status.get(srv.id, {}), "error": str(e), "name": srv.name}
                        continue
                    db.x("DELETE FROM releases WHERE server=?", (srv.id,))
                    db.many(f"INSERT OR REPLACE INTO releases VALUES ({','.join('?' * 25)})", rows)
                    self.status[srv.id] = {"name": srv.name, "owner": srv.owner, "error": "", "at": time.time(),
                                           "files": len(rows), "seconds": round(time.time() - t0, 1)}
                    log.info("indexed %s: %d files in %.0fs", srv.name, len(rows), time.time() - t0)
                db.set_state("indexed_at", time.time())
            finally:
                self.refreshing = False


LIB = Library()
