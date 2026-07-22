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
import json
import os
import sqlite3
import time
import traceback

import requests

# ---- Configuration via environment ----
DB_PATH = os.environ.get("VN_DB_PATH", "/data/vinted_notifications.db")
STATE_PATH = os.environ.get("STATE_PATH", "/state/state.json")
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "3600"))  # 1 hour
LOCALE = os.environ.get("VINTED_LOCALE", "www.vinted.nl")
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
)

REFRESH_URL = f"https://{LOCALE}/web/api/auth/refresh"

# Base headers written alongside the access-token cookie into default_headers.
# These match the fingerprint; keep the Chrome version here in sync with USER_AGENT.
BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
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


def do_refresh(state):
    """Call the refresh endpoint. Returns (access_token, new_state)."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, **BASE_HEADERS, "Origin": f"https://{LOCALE}"})
    cookies = {"refresh_token_web": state["refresh_token_web"]}
    if state.get("datadome"):
        cookies["datadome"] = state["datadome"]
    if state.get("cf_clearance"):
        cookies["cf_clearance"] = state["cf_clearance"]
    s.cookies.update(cookies)

    r = s.post(REFRESH_URL, timeout=20)
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

    new_state = {
        "refresh_token_web": new_refresh,
        "datadome": new_datadome,
        "cf_clearance": new_cf,
    }
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
    while True:
        try:
            state = load_state()
            if state is None:
                log("Waiting for a valid state.json; retrying in 60s.")
                time.sleep(60)
                continue
            access_token, new_state = do_refresh(state)
            save_state(new_state)
            write_to_db(access_token)
            log("OK: access token refreshed and written to DB. "
                "refresh_token + datadome rotated and persisted.")
        except Exception as e:
            log(f"ERROR during refresh: {e}")
            traceback.print_exc()
        time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    main()
