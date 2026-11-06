# Changelog

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
