"""Downloads files from a friend's Plex server: resumable, optionally throttled, with external subtitles."""
import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import time
from pathlib import Path

import httpx

from . import config, db, plex
from .plex import LIB

log = logging.getLogger("plexarr.download")
CHUNK = 1 << 20
MAX_ATTEMPTS = 8
SUB_EXT = {"srt": "srt", "ass": "ass", "ssa": "ssa", "vtt": "vtt", "smi": "smi", "webvtt": "vtt"}


class Cancelled(Exception):
    pass


class Downloader:
    def __init__(self):
        self.live: dict[str, dict] = {}       # nzo_id -> {done, speed, started}
        self.cancel: set[str] = set()
        self.wake = asyncio.Event()
        self.tasks: dict[str, asyncio.Task] = {}

    @property
    def paused(self) -> bool:
        return bool(db.get_state("paused", False))

    def set_paused(self, value: bool):
        db.set_state("paused", value)
        self.wake.set()

    # --- jobs ----------------------------------------------------------------------------------
    def add(self, server: str, part_id: int, name: str, category: str) -> str:
        r = db.q1("SELECT * FROM releases WHERE server=? AND part_id=?", (server, part_id))
        if not r:
            raise LookupError("This file is no longer on the Plex server")
        label, sub = labels(r)
        nzo = config.NZO_PREFIX + secrets.token_hex(6)
        db.x("INSERT INTO jobs(nzo_id, server, part_id, name, category, kind, label, sublabel, rating_key, show_key, "
             "size, status, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (nzo, server, part_id, safe_name(name), category or "*", r["kind"], label, sub, r["rating_key"],
              r["show_key"], r["size"], "Queued", time.time()))
        what = f"{label}{' ' + sub if sub else ''}"
        db.event("grab", f"{what}: {name}", what=what, name=name)
        self.wake.set()
        return nzo

    def remove(self, nzo: str, delete_files: bool):
        job = db.q1("SELECT * FROM jobs WHERE nzo_id=?", (nzo,))
        if not job:
            return
        if job["status"] in ("Queued", "Downloading", "Paused"):
            self.cancel.add(nzo)
            shutil.rmtree(self.tmp_dir(nzo), ignore_errors=True)
            db.x("UPDATE jobs SET status='Deleted', removed=1, finished=? WHERE nzo_id=?", (time.time(), nzo))
        else:
            if delete_files and job["storage"]:
                _rm_inside(Path(job["storage"]))
            db.x("UPDATE jobs SET removed=1 WHERE nzo_id=?", (nzo,))

    def retry(self, nzo: str) -> bool:
        cur = db.x("UPDATE jobs SET status='Queued', attempts=0, fail_message='', removed=0 "
                   "WHERE nzo_id=? AND status IN ('Failed', 'Deleted')", (nzo,))
        self.wake.set()
        return cur.rowcount > 0

    def pause_job(self, nzo: str, pause: bool):
        if pause:
            db.x("UPDATE jobs SET status='Paused' WHERE nzo_id=? AND status IN ('Queued', 'Downloading')", (nzo,))
            self.cancel.add(nzo)
        else:
            db.x("UPDATE jobs SET status='Queued' WHERE nzo_id=? AND status='Paused'", (nzo,))
        self.wake.set()

    @staticmethod
    def tmp_dir(nzo: str) -> Path:
        return config.DOWNLOAD_DIR / ".incomplete" / nzo

    # --- worker --------------------------------------------------------------------------------
    async def run(self):
        while True:
            self.wake.clear()
            for nzo, t in list(self.tasks.items()):
                if t.done():
                    del self.tasks[nzo]
            if not self.paused:
                free = db.get_settings()["max_downloads"] - len(self.tasks)
                if free > 0:
                    busy = list(self.tasks) or [""]
                    for job in db.q(f"SELECT * FROM jobs WHERE status='Queued' AND nzo_id NOT IN "
                                    f"({','.join('?' * len(busy))}) ORDER BY created LIMIT ?", (*busy, free)):
                        self.cancel.discard(job["nzo_id"])
                        self.tasks[job["nzo_id"]] = asyncio.create_task(self.download(job["nzo_id"]))
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    async def download(self, nzo: str):
        job = db.q1("SELECT * FROM jobs WHERE nzo_id=?", (nzo,))
        try:
            await self._download(job)
        except Cancelled:
            log.info("stopped %s", job["name"])
        except Exception as e:  # noqa: BLE001 - every failure ends up in the job
            log.exception("download %s", job["name"])
            self._fail(nzo, str(e) or type(e).__name__)
        finally:
            self.live.pop(nzo, None)
            self.wake.set()

    def _fail(self, nzo: str, msg: str):
        job = db.q1("SELECT label, sublabel FROM jobs WHERE nzo_id=?", (nzo,))
        db.x("UPDATE jobs SET status='Failed', fail_message=?, finished=? WHERE nzo_id=?", (msg[:500], time.time(), nzo))
        if job:
            what = f"{job['label']} {job['sublabel']}".strip()
            db.event("failed", f"{what}: {msg}", what=what, msg=msg)

    async def _download(self, job):
        nzo = job["nzo_id"]
        r = db.q1("SELECT * FROM releases WHERE server=? AND part_id=?", (job["server"], job["part_id"]))
        if not r:
            raise LookupError("This file is no longer on the Plex server")
        tmp = self.tmp_dir(nzo)
        tmp.mkdir(parents=True, exist_ok=True)
        ext = os.path.splitext(r["file"] or "")[1] or "." + (r["container"] or "mkv")
        target = tmp / (job["name"] + ext)
        size = job["size"]

        need = size - (target.stat().st_size if target.exists() else 0) + (2 << 30)
        if shutil.disk_usage(config.DOWNLOAD_DIR).free < need:
            raise OSError("Not enough free space in the download folder")

        db.x("UPDATE jobs SET status='Downloading', started=COALESCE(started, ?), fail_message='' WHERE nzo_id=?",
             (time.time(), nzo))
        attempts = job["attempts"]
        while True:
            try:
                await self._fetch(nzo, job["server"], r["part_key"], target, size)
                break
            except (httpx.HTTPError, ConnectionError, OSError) as e:
                if isinstance(e, PermissionError) or getattr(e, "errno", None) == 28:   # refused / disk full
                    raise
                attempts += 1
                db.x("UPDATE jobs SET attempts=?, fail_message=? WHERE nzo_id=?",
                     (attempts, f"Retrying: {e}"[:300], nzo))
                if attempts >= MAX_ATTEMPTS:
                    raise
                log.warning("%s: %s, retry %d", job["name"], e, attempts)
                await self._sleep(nzo, min(30 * attempts, 600))
        got = target.stat().st_size
        if got != size:
            target.unlink(missing_ok=True)
            raise OSError(f"Size does not match ({got} instead of {size} bytes)")

        if db.get_settings()["subtitles"]:
            try:
                subs = await self._subtitles(job, target)
                db.x("UPDATE jobs SET subs=? WHERE nzo_id=?", (json.dumps(subs) if subs else None, nzo))
            except Exception as e:  # noqa: BLE001 - subtitles are a bonus
                log.warning("subtitles %s: %s", job["name"], e)

        final = config.DOWNLOAD_DIR / safe_name(job["category"] if job["category"] != "*" else "default") / job["name"]
        if final.exists():
            final = final.with_name(f"{final.name}.{nzo[-6:]}")
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, final)
        took = time.time() - (job["started"] or time.time())
        db.x("UPDATE jobs SET status='Completed', done=?, storage=?, finished=?, fail_message='' WHERE nzo_id=?",
             (size, str(final), time.time(), nzo))
        what = f"{job['label']} {job['sublabel']}".strip()
        db.event("done", f"{what} ({size / 1e9:.1f} GB)", what=what, size=size)
        log.info("done %s in %.0fs", job["name"], took)

    async def _sleep(self, nzo: str, seconds: float):
        end = time.time() + seconds
        while time.time() < end:
            if nzo in self.cancel:
                raise Cancelled()
            await asyncio.sleep(1)

    async def _fetch(self, nzo: str, server: str, part_key: str, target: Path, size: int):
        srv = await LIB.server(server)
        base = await srv.connect()
        done = target.stat().st_size if target.exists() else 0
        if done >= size:
            if done > size:
                target.unlink()
                done = 0
            else:
                return
        hdr = {**plex.headers(srv.token), "Accept": "*/*"}
        if done:
            hdr["Range"] = f"bytes={done}-"
        live = self.live.setdefault(nzo, {"done": done, "speed": 0.0})
        live["done"] = done
        limit = db.get_settings()["speed_limit"] * 1e6
        timeout = httpx.Timeout(30, read=120)
        async with httpx.AsyncClient(timeout=timeout) as c:
            async with c.stream("GET", base + part_key, params={"download": 1}, headers=hdr) as resp:
                if resp.status_code == 200 and done:
                    done = 0                      # the server ignored the range: start over
                elif resp.status_code not in (200, 206):
                    if resp.status_code in (401, 403):
                        raise PermissionError(f"Plex refused the download ({resp.status_code}); are downloads still allowed?")
                    raise httpx.HTTPStatusError(f"Plex answered {resp.status_code}", request=resp.request, response=resp)
                mode = "ab" if done else "wb"
                with open(target, mode) as f:
                    t0, window_bytes, last_db = time.monotonic(), 0, 0.0
                    async for chunk in resp.aiter_bytes(CHUNK):
                        if nzo in self.cancel:
                            raise Cancelled()
                        f.write(chunk)
                        done += len(chunk)
                        window_bytes += len(chunk)
                        now = time.monotonic()
                        if limit:
                            ahead = window_bytes / limit - (now - t0)
                            if ahead > 0:
                                await asyncio.sleep(ahead)
                                now = time.monotonic()
                        if now - t0 >= 2:
                            live["speed"] = window_bytes / (now - t0)
                            t0, window_bytes = now, 0
                        live["done"] = done
                        if now - last_db > 5:
                            db.x("UPDATE jobs SET done=? WHERE nzo_id=?", (done, nzo))
                            last_db = now
        db.x("UPDATE jobs SET done=? WHERE nzo_id=?", (done, nzo))

    async def _subtitles(self, job, target: Path) -> list[dict]:
        """Saves the external subtitles next to the video; returns what was saved."""
        srv = await LIB.server(job["server"])
        md = (await srv.get(f"/library/metadata/{job['rating_key']}")).get("Metadata", [{}])[0]
        part = next((p for m in md.get("Media", []) for p in m.get("Part", []) if p.get("id") == job["part_id"]), None)
        if not part:
            return []
        seen, saved = set(), []
        async with httpx.AsyncClient(timeout=60) as c:
            for s in part.get("Stream", []):
                ext = SUB_EXT.get((s.get("codec") or "").lower())
                if s.get("streamType") != 3 or not s.get("key") or not ext:
                    continue
                lang = s.get("languageTag") or s.get("languageCode") or "und"
                name = f"{target.stem}.{lang}{'.forced' if s.get('forced') else ''}"
                n, i = name, 2
                while n in seen:
                    n, i = f"{name}.{i}", i + 1
                seen.add(n)
                resp = await c.get(srv.url(s["key"]), headers=plex.headers(srv.token))
                if resp.status_code == 200 and resp.content:
                    (target.parent / f"{n}.{ext}").write_bytes(resp.content)
                    saved.append({"lang": lang, "forced": bool(s.get("forced"))})
        return saved

    # --- for the web UI and the SABnzbd API ------------------------------------------------------
    def progress(self, job) -> dict:
        live = self.live.get(job["nzo_id"], {})
        done = live.get("done", job["done"] or 0)
        speed = live.get("speed", 0.0)
        left = max(0, job["size"] - done)
        return {"done": done, "speed": speed, "left": left, "eta": (left / speed) if speed > 0 else None}


def labels(r) -> tuple[str, str]:
    """(label, sublabel) of an index row: "Title (year)" for a movie, the show and "S01E02 · title" for an episode."""
    if r["kind"] == "movie":
        return (f"{r['title']} ({r['year']})" if r["year"] else r["title"]), ""
    return r["title"], f"S{r['season']:02d}E{r['episode']:02d} · {r['episode_title'] or ''}".rstrip(" ·")


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name)
    return re.sub(r"\s+", " ", name).strip(" .")[:200] or "download"


def _rm_inside(path: Path):
    """Deletes a finished download, but only inside the download folder."""
    try:
        path.resolve().relative_to(config.DOWNLOAD_DIR.resolve())
    except ValueError:
        log.warning("not deleting %s: outside %s", path, config.DOWNLOAD_DIR)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink()


DL = Downloader()
