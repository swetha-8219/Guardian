// Offline comment filter - the same rules as guardian/classifier.py, in JavaScript.
(function (root) {
  const LEET = { "0": "o", "1": "i", "!": "i", "3": "e", "4": "a", "@": "a", "$": "s", "5": "s", "7": "t", "*": "" };
  const lists = root.GUARDIAN_WORDLISTS;
  const threats = lists.threat_patterns.map((p) => new RegExp(p));

  function normalise(text) {
    const t = text.toLowerCase().replace(/[01!34@$57*]/g, (ch) => LEET[ch]);
    return t.replace(/(.)\1{2,}/gu, "$1$1"); // "sooooo" -> "soo"
  }

  function classify(text) {
    const t = normalise(text);
    if (threats.some((re) => re.test(t))) return "threat";
    if (lists.harassment.some((w) => t.includes(w))) return "harassment";
    if (lists.criticism.some((w) => t.includes(w))) return "criticism";
    if (lists.supportive.some((w) => t.includes(w))) return "supportive";
    return "neutral";
  }

  root.GuardianFilter = { classify, HIDE: new Set(["harassment", "threat"]) };
})(globalThis);
