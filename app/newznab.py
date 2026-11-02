"""Newznab API for Sonarr, Radarr and Prowlarr: the friends' files as search results and an RSS feed."""
import hmac
import logging
from email.utils import formatdate
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import auth, config, db, releases

log = logging.getLogger("plexarr.newznab")
router = APIRouter()
RSS_LIMIT = 100

CAPS = f"""<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server version="{config.VERSION}" title="PlexArr" strapline="Files from friends' Plex servers"/>
  <limits max="{RSS_LIMIT}" default="{RSS_LIMIT}"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep,tvdbid,imdbid"/>
    <movie-search available="yes" supportedParams="q,imdbid,tmdbid"/>
  </searching>
  <categories>
    <category id="2000" name="Movies">
      <subcat id="2030" name="SD"/><subcat id="2040" name="HD"/><subcat id="2045" name="UHD"/>
    </category>
    <category id="5000" name="TV">
      <subcat id="5030" name="SD"/><subcat id="5040" name="HD"/><subcat id="5045" name="UHD"/>
    </category>
  </categories>
</caps>"""


def _xml(body: str, status=200) -> Response:
    return Response(body, status_code=status, media_type="application/xml")


def _error(code: int, text: str) -> Response:
    return _xml(f'<?xml version="1.0" encoding="UTF-8"?>\n<error code="{code}" description={quoteattr(text)}/>')


def _key_ok(request: Request) -> bool:
    key = request.query_params.get("apikey") or request.headers.get("x-api-key") or ""
    return bool(key) and hmac.compare_digest(key, auth.api_key())


def _ids(v: str | None) -> str | None:
    v = (v or "").strip()
    return v if v and v not in ("0",) else None


def _find(t: str, p) -> tuple[list, bool]:
    """Rows for a request, and whether it is a search for something specific (not the RSS feed)."""
    imdb, tmdb, tvdb = _ids(p.get("imdbid")), _ids(p.get("tmdbid")), _ids(p.get("tvdbid"))
    q = (p.get("q") or "").strip()
    if imdb and not imdb.startswith("tt"):
        imdb = "tt" + imdb.zfill(7)
    if t == "movie" and (imdb or tmdb):
        conds, args = [], []
        if tmdb and tmdb.isdigit():
            conds.append("tmdb=?"); args.append(int(tmdb))
        if imdb:
            conds.append("imdb=?"); args.append(imdb)
        return db.q(f"SELECT * FROM releases WHERE kind='movie' AND ({' OR '.join(conds)})", args), True
    if t in ("tvsearch", "tv") and (tvdb or imdb or q):
        where, args = ["kind='episode'"], []
        if tvdb and tvdb.isdigit():
            where.append("tvdb=?"); args.append(int(tvdb))
        elif imdb:
            where.append("imdb=?"); args.append(imdb)
        else:
            where.append("title LIKE ?"); args.append(f"%{q}%")
        season, ep = (p.get("season") or "").strip(), (p.get("ep") or "").strip()
        if season.isdigit():
            where.append("season=?"); args.append(int(season))
        if ep.isdigit():
            where.append("episode=?"); args.append(int(ep))
        return db.q(f"SELECT * FROM releases WHERE {' AND '.join(where)} ORDER BY season, episode", args), True
    if q:
        kind = {"movie": "AND kind='movie'", "tvsearch": "AND kind='episode'", "tv": "AND kind='episode'"}.get(t, "")
        like = "%" + "%".join(q.split()) + "%"
        return db.q(f"SELECT * FROM releases WHERE (title LIKE ? OR original_title LIKE ?) {kind} "
                    "ORDER BY added_at DESC LIMIT 200", (like, like)), True
    kind = {"movie": "WHERE kind='movie'", "tvsearch": "WHERE kind='episode'", "tv": "WHERE kind='episode'"}.get(t, "")
    return db.q(f"SELECT * FROM releases {kind} ORDER BY added_at DESC LIMIT {RSS_LIMIT * 3}"), False


