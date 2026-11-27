const defaults = { enabled: true, useApp: false };

chrome.storage.sync.get(defaults, (s) => {
  for (const key of Object.keys(defaults)) {
    const box = document.getElementById(key);
    box.checked = s[key];
    box.onchange = () => chrome.storage.sync.set({ [key]: box.checked });
  }
});

chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
  if (!tab) return;
  chrome.tabs.sendMessage(tab.id, { type: "counts" }, (c) => {
    if (chrome.runtime.lastError || !c) return; // not a supported site
    document.getElementById("stat").textContent =
      `On this page: ${c.checked} comments checked, ${c.hidden} hidden for you 💚`;
  });
});
