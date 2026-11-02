"""Fallback mode: Sonarr/Radarr only search when something is added or a download fails. Once an hour PlexArr
asks them to search a few missing items that a friend has, so the fallback also works for things Usenet
never had. Searches also hit your Usenet indexers, so there are only a few per hour."""
import asyncio
import logging
import time
from collections import defaultdict

from . import db, releases
from .arr import RADARR, SONARR, ArrError

log = logging.getLogger("plexarr.sweep")


def _due(key: str, days: int) -> bool:
    r = db.q1("SELECT at FROM sweeps WHERE key=?", (key,))
    return not r or time.time() - r["at"] > days * 86400


def _mark(key: str):
    db.x("INSERT INTO sweeps(key, at) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET at=excluded.at", (key, time.time()))


async def run_once() -> dict:
    s = db.get_settings()
    budget, days = s["sweep_per_run"], s["sweep_repeat_days"]
    done = {"movies": [], "episodes": []}
    if RADARR.configured and budget > 0:
        queue = {q.get("movieId") for q in (await RADARR.get("/queue", pageSize=1000)).get("records", [])}
        ids = []
        for m in (await RADARR.movies()).values():
            if len(ids) >= budget:
                break
            if m.get("hasFile") or not m.get("monitored") or not m.get("isAvailable") or m["id"] in queue:
                continue
            key = f"movie:{m['tmdbId']}"
            if not _due(key, days):
                continue
            rows = db.q("SELECT * FROM releases WHERE kind='movie' AND (tmdb=? OR imdb=?)", (m["tmdbId"], m.get("imdbId") or "-"))
            if rows and (await releases.eligible(rows[0]))[0]:
                ids.append(m["id"])
                _mark(key)
                done["movies"].append(m["title"])
        if ids:
            await RADARR.command("MoviesSearch", movieIds=ids)
        budget -= len(ids)
    if SONARR.configured and budget > 0:
        queue = {q.get("episodeId") for q in (await SONARR.get("/queue", pageSize=1000)).get("records", [])}
        missing = (await SONARR.get("/wanted/missing", pageSize=2000, monitored="true", includeSeries="true")).get("records", [])
        seasons = defaultdict(list)
        for e in missing:
            tvdb = (e.get("series") or {}).get("tvdbId")
            if not tvdb or e["id"] in queue:
                continue
            seasons[(e["seriesId"], tvdb, e["seasonNumber"])].append(e)
        for (series_id, tvdb, season), eps in seasons.items():
            if budget <= 0:
                break
            ok = []
            for e in eps:
                key = f"episode:{tvdb}:{season}:{e['episodeNumber']}"
                if not _due(key, days):
                    continue
                row = db.q1("SELECT * FROM releases WHERE kind='episode' AND tvdb=? AND season=? AND episode=?",
                            (tvdb, season, e["episodeNumber"]))
                if row and (await releases.eligible(row))[0]:
                    ok.append((key, e))
            if not ok:
                continue
            title = (ok[0][1].get("series") or {}).get("title", "?")
            if len(ok) >= 3:
                await SONARR.command("SeasonSearch", seriesId=series_id, seasonNumber=season)
                done["episodes"].append(f"{title} S{season:02d}")
                budget -= 1
            else:
                ok = ok[:budget]
                await SONARR.command("EpisodeSearch", episodeIds=[e["id"] for _, e in ok])
                done["episodes"] += [f"{title} S{season:02d}E{e['episodeNumber']:02d}" for _, e in ok]
                budget -= len(ok)
            for key, _ in ok:
                _mark(key)
    if done["movies"] or done["episodes"]:
        db.event("sweep", ", ".join(done["movies"] + done["episodes"]))
    db.set_state("sweep_at", time.time())
    return done


async def loop():
    await asyncio.sleep(120)
    while True:
        s = db.get_settings()
        if s["sweep_enabled"] and s["mode"] == "fallback" and s["servers"]:
            try:
                await run_once()
            except ArrError as e:
                log.warning("sweep: %s", e)
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("sweep")
        await asyncio.sleep(3600)
