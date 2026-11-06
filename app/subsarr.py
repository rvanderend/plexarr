"""Subsarr API for Bazarr: the external subtitles on the friends' Plex servers as a subtitle provider.

Bazarr has a built-in provider for Subsarr (a self-hosted subtitle server) with a configurable address.
PlexArr answers its two calls, with the API key in the path because Bazarr sends no key of its own:
    {base}/api/v1/info
    {base}/api/v1/subtitles/search?language=dutch&imdb_id=tt…[&year=…][&season=…&episode=…]
    {base}/api/v1/subtitles/search?language=dutch&query=<title>[&season=…&episode=…]   (when imdb found nothing)
where {base} is http://plexarr:8080/subsarr/<api key>. Only external subtitles in a text format can be
offered: Plex doesn't hand out embedded ones (501) and image subtitles (VobSub, PGS) are no use to Bazarr.
"""
import asyncio
import hmac
import logging
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import auth, config, db, plex, releases
from .downloader import SUB_EXT
from .plex import LIB

log = logging.getLogger("plexarr.subsarr")
router = APIRouter()
CACHE_SECONDS = 600
_streams: dict[tuple[str, str], tuple[float, dict]] = {}

# Bazarr's Subsarr language names (Subscene's, as slugs) with ISO 639-1 and 639-2 T/B codes.
_LANGS = """abkhazian ab abk; afrikaans af afr; albanian sq sqi alb; amharic am amh; arabic ar ara; aragonese an arg;
armenian hy hye arm; assamese as asm; azerbaijani az aze; basque eu eus baq; belarusian be bel; bengali bn ben;
bosnian bs bos; breton br bre; bulgarian bg bul; burmese my mya bur; cambodian-khmer km khm; catalan ca cat;
chinese_bg_code zh zho chi; croatian hr hrv; czech cs ces cze; danish da dan; dutch nl nld dut; english en eng;
esperanto eo epo; estonian et est; farsi_persian fa fas per; finnish fi fin; french fr fra fre; gaelic gd gla;
georgian ka kat geo; german de deu ger; greek el ell gre; hebrew he heb; hindi hi hin; hungarian hu hun;
icelandic is isl ice; igbo ig ibo; indonesian id ind; interlingua ia ina; irish ga gle; italian it ita;
japanese ja jpn; kannada kn kan; kazakh kk kaz; kinyarwanda rw kin; korean ko kor; kurdish ku kur; latvian lv lav;
lithuanian lt lit; luxembourgish lb ltz; macedonian mk mkd mac; malay ms msa may; malayalam ml mal; marathi mr mar;
mongolian mn mon; navajo nv nav; nepali ne nep; northen-sami se sme; norwegian no nor nb nob nn nno; occitan oc oci;
pashto ps pus; polish pl pol; portuguese pt por; punjabi pa pan; romanian ro ron rum; russian ru rus; serbian sr srp;
sindhi sd snd; sinhala si sin; slovak sk slk slo; slovenian sl slv; somali so som; spanish es spa; sundanese su sun;
swahili sw swa; swedish sv swe; tagalog tl tgl fil; tamil ta tam; tatar tt tat; telugu te tel; thai th tha;
turkish tr tur; turkmen tk tuk; ukranian uk ukr; urdu ur urd; uzbek uz uzb; vietnamese vi vie; welsh cy cym wel;
yoruba yo yor"""
RELEASE_TOKENS = re.compile(r"(?i)\b((?:19|20)\d{2}|\d{3,4}p|web(?:-?dl|rip)?|blu-?ray|remux|hdtv|[xh]\.?26[45]|hevc)\b")
CODES = {code: name for entry in _LANGS.split(";") for name, *codes in [entry.split()] for code in codes}


def language(stream: dict) -> str | None:
    """Bazarr's name for the language of a Plex subtitle stream."""
    tag = (stream.get("languageTag") or "").lower()
    if tag.startswith("pt-br"):
        return "brazilian_portuguese"
    for code in (tag.split("-")[0], (stream.get("languageCode") or "").lower()):
        if code in CODES:
            return CODES[code]
    return None


def release(row) -> str:
    """The file name Bazarr scores against the user's file: for a movie the folder name when it says more."""
    return max(releases._candidates(row) or [row["title"]], key=lambda c: len(RELEASE_TOKENS.findall(c)))


def _key_ok(key: str) -> bool:
    return bool(key) and hmac.compare_digest(key, auth.api_key())


def _int(v: str | None) -> int | None:
    v = (v or "").strip()
    return int(v) if v.isdigit() else None