def _cat_filter(rows, cat: str | None):
    if not cat:
        return rows
    wanted = {c.strip() for c in cat.split(",") if c.strip().isdigit()}
    if not wanted:
        return rows
    return [r for r in rows if str(releases.category(r)) in wanted or str(releases.category(r) // 1000 * 1000) in wanted]


def _label(r) -> str:
    if r["kind"] == "movie":
        return f"{r['title']} ({r['year']})" if r["year"] else r["title"]
    return f"{r['title']} S{r['season']:02d}E{r['episode']:02d}"


async def _item(base: str, key: str, r) -> str:
    title, _ = await releases.title(r)
    cat = releases.category(r)
    link = f"{base}/newznab/nzb/{r['server']}/{r['part_id']}?apikey={quote(key)}"
    attrs = {"category": [cat // 1000 * 1000, cat], "size": [r["size"]]}
    if r["imdb"]:
        attrs["imdb"] = [r["imdb"][2:]]
    if r["tmdb"]:
        attrs["tmdbid"] = [r["tmdb"]]
    if r["tvdb"] and r["kind"] == "episode":
        attrs.update({"tvdbid": [r["tvdb"]], "season": [r["season"]], "episode": [r["episode"]]})
    date = formatdate(r["added_at"] or 0, usegmt=True)
    attrs["usenetdate"] = [date]
    attr_xml = "".join(f'<newznab:attr name="{k}" value={quoteattr(str(v))}/>' for k, vs in attrs.items() for v in vs)
    return (f"<item><title>{escape(title)}</title><guid isPermaLink=\"false\">plexarr-{r['server']}-{r['part_id']}</guid>"
            f"<link>{escape(link)}</link><pubDate>{date}</pubDate><category>{cat}</category>"
            f"<enclosure url={quoteattr(link)} length=\"{r['size']}\" type=\"application/x-nzb\"/>{attr_xml}</item>")


@router.get("/newznab/api")
@router.get("/newznab/api/")
async def api(request: Request):
    p = request.query_params
    t = (p.get("t") or "").lower()
    if t == "caps":
        return _xml(CAPS)
    if not _key_ok(request):
        return _error(100, "Incorrect user credentials")
    if t not in ("search", "movie", "tvsearch", "tv"):
        return _error(202, f"No such function ({t})")
    rows, specific = _find(t, p)
    rows = _cat_filter(rows, p.get("cat"))
    offset = int(p.get("offset") or 0) if (p.get("offset") or "0").isdigit() else 0
    limit = min(int(p.get("limit") or RSS_LIMIT) if (p.get("limit") or "").isdigit() else RSS_LIMIT, RSS_LIMIT)

    offered, held = [], {}
    for r in rows:
        ok, why = await releases.eligible(r)
        if ok:
            offered.append(r)
        else:
            held[why] = held.get(why, 0) + 1
    if specific and rows:
        names = sorted({_label(r) for r in rows})
        what = names[0] if len(names) == 1 else f"{names[0]} (+{len(names) - 1})"
        db.event("search", f"{what}: {len(offered)} offered" + (f", held back: {held}" if held else ""))
    page = offered[offset:offset + limit]

    base = str(request.base_url).rstrip("/")
    key = p.get("apikey") or auth.api_key()
    items = [await _item(base, key, r) for r in page]
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">'
            f'<channel><title>PlexArr</title><description>Files from friends\' Plex servers</description>'
            f'<newznab:response offset="{offset}" total="{len(offered)}"/>' + "".join(items) + "</channel></rss>")
    return _xml(body)


@router.get("/newznab/nzb/{server}/{part_id}")
async def nzb(server: str, part_id: int, request: Request):
    if not _key_ok(request):
        return _error(100, "Incorrect user credentials")
    r = db.q1("SELECT * FROM releases WHERE server=? AND part_id=?", (server, part_id))
    if not r:
        return _error(300, "This file is no longer on the Plex server")
    title, _ = await releases.title(r)
    return Response(make_nzb(title, r), media_type="application/x-nzb",
                    headers={"Content-Disposition": f'attachment; filename="{quote(title)}.nzb"'})


def make_nzb(title: str, r) -> str:
    """A valid NZB that only carries what PlexArr needs; a real Usenet client can't do anything with it."""
    t = escape(title)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head>
    <meta type="name">{t}</meta>
    <meta type="plexarr-server">{escape(r['server'])}</meta>
    <meta type="plexarr-part">{r['part_id']}</meta>
  </head>
  <file poster="PlexArr" date="{r['added_at'] or 0}" subject={quoteattr(title)}>
    <groups><group>alt.binaries.plexarr</group></groups>
    <segments><segment bytes="{r['size']}" number="1">plexarr-{escape(r['server'])}-{r['part_id']}@plexarr</segment></segments>
  </file>
</nzb>
"""
