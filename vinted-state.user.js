// ==UserScript==
// @name         Vinted state.json helper
// @namespace    https://github.com/terrorsource
// @version      2.0.0
// @description  Reads the vinted.nl cookies -- including the HttpOnly ones a page cannot see -- and hands you the state.json that vinted-token-refresher and vinted-reposter expect.
// @author       -
// @match        https://www.vinted.nl/*
// @include      https://www.vinted.*/*
// @grant        GM_cookie
// @grant        GM_registerMenuCommand
// @grant        GM_setClipboard
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      *
// @run-at       document-idle
// ==/UserScript==

/*
 * 2.0.0  cleanup once the cause was found: the refresh needs no headers, so
 *        the CSRF capture and the fetch/XHR interception are gone. What it
 *        needed all along was the access_token_web cookie.
 * 1.4.0  listened in on the app's own refresh call (served its purpose).
 * 1.3.0  includes access_token_web -- the browser sends it along on a refresh
 *        and we never did.
 * 1.2.0  captures the CSRF token as x_csrf_token, and adds "Test refresh from
 *        this page" -- which answers, from inside a session Vinted accepts,
 *        whether the refresh needs that header at all.
 * 1.1.0  shows the same token fingerprint the container logs, and stamps the
 *        download name with the time (Chrome never overwrites a download, so
 *        "state.json" in Downloads is the oldest one, not the newest).
 * 1.0.0  first version.
 *
 * Why this exists
 * ---------------
 * refresh_token_web is single use: the moment your browser refreshes its
 * session, the copy you took by hand is dead. Copying by hand from DevTools
 * takes a minute or two, and that is long enough to lose the race -- which is
 * exactly what kept happening.
 *
 * This does the whole grab in one click, shows you how old the token is, and
 * can post it straight into the reposter so the container claims the chain
 * within a second instead of after a trip to nano.
 *
 * document.cookie cannot see refresh_token_web (HttpOnly), so this needs
 * Tampermonkey's GM_cookie API. A plain page script can never do this.
 */

