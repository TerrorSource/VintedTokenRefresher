#!/usr/bin/env python3
"""
Vinted token refresher for Vinted-Notifications.

Periodically calls Vinted's refresh endpoint, obtains a fresh access_token,
and writes it into the default_headers field of the Vinted-Notifications SQLite
database. The rotated refresh_token + datadome cookie are persisted to disk so
the container survives restarts.

First run: populate state.json with your refresh_token_web and datadome from the
browser (see README).
"""
import base64
import json
import os
import re
import sqlite3
import time
import traceback

# Vinted sits behind Cloudflare and DataDome, which fingerprint the TLS
# handshake and the HTTP/2 settings -- not just the headers. Python's requests
# has a fingerprint of its own and does not even speak HTTP/2, and since August
# 2026 the auth endpoint answers a bare HTTP 400 to it: the same refresh token
# that a browser uses happily. curl_cffi presents Chrome's handshake, which no
# amount of header-setting can imitate.
try:
    from curl_cffi import requests
    IMPERSONATE = os.environ.get("IMPERSONATE", "chrome")
except ImportError:                       # still runs, but Vinted will refuse
    import requests
    IMPERSONATE = None

# ---- Configuration via environment ----
DB_PATH = os.environ.get("VN_DB_PATH", "/data/vinted_notifications.db")
STATE_PATH = os.environ.get("STATE_PATH", "/state/state.json")
# Consumed by the vinted-reposter sidecar; harmless if nothing reads it.
TOKEN_PATH = os.environ.get("TOKEN_PATH", "/state/token.json")
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "3600"))  # 1 hour
LOCALE = os.environ.get("VINTED_LOCALE", "www.vinted.nl")
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
)

REFRESH_URL = f"https://{LOCALE}/web/api/auth/refresh"


def client_hints(user_agent):
    """
    Derive the Sec-Ch-Ua headers from the user agent instead of hard-coding them.

    A datadome cookie is issued against a browser fingerprint. Copy one from
    Chrome on a Mac and send it back with headers claiming Chrome on Windows and
    it is suspect from the first request -- which is how a working setup starts
    collecting CAPTCHAs. Deriving them means setting USER_AGENT is enough.
    """
    version = re.search(r"Chrome/(\d+)", user_agent)
    version = version.group(1) if version else "138"
    if "Windows" in user_agent:
        platform = "Windows"
    elif "Mac OS X" in user_agent or "Macintosh" in user_agent:
        platform = "macOS"
    elif "Android" in user_agent:
        platform = "Android"
    elif "Linux" in user_agent:
        platform = "Linux"
    else:
        platform = "Unknown"
    return {
        "Sec-Ch-Ua": f'"Not)A;Brand";v="8", "Chromium";v="{version}", '
                     f'"Google Chrome";v="{version}"',
        "Sec-Ch-Ua-Mobile": "?1" if "Mobile" in user_agent else "?0",
        "Sec-Ch-Ua-Platform": f'"{platform}"',
    }


# Base headers written alongside the access-token cookie into default_headers,
# so Vinted-Notifications scrapes with the same fingerprint this container uses.
BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    **client_hints(USER_AGENT),
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Referer": f"https://{LOCALE}/",
}


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_state():
    if not os.path.exists(STATE_PATH):
        log(f"ERROR: {STATE_PATH} does not exist. Create it with your "
            f"refresh_token_web and datadome from the browser. See README.")
        return None
    with open(STATE_PATH) as f:
        state = json.load(f)
    if not state.get("refresh_token_web"):
        log("ERROR: refresh_token_web missing in state.json")
        return None
    return state


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def save_token_file(access_token, state):
    """Publish the fresh token for other containers (e.g. vinted-reposter)."""
    payload = {
        "access_token": access_token,
        "obtained_at": int(time.time()),
        "expires_at": int(time.time()) + 7200,   # Vinted access tokens live ~2h
        "datadome": state.get("datadome"),
        "cf_clearance": state.get("cf_clearance"),
    }
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, TOKEN_PATH)


class ChainDead(RuntimeError):
    """The refresh token is gone. Only fresh browser cookies fix this."""


