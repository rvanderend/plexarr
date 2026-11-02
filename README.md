<p align="center"><img src="app/static/icon.svg" width="88" alt=""></p>

<h1 align="center">PlexArr</h1>

<p align="center">
  Download from the Plex servers friends share with you, through Sonarr and Radarr.<br>
  A Newznab indexer plus a SABnzbd-like download client: no tunnels, no open ports.
</p>

## How it works

- **Indexer.** PlexArr reads the libraries of the shared Plex servers (with your own Plex token, through plex.tv) and answers Newznab searches by IMDb/TMDb/TVDB id. The file name becomes the release title, so Sonarr/Radarr see source, resolution and group. If a name lacks something, PlexArr fills it in from Plex's media info (shown as "name by PlexArr").
- **Download client.** Sonarr/Radarr send the release to PlexArr as if it were SABnzbd. PlexArr downloads the original file from the friend's server (Plex's own download, resumable, optionally throttled), adds external subtitles, and puts it in the download folder. Sonarr/Radarr import it as usual.
- **Fallback first.** In *fallback* mode PlexArr only offers a release when a Usenet download failed, when the item has been missing for a while (default 24 h), or when you fetch it yourself in the web UI. An optional hourly *sweep* asks Sonarr/Radarr to search a few of those missing items. *Equal* mode offers everything.
- **Web UI.** The friends' libraries with what you already have, lack or have in lower quality; downloads with progress; an activity log.

The friend only needs to allow downloads for your account when sharing the server (Plex → Users & Sharing → Allow downloads).

## Running it

```yaml
plexarr:
  build: .
  container_name: plexarr
  user: "1000:1000"
  volumes:
    - ./data:/data
    - /path/to/downloads:/complete-downloads   # the same path Sonarr/Radarr see
  environment:
    DOWNLOAD_DIR: /complete-downloads/plexarr
```

Open the web UI on port 8080. Under **Settings**: fill in your Plex token and pick the shared servers, connect Sonarr and Radarr, then use **Link with Sonarr & Radarr**. That creates a *PlexArr* indexer and a *PlexArr* download client (SABnzbd, URL base `sabnzbd`, priority 50) in each app and links the indexer to that client. *Inactive* means interactive search only.

By hand (for example in Prowlarr): Newznab, URL `http://plexarr:8080`, API path `/newznab/api`, and the API key from the settings page.

## Notes

- Downloads go over the friend's upload. Use the speed limit if needed.
- Only direct connections to the friend's server are used; the Plex relay is too slow.
- Multi-part files (CD1/CD2) are skipped.
