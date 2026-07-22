# Vinted Token Refresher

Keeps the `access_token_web` of your [Vinted-Notifications](https://github.com/Fuyucch1/Vinted-Notifications)
instance fresh automatically, so it can run 24/7 without manually pasting cookies.

## The problem this solves

Vinted's catalog API sits behind Cloudflare and requires a valid Bearer access token.
Vinted-Notifications cannot obtain a logged-in token on its own (its built-in flow only
gets an anonymous session), and a manually pasted `access_token_web` expires after ~2 hours.
That makes unattended 24/7 operation impossible on its own.

## How it works

Every hour this small container calls `POST https://www.vinted.nl/web/api/auth/refresh`
using your `refresh_token_web` cookie. Vinted returns a fresh access token (valid 2h) plus
a **rotated** refresh token. The access token is written into the `default_headers` field of
the Vinted-Notifications database (as `Cookie: access_token_web=...`); the new refresh token
and datadome cookie are persisted to `state/state.json`.

Because the refresh token rotates on every call and lives for weeks, the chain keeps going
indefinitely as long as the container runs at least once every few days.

No proxies, no browser engine, one small sidecar container.

## Requirements

- A running Vinted-Notifications container (the [ddallabenetta](https://github.com/ddallabenetta/Vinted-Notifications)
  fork or Fuyucch1 upstream), with its data volume accessible.
- You must have configured a matching browser fingerprint in the Vinted-Notifications UI
  under **Advanced Settings** (a real Chrome `User Agents` entry + `Default Headers`). This
  refresher writes into that same `Default Headers` field.

## One-time setup

1. Create the state folder on your host, e.g.:
   `/share/CACHEDEV1_DATA/Docker/vinted-token-refresher/state/`

2. In your browser (logged in on vinted.nl), open DevTools -> Application -> Cookies and copy:
   - `refresh_token_web`  (the long one with `purpose: refresh` — NOT `access_token_web`)
   - `datadome`
   - `cf_clearance` (optional, helps with Cloudflare)

3. Create `state/state.json` in that folder, based on `state.example.json`:
   ```json
   {
     "refresh_token_web": "PASTE_YOUR_REFRESH_TOKEN_WEB_HERE",
     "datadome": "PASTE_YOUR_DATADOME_COOKIE_HERE",
     "cf_clearance": "PASTE_YOUR_CF_CLEARANCE_COOKIE_HERE_OPTIONAL"
   }
   ```

4. Edit `docker-compose.yml` so the **first** volume points to the SAME data folder as your
   vinted-notifications container (the one containing `vinted_notifications.db`).

## Image

A prebuilt image is published to GitHub Container Registry automatically on every push
(via the included GitHub Actions workflow):

```
ghcr.io/terrorsource/vintedtokenrefresher:latest
```

No local build needed. If you fork this repo, the workflow publishes under your own
username; update the `image:` line in `docker-compose.yml` accordingly. Make sure the
package is set to **public** in GitHub (Packages -> the package -> Package settings ->
Change visibility) if you want Portainer to pull it without registry credentials.

## Deploy in Portainer

The compose pulls a ready-made image, so use Portainer's **Web editor** method:

1. Portainer -> Stacks -> **Add stack** -> name it e.g. `vinted-token-refresher`.
2. Build method: **Web editor**.
3. Paste the contents of `docker-compose.yml`, adjusting the volume host paths to match
   your setup.
4. Deploy the stack. Portainer pulls the image and starts it.
5. Check the container logs. You want to see:
   `OK: access token refreshed and written to DB.`

6. Restart your **vinted-notifications** container once so it picks up the new headers from
   the DB. (Not needed afterwards; the app reads the DB on every scrape.)

(You can also use the **Repository** method pointing at this repo with compose path
`docker-compose.yml` — since the compose references a registry image, no Dockerfile build
happens either way.)

## Environment variables

| Variable           | Default                          | Meaning                                  |
|--------------------|----------------------------------|------------------------------------------|
| `REFRESH_INTERVAL` | `3600`                           | Seconds between refreshes (2h token life)|
| `VINTED_LOCALE`    | `www.vinted.nl`                  | Vinted domain to refresh against         |
| `VN_DB_PATH`       | `/data/vinted_notifications.db`  | Path to the Vinted-Notifications DB      |
| `STATE_PATH`       | `/state/state.json`              | Path to the persisted state file         |
| `USER_AGENT`       | (Chrome 138 on Windows)          | Must match your Sec-Ch-Ua in refresh.py  |

## Notes & troubleshooting

- Keep the Chrome version in `USER_AGENT` and in the `Sec-Ch-Ua` header (in `refresh.py`)
  in sync, and ideally matching the fingerprint set in the Vinted-Notifications UI.
- If refresh stops working (e.g. after a password change, or after the container was off
  long enough for the refresh token to expire), the refresh token is dead: repeat steps 2-3
  with fresh cookies from the browser.
- **Never commit your real `state.json`.** The included `.gitignore` keeps `state/` and
  `state.json` out of Git. Only `state.example.json` (with placeholders) is tracked.

## Security

`state.json` contains credentials that grant access to your Vinted account. Treat it like a
password. It stays on your host (mounted as a volume) and is never part of the image or repo.

## Disclaimer

This project automates the same token refresh a normal browser performs. Use it in accordance
with Vinted's Terms of Service. Provided as-is, no warranty.
