// Finds comments on the page, blurs the bullying ones, and keeps watching as you scroll.
(function () {
  // Where each site puts comment text. Sites change their pages now and then;
  // if blurring stops working on one, these selectors are what to update.
  const SITES = {
    "www.youtube.com": "ytd-comments #content-text",
    "www.instagram.com": "ul li span[dir='auto'], div[role='dialog'] span[dir='auto']",
    "www.tiktok.com": "[data-e2e='comment-level-1'], [data-e2e='comment-level-2']",
    "x.com": "article [data-testid='tweetText']",
    "twitter.com": "article [data-testid='tweetText']",
  };
  const selector = SITES[location.hostname] || globalThis.GUARDIAN_TEST_SELECTOR;
  if (!selector) return;

  const { classify, HIDE } = globalThis.GuardianFilter;
  let settings = { enabled: true, useApp: false };
  const counts = { checked: 0, hidden: 0 };

  function hideEl(el) {
    if (el.dataset.bbHidden === "1") return;
    el.dataset.bbHidden = "1";
    el.classList.add("bb-blur");
    counts.hidden++;
    const label = document.createElement("button");
    label.className = "bb-label";
    label.type = "button";
    label.textContent = "🛡️ Hidden by Guardian · show anyway";
    label.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      el.classList.remove("bb-blur");
      label.remove();
    });
    el.before(label);
  }

  function showEl(el) {
    if (el.dataset.bbHidden !== "1") return;
    el.dataset.bbHidden = "0";
    el.classList.remove("bb-blur");
    counts.hidden--;
    if (el.previousElementSibling?.classList.contains("bb-label")) el.previousElementSibling.remove();
  }

  function apply(el, category) {
    el.dataset.bbCategory = category;
    if (settings.enabled && HIDE.has(category)) hideEl(el);
    else showEl(el);
  }

  // Optional: ask the Guardian app (with Claude) for a smarter second opinion.
  function askApp(els) {
    if (!settings.useApp || !els.length || !globalThis.chrome?.runtime?.sendMessage) return;
    chrome.runtime.sendMessage({ type: "classify", texts: els.map((el) => el.innerText) }, (res) => {
      if (chrome.runtime.lastError || !res?.categories) return; // app not running: keep offline result
      res.categories.forEach((cat, i) => apply(els[i], cat));
    });
  }

  function scan() {
    const fresh = [];
    document.querySelectorAll(selector).forEach((el) => {
      const text = el.innerText.trim();
      if (!text || el.dataset.bbText === text) return;
      el.dataset.bbText = text; // re-check if the site reuses the element for a new comment
      counts.checked++;
      apply(el, classify(text));
      fresh.push(el);
    });
    for (let i = 0; i < fresh.length; i += 50) askApp(fresh.slice(i, i + 50));
  }

  function reapplyAll() {
    document.querySelectorAll("[data-bb-category]").forEach((el) => apply(el, el.dataset.bbCategory));
  }

  let timer;
  new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(scan, 300); })
    .observe(document.body, { childList: true, subtree: true });

  if (globalThis.chrome?.storage?.sync) {
    chrome.storage.sync.get(settings, (s) => { settings = s; scan(); });
    chrome.storage.onChanged.addListener((changes) => {
      for (const [k, v] of Object.entries(changes)) settings[k] = v.newValue;
      reapplyAll();
      if (changes.useApp?.newValue) askApp([...document.querySelectorAll("[data-bb-category]")].slice(0, 200));
    });
    chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
      if (msg.type === "counts") reply({ ...counts, site: location.hostname });
    });
  } else {
    scan();
  }
  globalThis.GUARDIAN_COUNTS = counts; // handy for testing
})();
