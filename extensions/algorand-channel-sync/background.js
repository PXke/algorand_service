/**
 * Background: match open Firefox tabs to configured rules, snapshot via content script, POST ingest API.
 * Uses your logged-in session — not server crawl, not reading the Firefox profile on disk.
 */

const DEFAULTS = {
  enabled: true,
  apiBase: "http://localhost:8080",
  ingestKey: "",
  pollMinutes: 2,
  minChars: 40,
  rules: [],
  hashes: {},
};

function normalizeUrl(url) {
  try {
    const u = new URL(url);
    return `${u.origin}${u.pathname}`.replace(/\/$/, "");
  } catch {
    return (url || "").trim();
  }
}

function ruleMatches(rule, tabUrl) {
  const raw = (rule.matchPrefix || rule.url || "").trim();
  if (!raw) return false;
  const tab = tabUrl.trim();
  if (rule.matchRegex) {
    try {
      return new RegExp(rule.matchRegex, "i").test(tab);
    } catch {
      return false;
    }
  }
  return tab.startsWith(raw) || normalizeUrl(tab).startsWith(normalizeUrl(raw));
}

async function getSettings() {
  const stored = await browser.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

async function sha256Hex(text) {
  const buf = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function ensureApiHostPermission(apiBase) {
  let origin;
  try {
    origin = `${new URL(apiBase).origin}/*`;
  } catch {
    return false;
  }
  const has = await browser.permissions.contains({ origins: [origin] });
  if (has) return true;
  return browser.permissions.request({ origins: [origin] });
}

async function pushIngest(settings, payload) {
  const base = (settings.apiBase || "").replace(/\/$/, "");
  const key = (settings.ingestKey || "").trim();
  if (!base || !key) {
    console.warn("[channel-sync] apiBase or ingestKey not set");
    return { ok: false, reason: "not_configured" };
  }
  const allowed = await ensureApiHostPermission(base);
  if (!allowed) {
    console.warn("[channel-sync] API host permission denied", base);
    return { ok: false, reason: "permission_denied" };
  }
  const res = await fetch(`${base}/api/v1/ingest/signal`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Ingest-Key": key,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.text();
    console.error("[channel-sync] ingest failed", res.status, body);
    return { ok: false, reason: `http_${res.status}` };
  }
  return { ok: true, body: await res.json() };
}

async function snapshotTab(tab, rule, settings) {
  if (!tab.id || !tab.url) return null;
  let response;
  try {
    response = await browser.tabs.sendMessage(tab.id, { type: "SNAPSHOT" });
  } catch (err) {
    console.debug("[channel-sync] no content script on tab", tab.url, err);
    return null;
  }
  if (!response || !response.ok || !response.text) return null;

  const text = response.text.trim();
  if (text.length < (settings.minChars || 40)) {
    console.debug("[channel-sync] too short", rule.serviceId, text.length);
    return null;
  }

  const digest = await sha256Hex(text);
  const hashes = settings.hashes || {};
  if (hashes[rule.serviceId] === digest) {
    return { skipped: true, serviceId: rule.serviceId };
  }

  const payload = {
    service_id: rule.serviceId,
    display_name: rule.displayName || rule.serviceId,
    page_title: (rule.pageTitle || response.title || "Channel update").slice(0, 512),
    page_text: text.slice(0, 100000),
    source_url: (rule.sourceUrl || response.url || tab.url).slice(0, 2048),
    source_kind: "firefox_extension",
    match_kind: "tab_snapshot",
    match_value: digest.slice(0, 16),
  };

  const outcome = await pushIngest(settings, payload);
  if (outcome.ok) {
    hashes[rule.serviceId] = digest;
    await browser.storage.sync.set({ hashes });
    console.info("[channel-sync] pushed", rule.serviceId);
    return { pushed: true, serviceId: rule.serviceId };
  }
  return { error: true, serviceId: rule.serviceId };
}

async function syncOpenTabs() {
  const settings = await getSettings();
  if (!settings.enabled) return;
  const rules = settings.rules || [];
  if (!rules.length) return;

  const tabs = await browser.tabs.query({});
  const results = [];
  for (const tab of tabs) {
    for (const rule of rules) {
      if (!rule.serviceId || !ruleMatches(rule, tab.url)) continue;
      const r = await snapshotTab(tab, rule, settings);
      if ( r) results.push(r);
      break;
    }
  }
  return results;
}

browser.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "channel-sync-poll") syncOpenTabs();
});

browser.tabs.onActivated.addListener(() => {
  syncOpenTabs();
});

browser.runtime.onMessage.addListener((msg) => {
  if (msg.type === "SYNC_NOW") {
    return syncOpenTabs();
  }
});

async function reschedulePoll() {
  const settings = await getSettings();
  await browser.alarms.clear("channel-sync-poll");
  if (!settings.enabled) return;
  const minutes = Math.max(1, parseInt(settings.pollMinutes, 10) || 2);
  browser.alarms.create("channel-sync-poll", { periodInMinutes: minutes });
}

browser.storage.onChanged.addListener((changes, area) => {
  if (area === "sync") reschedulePoll();
});

browser.runtime.onInstalled.addListener(() => reschedulePoll());
browser.runtime.onStartup.addListener(() => reschedulePoll());

reschedulePoll();
