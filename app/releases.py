"""Turns files on a friend's Plex server into releases: a title Sonarr/Radarr parse well, a category, and
whether PlexArr offers it right now (in fallback mode only when Usenet failed or has nothing)."""
import logging
import os
import re
import time
from datetime import datetime, timezone

from . import config, db
from .arr import RADARR, SONARR, ArrError

log = logging.getLogger("plexarr.releases")
_titles: dict[tuple[str, int], tuple[str, bool]] = {}

RES = {"4k": "2160p", "2160": "2160p", "1080": "1080p", "720": "720p", "576": "576p", "480": "480p", "sd": "480p"}
VCODEC = {"hevc": "HEVC", "h265": "HEVC", "h264": "H264", "avc": "H264", "av1": "AV1", "mpeg4": "XviD", "vc1": "VC1"}
ACODEC = {"eac3": "DDP", "ac3": "DD", "dca": "DTS", "dts": "DTS", "truehd": "TrueHD", "aac": "AAC", "flac": "FLAC",
          "mp3": "MP3", "opus": "Opus"}
CHANNELS = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}
EXPLICIT_TV = re.compile(r"\b(HDTV|PDTV|SDTV|DSR|TVRip)\b", re.I)
UNKNOWN_SOURCES = {"unknown", "tv", "television"}


def norm(s: str | None) -> str:
    s = re.sub(r"\(\d{4}\)$", "", (s or "").strip())
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("&", "and"))


def resolution(row) -> str:
    return RES.get(str(row["resolution"] or "").lower(), "")


def category(row) -> int:
    res = resolution(row)
    base = 2000 if row["kind"] == "movie" else 5000
    return base + (45 if res == "2160p" else 40 if res in ("1080p", "720p") else 30)


def _source_guess(row) -> str:
    """For files whose name has no source: a high bitrate is most likely a Blu-ray, otherwise a web release."""
    floor = {"2160p": 40000, "1080p": 15000, "720p": 8000}.get(resolution(row), 4000)
    return "BluRay" if (row["bitrate"] or 0) >= floor else "WEB-DL"


def _with_source(cand: str, row) -> str:
    """Puts the guessed source right after the resolution, keeping the rest of the name (and the group)."""
    src = _source_guess(row)
    m = re.search(r"(?i)\b(2160p|1080p|720p|576p|480p|4k|uhd)\b([ ._-]?)", cand)
    if m:
        sep = m.group(2) or "."
        return cand[:m.end(1)] + sep + src + (sep if m.group(2) else "") + cand[m.end():]
    return f"{cand} {resolution(row)} {src}".replace("  ", " ")


def _media_tokens(row) -> str:
    res = resolution(row)
    src = _source_guess(row)
    a = ACODEC.get((row["acodec"] or "").lower(), "")
    if a:
        a += CHANNELS.get(row["channels"] or 0, "")
    return " ".join(x for x in (res, src, a, VCODEC.get((row["vcodec"] or "").lower(), "")) if x)


def _candidates(row) -> list[str]:
    path = (row["file"] or "").replace("\\", "/")
    stem = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.basename(os.path.dirname(path))
    out = [stem]
    # The folder name only describes the release for movies (for episodes it is "Season 01").
    if row["kind"] == "movie" and folder and folder != stem:
        out.append(folder)
    return [c for c in out if c]


def _clean(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]', " ", title)).strip()


async def _movie_title(row) -> tuple[str, bool]:
    names = {norm(row["title"]), norm(row["original_title"])} - {""}
    rest = ""
    for cand in _candidates(row):
        p = await RADARR.parse(cand)
        info = p.get("parsedMovieInfo") or {}
        q = ((info.get("quality") or {}).get("quality") or {})
        source_ok = q.get("source") not in UNKNOWN_SOURCES or EXPLICIT_TV.search(cand)
        title_ok = ((p.get("movie") or {}).get("tmdbId") == row["tmdb"] and row["tmdb"]) or \
            any(norm(t) in names for t in info.get("movieTitles") or [])
        if title_ok and source_ok:
            return cand, False
        if title_ok and info.get("year") in (row["year"], None, 0) and q.get("resolution"):
            return _with_source(cand, row), True
        if not rest and source_ok:
            # Keep what comes after the year: "WAVE (2016) x264 1080p Bluray" -> "x264 1080p Bluray"
            m = re.search(r"(?:19|20)\d{2}\)?[ ._-]+(.+)$", cand)
            rest = m.group(1) if m else ""
    rest = rest or _media_tokens(row)
    year = f" ({row['year']})" if row["year"] else ""
    return _clean(f"{row['title']}{year} {rest}"), True


