// Talks to the Guardian app on your computer (if it's running) for AI sorting.
const APP_URL = "http://localhost:5000/api/classify";

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type !== "classify") return;
  fetch(APP_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ texts: msg.texts }),
  })
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => reply(data))
    .catch(() => reply(null)); // app not running - the offline filter result stays
  return true; // keep the channel open for the async reply
});
