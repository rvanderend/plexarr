"""Process settings (environment) and the defaults for the settings stored in the database.

Everything a user configures lives in the database and is edited in the web UI. The environment
only holds where things are stored.
"""
import os
from pathlib import Path

VERSION = "0.1.1"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DATA_DIR = Path(env("DATA_DIR", "/data"))
# Where finished downloads go. Sonarr/Radarr must see this folder at the same path.
DOWNLOAD_DIR = Path(env("DOWNLOAD_DIR", "/downloads/plexarr"))
PORT = int(env("PORT", "8080"))

# Prefix of the download ids handed to Sonarr/Radarr, so PlexArr's own failures are recognisable.
NZO_PREFIX = "SABnzbd_nzo_plexarr_"

DEFAULT_SETTINGS = {
    # Plex: the account token of the person running PlexArr; the friends' servers are found through plex.tv.
    "plex_token": "",
    "servers": [],                 # clientIdentifiers of the shared Plex servers to use
    # Sonarr/Radarr
    "radarr_url": "", "radarr_api_key": "",
    "sonarr_url": "", "sonarr_api_key": "",
    # How Sonarr/Radarr reach PlexArr (for the automatic setup); empty = http://plexarr:<port>
    "self_url": "",
    # fallback: only offer a release when Usenet failed or has nothing (see eligibility.py)
    # equal: offer everything and let Sonarr/Radarr choose
    "mode": "fallback",
    "wait_hours": 24,              # fallback: missing this long before PlexArr steps in
    "sweep_enabled": False,        # fallback: ask Sonarr/Radarr to search missing items PlexArr has
    "sweep_per_run": 3,            # at most this many searches per hour (they also hit your Usenet indexers)
    "sweep_repeat_days": 7,        # search the same item again after this many days at the earliest
    "max_downloads": 1,            # at the same time
    "speed_limit": 0,              # MB/s, 0 = no limit
    "subtitles": True,             # also fetch external subtitles next to the video
    "refresh_minutes": 30,         # how often the friends' libraries are read again
    "language": "nl",
    "auth_enabled": False,
    "auth_user": "",
    "setup_done": False,           # the setup wizard was finished (an install that already works counts as done)
}
SECRET_SETTINGS = ("plex_token", "radarr_api_key", "sonarr_api_key")
