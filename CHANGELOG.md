# Changelog

## Unreleased

- Activity shows one line per Bazarr subtitle search. Bazarr asks again by title when the IMDb search finds nothing, so each search used to appear twice.

## 0.4.0

**Downloads**
- **History per title:** one block per movie or episode, with the poster, the video and the subtitles that came in for it, each marked as video or subtitle. A filter shows all, only videos or only subtitles.
- **Subtitles from Bazarr show up:** the subtitles Bazarr fetched through PlexArr appear in the history with their language, and a download lists the subtitles that came along with it.
- **Where it comes from:** each download and subtitle shows the friend's Plex server and the friend who shares it, in the queue, the current download and the history.
- **How long it took:** a finished download reads "10.6 GB · in 5 min · 39 MB/s".
- **The speed limit shows:** the speed card says "limit 5 MB/s" (a link to the setting) and a running download is marked "Limited to 5 MB/s", updated live.
- No more flicker: the page only replaces what changed, and a running download only updates its numbers. Long release names break at dots and spaces instead of in the middle of a word.

**Speed limit and settings**
- **The speed limit works properly:** it is for all downloads together (it was per download, so two downloads used twice the limit), allows decimals like 0.5 (a value below 1 used to mean no limit at all), keeps an even pace, and a change applies within 2 seconds, also to a running download. Measured: 4.94 MB/s for two downloads at a limit of 5, 0.50 at a limit of 0.5.
- A new number of downloads at the same time applies at once, and a new library interval within 30 seconds. The download settings line up and each has a short explanation.
- The settings section *Link with Sonarr & Radarr* is now *Link with Sonarr, Radarr & Bazarr*.

**Reliability**
- **Reachable right after an update:** the health check runs every 2 seconds while PlexArr starts, instead of once a minute, so a reverse proxy that waits for a healthy container (such as Traefik) no longer answers "404 page not found" for up to a minute.
- Interrupted downloads are put back in the queue only when PlexArr starts, so another process opening the database can't make a running download look queued.

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
