"""Radarr and Sonarr: what you already have, failed downloads, title parsing, searches and the automatic setup."""
import asyncio
import logging
import time

import httpx

from . import auth, config, db

log = logging.getLogger("plexarr.arr")


class ArrError(Exception):
    pass


class Arr:
    def __init__(self, app: str):
        self.app = app                      # radarr | sonarr
        self._cache: dict[str, tuple[float, object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def configured(self) -> bool:
        s = db.get_settings()
        return bool(s[f"{self.app}_url"] and s[f"{self.app}_api_key"])

    async def req(self, method: str, path: str, params=None, json=None, timeout=30):
        s = db.get_settings()
        url, key = s[f"{self.app}_url"].rstrip("/"), s[f"{self.app}_api_key"]
        if not url or not key:
            raise ArrError(f"{self.app} is not configured")
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, f"{url}/api/v3{path}", params=params, json=json, headers={"X-Api-Key": key})
        if r.status_code >= 400:
            raise ArrError(f"{self.app} {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    async def get(self, path, **params):
        return await self.req("GET", path, params=params or None)

    async def cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            val = await fn()
            self._cache[key] = (time.time(), val)
            return val

    def forget(self, prefix: str = ""):
        for k in [k for k in self._cache if k.startswith(prefix)]:
            del self._cache[k]

    async def parse(self, title: str) -> dict:
        return await self.cached("parse:" + title, 3600, lambda: self.get("/parse", title=title))

    async def command(self, name: str, **body):
        return await self.req("POST", "/command", json={"name": name, **body})

    # --- automatic setup ----------------------------------------------------------------------
    async def setup(self, active: bool) -> dict:
        """Creates or updates the PlexArr download client and indexer, linked to each other."""
        s = db.get_settings()
        base = (s["self_url"] or f"http://plexarr:{config.PORT}").rstrip("/")
        host, _, port = base.split("://", 1)[1].partition(":")
        port = int(port.split("/")[0] or 80)
        key = auth.api_key()
        cat = "radarr" if self.app == "radarr" else "sonarr"

        clients = await self.get("/downloadclient")
        client = next((c for c in clients if c["name"] == "PlexArr"), None)
        if client is None:
            client = next(c for c in await self.get("/downloadclient/schema") if c["implementation"] == "Sabnzbd")
        _fill(client, {"host": host, "port": port, "useSsl": False, "urlBase": "sabnzbd", "apiKey": key,
                       "movieCategory": cat, "tvCategory": cat})
        # Lowest priority: Sonarr/Radarr only use it for releases of the PlexArr indexer.
        client.update({"name": "PlexArr", "enable": True, "priority": 50, "removeCompletedDownloads": True,
                       "removeFailedDownloads": True})
        client = await self.req("PUT" if client.get("id") else "POST",
                                "/downloadclient" + (f"/{client['id']}" if client.get("id") else ""), json=client)

        indexers = await self.get("/indexer")
        idx = next((i for i in indexers if i["name"] == "PlexArr"), None)
        if idx is None:
            idx = next(i for i in await self.get("/indexer/schema") if i["implementation"] == "Newznab")
        cats = [2000, 2030, 2040, 2045] if self.app == "radarr" else [5000, 5030, 5040, 5045]
        _fill(idx, {"baseUrl": base, "apiPath": "/newznab/api", "apiKey": key, "categories": cats,
                    "animeCategories": []})
        idx.update({"name": "PlexArr", "enableRss": active, "enableAutomaticSearch": active,
                    "enableInteractiveSearch": True, "priority": 50, "downloadClientId": client["id"]})
        idx = await self.req("PUT" if idx.get("id") else "POST",
                             "/indexer" + (f"/{idx['id']}" if idx.get("id") else ""), json=idx)
        return {"client": client["id"], "indexer": idx["id"], "active": active}

    async def link_status(self) -> dict:
        idx = next((i for i in await self.get("/indexer") if i["name"] == "PlexArr"), None)
        cli = next((c for c in await self.get("/downloadclient") if c["name"] == "PlexArr"), None)
        return {"indexer": bool(idx), "client": bool(cli),
                "active": bool(idx and (idx["enableRss"] or idx["enableAutomaticSearch"])),
                "linked": bool(idx and cli and idx.get("downloadClientId") == cli["id"])}


def _fill(provider: dict, values: dict):
    for f in provider.get("fields", []):
        if f["name"] in values:
            f["value"] = values[f["name"]]


class Radarr(Arr):
    def __init__(self):
        super().__init__("radarr")

    async def movies(self) -> dict[int, dict]:
        async def load():
            return {m["tmdbId"]: m for m in await self.get("/movie") if m.get("tmdbId")}
        return await self.cached("movies", 600, load)

    async def movie(self, tmdb: int | None, imdb: str | None = None) -> dict | None:
        movies = await self.movies()
        if tmdb and tmdb in movies:
            return movies[tmdb]
        if imdb:
            return next((m for m in movies.values() if m.get("imdbId") == imdb), None)
        return None

    async def failed(self, movie_id: int) -> list[dict]:
        async def load():
            return [h for h in await self.get("/history/movie", movieId=movie_id) if h["eventType"] == "downloadFailed"]
        return await self.cached(f"failed:{movie_id}", 120, load)


class Sonarr(Arr):
    def __init__(self):
        super().__init__("sonarr")

    async def series(self) -> dict[int, dict]:
        async def load():
            return {s["tvdbId"]: s for s in await self.get("/series") if s.get("tvdbId")}
        return await self.cached("series", 600, load)

    async def episodes(self, series_id: int) -> dict[tuple[int, int], dict]:
        async def load():
            return {(e["seasonNumber"], e["episodeNumber"]): e
                    for e in await self.get("/episode", seriesId=series_id, includeEpisodeFile="true")}
        return await self.cached(f"episodes:{series_id}", 300, load)

    async def failed(self, series_id: int) -> list[dict]:
        async def load():
            return [h for h in await self.get("/history/series", seriesId=series_id) if h["eventType"] == "downloadFailed"]
        return await self.cached(f"failed:{series_id}", 120, load)


RADARR = Radarr()
SONARR = Sonarr()