async def _episode_title(row) -> tuple[str, bool]:
    names = {norm(row["title"]), norm(row["original_title"])} - {""}
    rest = ""
    for cand in _candidates(row):
        p = await SONARR.parse(cand)
        info = p.get("parsedEpisodeInfo") or {}
        q = ((info.get("quality") or {}).get("quality") or {})
        source_ok = q.get("source") not in UNKNOWN_SOURCES or EXPLICIT_TV.search(cand)
        series_ok = ((p.get("series") or {}).get("tvdbId") == row["tvdb"] and row["tvdb"]) or \
            norm(info.get("seriesTitle")) in names
        ep_ok = info.get("seasonNumber") == row["season"] and row["episode"] in (info.get("episodeNumbers") or [])
        if series_ok and ep_ok and source_ok:
            return cand, False
        if series_ok and ep_ok and q.get("resolution"):
            return _with_source(cand, row), True
        if not rest and source_ok:
            m = re.search(r"S\d{1,2}E\d{1,3}(?:-?E\d{1,3})*[ ._-]+(.+)$", cand, re.I)
            rest = m.group(1) if m else ""
    rest = rest or _media_tokens(row)
    return _clean(f"{row['title']} S{row['season']:02d}E{row['episode']:02d} {rest}"), True


async def title(row) -> tuple[str, bool]:
    """(release title, made up by PlexArr?)"""
    key = (row["server"], row["part_id"])
    if key not in _titles:
        try:
            _titles[key] = await (_movie_title(row) if row["kind"] == "movie" else _episode_title(row))
        except ArrError as e:
            log.warning("parse: %s", e)
            stem = _candidates(row)[0] if _candidates(row) else row["title"]
            return stem, False
    return _titles[key]


# --- may PlexArr offer it now? ----------------------------------------------------------------------
def _ts(iso: str | None) -> float:
    if not iso:
        return 0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0


def request_key(row) -> str:
    if row["kind"] == "movie":
        return f"movie:{row['tmdb'] or row['imdb']}"
    return f"episode:{row['tvdb']}:{row['season']}:{row['episode']}"


def requested(key: str) -> bool:
    r = db.q1("SELECT until FROM requests WHERE key=?", (key,))
    return bool(r and r["until"] > time.time())


def _recent_failure(history: list[dict], episode_id: int | None = None) -> bool:
    cutoff = time.time() - 30 * 86400
    for h in history:
        if episode_id is not None and h.get("episodeId") != episode_id:
            continue
        if (h.get("downloadId") or "").startswith(config.NZO_PREFIX):
            continue                     # PlexArr's own failure: no reason to offer it again
        if _ts(h.get("date")) >= cutoff:
            return True
    return False


async def eligible(row) -> tuple[bool, str]:
    """(offer it?, reason code). Codes are translated in the web UI."""
    s = db.get_settings()
    if s["mode"] == "equal":
        return True, "equal"
    if requested(request_key(row)):
        return True, "requested"
    wait = s["wait_hours"] * 3600
    now = time.time()
    if row["kind"] == "movie":
        if not RADARR.configured:
            return False, "no_arr"
        m = await RADARR.movie(row["tmdb"], row["imdb"])
        if not m:
            return True, "not_in_arr"     # Radarr ignores releases of movies it doesn't have
        if m.get("hasFile"):
            return False, "have"
        if await _recent_failure_movie(m):
            return True, "failed"
        if not m.get("monitored"):
            return False, "unmonitored"
        if not m.get("isAvailable"):
            return False, "not_available"
        since = max(_ts(m.get("added")), *(_ts(m.get(k)) for k in ("digitalRelease", "physicalRelease")))
        if since and since > now:
            since = _ts(m.get("added"))
        return (True, "missing") if now - since >= wait else (False, "waiting")
    if not SONARR.configured:
        return False, "no_arr"
    series = (await SONARR.series()).get(row["tvdb"])
    if not series:
        return True, "not_in_arr"
    ep = (await SONARR.episodes(series["id"])).get((row["season"], row["episode"]))
    if not ep:
        return True, "not_in_arr"
    if ep.get("hasFile"):
        return False, "have"
    if _recent_failure(await SONARR.failed(series["id"]), ep["id"]):
        return True, "failed"
    if not ep.get("monitored") or not series.get("monitored"):
        return False, "unmonitored"
    aired = _ts(ep.get("airDateUtc"))
    if not aired or aired > now:
        return False, "not_available"
    since = max(aired, _ts(series.get("added")))
    return (True, "missing") if now - since >= wait else (False, "waiting")


async def _recent_failure_movie(m: dict) -> bool:
    return _recent_failure(await RADARR.failed(m["id"]))