def token_expiry(token):
    """Vinted's tokens are plain JWTs; the refresh one lives exactly 7 days."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return None


def token_claims(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def describe_chain(state):
    """
    A word about the stored refresh token: which one it is, when it was issued,
    and how much life is left. The identity matters as much as the expiry --
    "still valid for 7 days" reads the same for a token you just pasted and for
    the one that was already there, and those need very different fixes.
    """
    token = state.get("refresh_token_web", "")
    claims = token_claims(token)
    exp, iat = claims.get("exp"), claims.get("iat")
    if not exp:
        return None
    left, age = exp - time.time(), time.time() - (iat or 0)
    life = (f"expired {abs(left) / 86400:.1f} days ago" if left <= 0
            else f"valid for another {left / 86400:.1f} days")
    return (f"token …{token[-8:]}, issued {time.strftime('%Y-%m-%d %H:%M', time.localtime(iat))} "
            f"({age / 60:.0f} min ago), {life}")


# Keys in state.json that are bookkeeping, not cookies to send along.
NON_COOKIE_KEYS = {"previous_refresh_tokens", "note", "notes", "x_csrf_token"}


def cookies_from(state):
    """
    Every string in state.json is sent as a cookie.

    Vinted decides for itself which cookies a refresh needs, and that has
    changed before. Rather than hard-code three names, paste whatever the
    browser has -- v_udt, anon_id, __cf_bm -- and it goes along without a code
    change. Only refresh_token_web is required.
    """
    return {k: v for k, v in state.items()
            if k not in NON_COOKIE_KEYS and isinstance(v, str) and v.strip()}


def do_refresh(state):
    """Call the refresh endpoint. Returns (access_token, new_state)."""
    s = requests.Session(**({"impersonate": IMPERSONATE} if IMPERSONATE else {}))
    s.headers.update({"User-Agent": USER_AGENT, **BASE_HEADERS, "Origin": f"https://{LOCALE}"})
    # Vinted's own app sends this on every call that changes something, and the
    # refresh goes through the same client. Supply it via state.json and it
    # travels as a header rather than a cookie.
    if state.get("x_csrf_token"):
        s.headers["X-CSRF-Token"] = state["x_csrf_token"]
    s.cookies.update(cookies_from(state))

    r = s.post(REFRESH_URL, timeout=20)
    if r.status_code in (400, 401):
        # Vinted answers 400 with an empty body for a refresh token it no longer
        # knows: expired, or rotated away by something else using the same one.
        # Retrying cannot fix it, so say what will.
        raise ChainDead(f"Vinted rejected the refresh token (HTTP {r.status_code}). "
                        f"{describe_chain(state) or 'no expiry readable from it'}.")
    if r.status_code != 200:
        raise RuntimeError(f"refresh returned status {r.status_code}: {r.text[:200]}")

    body = r.json()
    access_token = body.get("access_token")
    if not access_token:
        raise RuntimeError(f"no access_token in response: {r.text[:200]}")

    # Take the rotated refresh_token from body or cookie jar
    new_refresh = body.get("refresh_token") or s.cookies.get("refresh_token_web") or state["refresh_token_web"]
    new_datadome = s.cookies.get("datadome") or state.get("datadome")
    new_cf = s.cookies.get("cf_clearance") or state.get("cf_clearance")

    # Keep everything else that was pasted; only the rotated values change.
    # Harvest the jar FIRST and set the rotated token last: the jar still holds
    # the refresh token we sent, so doing it the other way round would write the
    # spent token back over the new one and kill the chain on every success.
    new_state = dict(state)
    for name, value in s.cookies.items():          # anything Vinted reissued
        if name in state and value:
            new_state[name] = value
    new_state["refresh_token_web"] = new_refresh
    # The refresh endpoint wants the access token sent along -- it identifies the
    # session, and without it Vinted answers a bare 400 however valid the refresh
    # token is. So keep the fresh one for the next round.
    new_state["access_token_web"] = access_token
    if new_datadome:
        new_state["datadome"] = new_datadome
    if new_cf:
        new_state["cf_clearance"] = new_cf
    return access_token, new_state


def write_to_db(access_token):
    """Write the access token as a Cookie header into the default_headers field."""
    if not os.path.exists(DB_PATH):
        raise RuntimeError(f"DB not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM parameters WHERE key='default_headers'")
        row = cur.fetchone()
        try:
            headers = json.loads(row[0]) if row and row[0] else {}
        except json.JSONDecodeError:
            headers = {}
        # merge our base headers (idempotent) + the fresh cookie
        headers.update(BASE_HEADERS)
        headers["Cookie"] = f"access_token_web={access_token}"
        cur.execute("UPDATE parameters SET value=? WHERE key='default_headers'",
                    (json.dumps(headers),))
        conn.commit()
    finally:
        conn.close()


def main():
    log(f"Vinted token refresher started. endpoint={REFRESH_URL} interval={REFRESH_INTERVAL}s")
    if IMPERSONATE:
        log(f"Using curl_cffi, impersonating {IMPERSONATE} -- Vinted fingerprints the TLS "
            f"handshake, so a plain HTTP client is refused whatever the token says.")
    else:
        log("WARNING: curl_cffi is not installed, falling back to plain requests. Vinted "
            "will answer 400 to that regardless of your token. Rebuild the image.")
    complained = False          # only report a dead chain once
    while True:
        try:
            state = load_state()
            if state is None:
                log("Waiting for a valid state.json; retrying in 60s.")
                time.sleep(60)
                continue
            access_token, new_state = do_refresh(state)
            save_state(new_state)
            save_token_file(access_token, new_state)
            write_to_db(access_token)
            log("OK: access token refreshed and written to DB. "
                f"{describe_chain(new_state) or 'refresh token rotated'}.")
            complained = False
        except ChainDead as e:
            # Nothing to retry: an hourly stack trace only buries the one line
            # that matters, so say it once and then keep quiet about it.
            if not complained:
                log(f"STOPPED: {e}")
                log("Paste a fresh refresh_token_web and datadome from your browser "
                    "(DevTools -> Application -> Cookies) into state.json, or enter "
                    "them under Settings in the vinted-reposter web UI if it shares "
                    "this state folder. Retrying hourly in the meantime, silently.")
                complained = True
        except Exception as e:
            log(f"ERROR during refresh: {e}")
            traceback.print_exc()
            complained = False
        time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    main()
