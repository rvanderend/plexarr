# Contributing

Thanks for helping to make PlexArr better. PlexArr is developed in this repository only.

## Ideas and bugs

Open an issue. For a bug, add what you did, what you expected, what happened, and the relevant part of `docker logs plexarr` (remove tokens, API keys and addresses you don't want to share). The Activity page often shows why PlexArr did or didn't offer something.

## Pull requests

Pull requests are welcome. PlexArr is licensed under the [PolyForm Strict License 1.0.0](LICENSE), which does not allow changes in general. As an additional permission, you may make changes to the software **for the sole purpose of submitting them as a pull request to this repository**. By opening a pull request you agree that your contribution may be used, changed and distributed by the licensor as part of PlexArr, under the license of the project.

A few guidelines:

- Keep the code in the style of the surrounding code; the web UI has no build step and no dependencies.
- Every text in the UI goes through `t()` and exists in both languages in `app/static/i18n.js`.
- Test a change with a real Sonarr or Radarr and a shared Plex server before you open the pull request, and describe how you tested it.
- Be careful with the friend's server: PlexArr should never fetch more than Sonarr/Radarr asked for, and the fallback mode should keep Usenet first.
