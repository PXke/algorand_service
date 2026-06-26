/* Shared helpers for content scripts (no imports in MV2). */

function cleanVisibleText(raw) {
  const lines = [];
  let prev = "";
  for (const part of (raw || "").split("\n")) {
    const line = part.trim();
    if (!line || line.length < 2 || line === prev) continue;
    lines.push(line);
    prev = line;
  }
  return lines.slice(-300).join("\n");
}

function pickLargestText(root) {
  const selectors = [
    "main",
    "article",
    "[role='main']",
    "[class*='messages']",
    "[class*='chat']",
    ".tgme_widget_message_wrap",
    "shreddit-feed",
  ];
  const chunks = [];
  for (const sel of selectors) {
    const nodes = root.querySelectorAll(sel);
    for (const node of nodes) {
      const t = (node.innerText || "").trim();
      if (t.length > 80) chunks.push(t);
    }
  }
  if (chunks.length) {
    chunks.sort((a, b) => b.length - a.length);
    return cleanVisibleText(chunks[0]);
  }
  return cleanVisibleText(root.body ? root.body.innerText : "");
}

function respondToBackground(request, sender, sendResponse) {
  if (request.type !== "SNAPSHOT") return false;
  const text = pickLargestText(document);
  const title = (document.title || "Channel").trim().slice(0, 512);
  sendResponse({
    ok: true,
    url: location.href,
    title,
    text,
    site: request.site,
  });
  return true;
}