def _find(p) -> list:
    """Index rows for a search: by IMDb id (of the movie, or of the show for an episode) or by title."""
    imdb, query = (p.get("imdb_id") or "").strip(), (p.get("query") or "").strip()
    season, episode = _int(p.get("season")), _int(p.get("episode"))
    if imdb and not imdb.startswith("tt"):
        imdb = "tt" + imdb.zfill(7)
    if season is None:
        if imdb:
            return db.q("SELECT * FROM releases WHERE kind='movie' AND imdb=?", (imdb,))
        if query:
            name = releases.norm(query)
            return [r for r in db.q("SELECT * FROM releases WHERE kind='movie' AND (title LIKE ? OR original_title LIKE ?)",
                                    (f"%{query}%", f"%{query}%"))
                    if name in (releases.norm(r["title"]), releases.norm(r["original_title"]))]
        return []
    where, args = ["kind='episode'", "season=?"], [season]
    if episode is not None:
        where.append("episode=?"); args.append(episode)
    if imdb:
        where.append("imdb=?"); args.append(imdb)
        return db.q(f"SELECT * FROM releases WHERE {' AND '.join(where)}", args)
    if query:
        name = releases.norm(query)
        where.append("(title LIKE ? OR original_title LIKE ?)"); args += [f"%{query}%", f"%{query}%"]
        return [r for r in db.q(f"SELECT * FROM releases WHERE {' AND '.join(where)}", args)
                if name in (releases.norm(r["title"]), releases.norm(r["original_title"]))]
    return []


async def _subtitle_streams(row) -> list[dict]:
    """The external text subtitles of one file on a friend's server (cached for a while)."""
    ck = (row["server"], row["rating_key"])
    hit = _streams.get(ck)
    if not hit or time.time() - hit[0] > CACHE_SECONDS:
        srv = await LIB.server(row["server"])
        md = (await srv.get(f"/library/metadata/{row['rating_key']}", timeout=20)).get("Metadata", [{}])[0]
        parts = {p.get("id"): p.get("Stream", []) for m in md.get("Media", []) for p in m.get("Part", [])}
        hit = (time.time(), parts)
        _streams[ck] = hit
    return [s for s in hit[1].get(row["part_id"], [])
            if s.get("streamType") == 3 and s.get("key") and SUB_EXT.get((s.get("codec") or "").lower())]


async def _items(row, lang: str, base: str) -> list[dict]:
    try:
        streams = await _subtitle_streams(row)
    except (ConnectionError, httpx.HTTPError) as e:
        log.info("subtitles of %s on %s: %s", row["title"], row["server"], e)
        return []
    stem = release(row)
    out = []
    for s in streams:
        # Bazarr has no notion of forced subtitles from Subsarr: a forced track offered as a full one would win
        # over a complete subtitle, so leave those out.
        if s.get("forced") or language(s) != lang:
            continue
        ext = SUB_EXT[s["codec"].lower()]
        hi = bool(s.get("hearingImpaired"))
        out.append({"id": f"plexarr-{row['server']}-{s['id']}", "language": lang, "hi": hi,
                    "download_url": f"{base}/api/v1/download/{row['server']}/{s['id']}.{ext}",
                    "title": row["title"], "releases": [stem],
                    "filename": f"{stem}.{s.get('languageTag') or lang}{'.hi' if hi else ''}.{ext}"})
    return out


def _label(rows) -> str:
    r = rows[0]
    if r["kind"] == "movie":
        return f"{r['title']} ({r['year']})" if r["year"] else r["title"]
    return f"{r['title']} S{r['season']:02d}E{r['episode']:02d}"


@router.get("/subsarr/{key}/api/v1/info")
async def info(key: str):
    if not _key_ok(key):
        raise HTTPException(401, "Incorrect API key")
    return {"name": "PlexArr", "version": config.VERSION}


@router.get("/subsarr/{key}/api/v1/subtitles/search")
async def search(key: str, request: Request):
    if not _key_ok(key):
        raise HTTPException(401, "Incorrect API key")
    p = request.query_params
    lang = (p.get("language") or "").strip().lower()
    rows = _find(p)
    base = str(request.url).split("/api/v1/", 1)[0]
    items = [i for found in await asyncio.gather(*(_items(r, lang, base) for r in rows)) for i in found]
    if rows:
        what = _label(rows)
        db.event("subtitles", f"Bazarr {lang}: {what}: {len(items)} found", what=what, lang=lang, found=len(items))
    return JSONResponse({"items": items, "total": len(items)})


@router.get("/subsarr/{key}/api/v1/download/{server}/{stream_id}.{ext}")
async def download(key: str, server: str, stream_id: int, ext: str):
    if not _key_ok(key):
        raise HTTPException(401, "Incorrect API key")
    try:
        srv = await LIB.server(server)
        await srv.connect()
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(srv.url(f"/library/streams/{stream_id}"), headers=plex.headers(srv.token))
    except (ConnectionError, httpx.HTTPError) as e:
        raise HTTPException(502, str(e))
    if r.status_code != 200 or not r.content:
        raise HTTPException(404 if r.status_code in (200, 404) else 502, f"Plex answered {r.status_code}")
    return Response(r.content, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{stream_id}.{ext}"'})
