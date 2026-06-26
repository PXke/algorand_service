const $ = (id) => document.getElementById(id);

function ruleTemplate(rule = {}) {
  const div = document.createElement("div");
  div.className = "rule";
  div.innerHTML = `
    <label>URL prefix (must match tab when you visit the channel)</label>
    <input type="text" class="matchPrefix" value="${rule.matchPrefix || ""}" placeholder="https://discord.com/channels/GUILD/CHANNEL" />
    <label>Service ID (platform registry)</label>
    <input type="text" class="serviceId" value="${rule.serviceId || ""}" />
    <label>Display name</label>
    <input type="text" class="displayName" value="${rule.displayName || ""}" />
    <label>Page title override (optional)</label>
    <input type="text" class="pageTitle" value="${rule.pageTitle || ""}" />
    <button type="button" class="remove">Remove</button>
  `;
  div.querySelector(".remove").addEventListener("click", () => div.remove());
  return div;
}

function collectRules() {
  return [...document.querySelectorAll(".rule")].map((div) => ({
    matchPrefix: div.querySelector(".matchPrefix").value.trim(),
    serviceId: div.querySelector(".serviceId").value.trim(),
    displayName: div.querySelector(".displayName").value.trim(),
    pageTitle: div.querySelector(".pageTitle").value.trim(),
  }));
}

async function load() {
  const stored = await browser.storage.sync.get({
    enabled: true,
    apiBase: "http://localhost:8080",
    ingestKey: "",
    pollMinutes: 2,
    rules: [],
  });
  $("enabled").checked = stored.enabled !== false;
  $("apiBase").value = stored.apiBase || "";
  $("ingestKey").value = stored.ingestKey || "";
  $("pollMinutes").value = stored.pollMinutes || 2;
  const container = $("rules");
  container.innerHTML = "";
  for (const rule of stored.rules || []) {
    container.appendChild(ruleTemplate(rule));
  }
  if (!(stored.rules || []).length) {
    container.appendChild(
      ruleTemplate({
        matchPrefix: "https://discord.com/channels/",
        serviceId: "algorand-foundation-discord",
        displayName: "Algorand Foundation (Discord)",
      })
    );
  }
}

async function save() {
  const data = {
    enabled: $("enabled").checked,
    apiBase: $("apiBase").value.trim(),
    ingestKey: $("ingestKey").value.trim(),
    pollMinutes: parseInt($("pollMinutes").value, 10) || 2,
    rules: collectRules().filter((r) => r.matchPrefix && r.serviceId),
  };
  await browser.storage.sync.set(data);
  $("status").textContent = "Saved.";
}

$("addRule").addEventListener("click", () => {
  $("rules").appendChild(ruleTemplate());
});
$("save").addEventListener("click", save);
$("syncNow").addEventListener("click", async () => {
  $("status").textContent = "Syncing…";
  try {
    const results = await browser.runtime.sendMessage({ type: "SYNC_NOW" });
    const pushed = (results || []).filter((r) => r && r.pushed).length;
    const skipped = (results || []).filter((r) => r && r.skipped).length;
    $("status").textContent = `Done. Pushed: ${pushed}, unchanged: ${skipped}.`;
  } catch (e) {
    $("status").textContent = `Sync failed: ${e}`;
  }
});

load();