(function () {
  "use strict";

  // The cookies that have actually mattered for a refresh. Unknown names are
  // harmless -- the refresher sends every string in state.json as a cookie --
  // but keeping the file readable beats dumping analytics cookies into it.
  const WANTED = [
    "refresh_token_web",   // the only one that is strictly required
    "access_token_web",    // expired is fine: the refresh seems to want it present
    "datadome",
    "cf_clearance",
    "v_udt",               // Vinted's device token
    "anon_id",
    "anonymous-iso-locale",
    "__cf_bm",
  ];

  const REPOSTER_URL_KEY = "reposterUrl";   // e.g. http://192.168.1.20:8095

  // ---- reading cookies -----------------------------------------------------

  function readCookies() {
    return new Promise((resolve, reject) => {
      if (typeof GM_cookie === "undefined") {
        reject(new Error(
          "GM_cookie is not available. Open the Tampermonkey dashboard, edit this " +
          "script and make sure @grant GM_cookie is present, then reload the page. " +
          "Without it the HttpOnly refresh token cannot be read at all."));
        return;
      }
      GM_cookie.list({}, (cookies, error) => {
        if (error) return reject(new Error(String(error)));
        const jar = {};
        (cookies || []).forEach(c => {
          if ((c.domain || "").includes("vinted")) jar[c.name] = c.value;
        });
        resolve(jar);
      });
    });
  }

  // ---- building the file ---------------------------------------------------

  function buildState(jar, includeEverything) {
    const names = includeEverything
      ? Object.keys(jar).sort()
      : WANTED.filter(n => jar[n]);
    const state = {};
    names.forEach(n => { if (jar[n]) state[n] = jar[n]; });
    return state;
  }

  // ---- what does this endpoint actually want? ------------------------------

  async function diagnose(msg) {
    // Only the browser can answer this: it has a session Vinted accepts. A
    // refresh rotates the token, so reload the cookies afterwards.
    msg("Refreshing from this page…");
    const r = await fetch("/web/api/auth/refresh", {
      method: "POST", credentials: "include",
    });
    msg(r.ok
      ? "The session is healthy and the token has just rotated — press Reload cookies, "
        + "then put the file in place."
      : `Vinted refused it from this page too (HTTP ${r.status}). Logging out and back `
        + "in is the next step; this session is spent.");
  }

  function jwtClaims(token) {
    try {
      const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(part + "===".slice((part.length + 3) % 4)));
    } catch (e) {
      return null;
    }
  }

  function describeToken(token) {
    if (!token) return { ok: false, text: "refresh_token_web is missing — are you logged in?" };
    const c = jwtClaims(token);
    if (!c || !c.exp) return { ok: false, text: "refresh_token_web is not a readable token" };
    const now = Date.now() / 1000;
    const ageMin = Math.round((now - (c.iat || now)) / 60);
    const daysLeft = ((c.exp - now) / 86400).toFixed(1);
    // The container logs the same last-8-characters fingerprint, so the two can
    // be compared at a glance. Downloads are the trap here: Chrome never
    // overwrites, it writes state (1).json, so "the file in Downloads" is
    // easily the first one you ever pulled.
    const fingerprint = `…${token.slice(-8)}`;
    if (c.exp < now) {
      return { ok: false, text: `token ${fingerprint} expired ${(-daysLeft)} days ago` };
    }
    return {
      ok: true,
      fingerprint,
      text: `token ${fingerprint}, issued ${ageMin} min ago, valid for another ${daysLeft} days`,
      stale: ageMin > 5,
    };
  }

  // ---- the panel -----------------------------------------------------------

  function panel(state, info) {
    document.getElementById("vsh-panel")?.remove();
    const json = JSON.stringify(state, null, 2);
    const wrap = document.createElement("div");
    wrap.id = "vsh-panel";
    wrap.innerHTML = `
      <style>
        #vsh-panel { position:fixed; inset:0; z-index:2147483647; background:rgba(0,0,0,.6);
                     display:flex; align-items:center; justify-content:center;
                     font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
        #vsh-box { background:#1c2027; color:#e8eaed; border:1px solid #2b313a; border-radius:12px;
                   padding:18px 20px; width:min(680px,92vw); max-height:88vh; overflow:auto; }
        #vsh-box h2 { margin:0 0 6px; font-size:16px; }
        #vsh-box p { margin:0 0 10px; font-size:12.5px; color:#9aa4b2; }
        #vsh-box textarea { width:100%; height:210px; background:#14171c; color:#e8eaed;
                            border:1px solid #2b313a; border-radius:8px; padding:10px;
                            font-family:ui-monospace,Menlo,monospace; font-size:11.5px; }
        #vsh-box .bar { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
        #vsh-box button { background:#1c2027; color:#e8eaed; border:1px solid #2b313a;
                          border-radius:8px; padding:7px 13px; cursor:pointer; font-size:13px; }
        #vsh-box button:hover { border-color:#3bb3c9; color:#3bb3c9; }
        #vsh-box .ok { color:#4ec98a; } #vsh-box .warn { color:#e3b341; } #vsh-box .bad { color:#f07178; }
        #vsh-box code { background:#14171c; border:1px solid #2b313a; border-radius:4px; padding:1px 5px; }
        #vsh-msg { min-height:18px; font-size:12.5px; margin-top:8px; }
      </style>
      <div id="vsh-box">
        <h2>state.json for your Vinted containers</h2>
        <p class="${info.ok ? (info.stale ? "warn" : "ok") : "bad"}">${info.text}${
          info.stale ? " — that is old enough for the browser to have rotated it; press Reload below" : ""}</p>
        <textarea readonly>${json.replace(/</g, "&lt;")}</textarea>
        <div class="bar">
          <button id="vsh-copy">Copy</button>
          <button id="vsh-save">Download state.json</button>
          <button id="vsh-reload">Reload cookies</button>
          <button id="vsh-all">Include every cookie</button>
          <button id="vsh-diagnose">Test refresh from this page</button>
          <button id="vsh-push">Send to reposter…</button>
          <button id="vsh-close">Close</button>
        </div>
        <div id="vsh-msg"></div>
        <p style="margin-top:12px">Paste this into <code>state.json</code> and restart the
           container <b>straight away</b> — the token is only yours until your browser
           refreshes again. Your user agent, for <code>USER_AGENT</code> in docker-compose:</p>
        <textarea readonly style="height:56px">${navigator.userAgent}</textarea>
      </div>`;
    document.body.appendChild(wrap);

    const msg = t => { wrap.querySelector("#vsh-msg").textContent = t; };
    wrap.querySelector("#vsh-close").onclick = () => wrap.remove();
    wrap.onclick = e => { if (e.target === wrap) wrap.remove(); };
    wrap.querySelector("#vsh-copy").onclick = () => {
      GM_setClipboard(json, "text");
      msg("Copied. Paste it, save, and restart the container now.");
    };
    wrap.querySelector("#vsh-save").onclick = () => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([json], { type: "application/json" }));
      // Chrome never overwrites a download: the second one becomes
      // "state (1).json" and the file you then grab from Downloads is the
      // oldest, not the newest. Stamping the name makes the newest obvious --
      // rename it to state.json when you put it in place.
      a.download = `state-${new Date().toTimeString().slice(0, 5).replace(":", "")}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      msg(`Saved as ${a.download} — rename it to state.json where the container reads it.`);
    };
    wrap.querySelector("#vsh-reload").onclick = () => show(false);
    wrap.querySelector("#vsh-all").onclick = () => show(true);
    wrap.querySelector("#vsh-diagnose").onclick = () => diagnose(msg);
    wrap.querySelector("#vsh-push").onclick = () => push(state, msg);
  }

  // ---- optional: straight into the reposter --------------------------------

  function push(state, msg) {
    let url = GM_getValue(REPOSTER_URL_KEY, "");
    url = prompt(
      "Address of the vinted-reposter web UI, e.g. http://192.168.1.20:8095\n\n" +
      "The cookies are posted to it and it refreshes the token immediately, which " +
      "closes the window where your browser can rotate the token away.", url);
    if (!url) return;
    url = url.replace(/\/+$/, "");
    GM_setValue(REPOSTER_URL_KEY, url);
    msg("Sending…");
    GM_xmlhttpRequest({
      method: "POST",
      url: url + "/api/credentials",
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify({ ...state, refresh: true }),
      onload: r => {
        let body = {};
        try { body = JSON.parse(r.responseText); } catch (e) { /* keep the raw text */ }
        if (r.status === 401) return msg("The reposter asked for a login. Open it in a tab, "
                                       + "sign in once, then try again.");
        if (r.status >= 300) return msg(`The reposter refused it (HTTP ${r.status}): `
                                      + (body.error || r.responseText || "").slice(0, 160));
        msg(body.refreshed
          ? `Stored and refreshed — the token is valid for ${Math.round((body.expires_in || 0) / 60)} min.`
          : `Stored: ${(body.changed || []).join(", ") || "nothing changed"}.`);
      },
      onerror: () => msg("Could not reach that address. Is the reposter running, and is the "
                       + "URL right? Tampermonkey may also ask you to allow the connection."),
    });
  }

  // ---- entry point ---------------------------------------------------------

  async function show(includeEverything) {
    try {
      const jar = await readCookies();
      const state = buildState(jar, includeEverything);
      panel(state, describeToken(state.refresh_token_web));
    } catch (e) {
      alert("Vinted state.json helper\n\n" + e.message);
    }
  }

  GM_registerMenuCommand("Get state.json for the containers", () => show(false));
  GM_registerMenuCommand("Get state.json (every cookie)", () => show(true));
})();
