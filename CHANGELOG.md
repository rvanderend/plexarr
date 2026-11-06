# Changelog

## Unreleased

- **Download settings apply right away:** a new speed limit also applies to a running download (it used to wait for the next one), a new number of downloads at the same time starts or holds downloads at once, and a new library interval applies within 30 seconds instead of after the current wait. The three fields are aligned and each has a short explanation.
- **Reachable right after a restart:** the health check runs every 2 seconds while PlexArr starts, instead of once a minute. A reverse proxy that waits for a healthy container (such as Traefik) no longer answers "404 page not found" for up to a minute after an update.
- The time next to a finished download reads "in 5 min" and comes with the average speed ("10.6 GB · in 5 min · 39 MB/s").
- **No more flicker on Downloads:** the page no longer rebuilds itself every 3 seconds. Only what changed is replaced, and a running download only updates its numbers, so posters aren't loaded again.
- **Download history per title:** the history groups everything per movie or episode, with the poster, the video and the subtitles that came in for it, each marked as video or subtitle. A filter shows all, only videos or only subtitles. Long release names break at dots and spaces instead of in the middle of a word.
- **Downloads shows where it comes from:** each download and subtitle shows the friend's Plex server and the friend who shares it, in the queue, the current download and the history.
- **Downloads shows subtitles:** the subtitles Bazarr fetched through PlexArr appear in the history (with the movie or episode and the language), and a download lists the subtitles that came along with it.
- The settings section *Link with Sonarr & Radarr* is now called *Link with Sonarr, Radarr & Bazarr*, and its texts mention Bazarr (the address field, and what to change after a new API key).

## 0.3.0

- **Bazarr:** PlexArr answers as a Subsarr server, a subtitle provider Bazarr has built in. Bazarr searches the external subtitles (.srt, .ass) on the friends' servers by IMDb id or title; the friend's file name comes along, so Bazarr's score shows whether the subtitle fits your release. Settings → *Link with Sonarr & Radarr* shows the base URL for Bazarr, and Activity shows each search.

## 0.2.0

First public release.

- **Setup wizard:** a new install opens a wizard for the language, Plex, Sonarr/Radarr, the download folder, the link with Sonarr/Radarr, when PlexArr steps in, and an optional login. An install that already has a Plex token and servers skips it; Settings has a button to run it again.
- **Sign in with Plex:** approve PlexArr on app.plex.tv instead of looking up a Plex token, in the wizard and under Settings. Filling in a token by hand still works.
- **Download folder check:** PlexArr writes a test file in its download folder and asks Sonarr and Radarr, through their folder browser, whether they see it at the same path. If not, they can't import what PlexArr fetches.
- **Filter menus** on the Library page open a menu in the app's style with a mouse (keyboard: arrows, Home/End, Enter, Esc). Touch screens keep the native picker.
- **Activity** is translated, with clearer lines. "Sweep" is now called *Auto search*, and only searches when the quality profile would take the friend's version.
- The web app no longer starts twice in the browser (the page loaded `app.js` under two addresses).
- Docker image on `ghcr.io/rvanderend/plexarr` for amd64 and arm64.

## 0.1.0

First version: friends' Plex servers as a Newznab indexer plus a SABnzbd-like download client for Sonarr and Radarr, with a web UI for the libraries, downloads and activity.
