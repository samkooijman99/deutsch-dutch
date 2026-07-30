#!/usr/bin/env python3
"""Deutsch — minimal Anki-style German<->Dutch flashcard server.
Run: python3 app.py --serve

Each vocab word is studied in BOTH directions as two independent cards with
their own SM-2 schedule: "de2nl" (see German, recall Dutch) and "nl2de"
(see Dutch, recall German). The header toggle picks the active direction."""
import argparse
import json
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(ROOT, "data.json")
SEED_PATH = os.path.join(ROOT, "seed.json")
WORDBANK_PATH = os.path.join(ROOT, "wordbank.txt")
DAY = 86400
LOCK = threading.Lock()
LEVEL_ORDER = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}
DIRECTIONS = ("de2nl", "nl2de")
WORDBANK: list[str] = []
DEFAULT_NEW_PER_DAY = 15
RELEARN_GAP = 3
# Ephemeral in-process relearn queue: list of {"id": int, "dir": str, "grades_left": int}.
RELEARN_QUEUE: list[dict] = []


def load_wordbank() -> list[str]:
    if not os.path.exists(WORDBANK_PATH):
        return []
    with open(WORDBANK_PATH, "r", encoding="utf-8") as f:
        return [w.strip() for w in f if w.strip()]


def suggest(q: str, limit: int = 10) -> list[str]:
    q = q.strip().lower()
    if not q or not WORDBANK:
        return []
    prefix = [w for w in WORDBANK if w.lower().startswith(q)]
    if len(prefix) >= limit:
        return prefix[:limit]
    contains = [w for w in WORDBANK if q in w.lower() and not w.lower().startswith(q)]
    return (prefix + contains)[:limit]


def level_rank(card: dict) -> tuple[int, int]:
    lvl = card.get("level") or "A1"
    sub = card.get("sublevel") or 1
    return (LEVEL_ORDER.get(lvl, 99), int(sub))


def now() -> int:
    return int(time.time())


def today_key() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def yesterday_key() -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - DAY))


def _per_dir(value) -> dict:
    """Coerce a value into {"de2nl": {...}, "nl2de": {...}}, keeping usable parts."""
    src = value if isinstance(value, dict) else {}
    return {d: (src[d] if isinstance(src.get(d), dict) else {}) for d in DIRECTIONS}


def ensure_state_defaults(state: dict) -> None:
    """Backfill fields on existing data.json without disturbing card progress.
    ALL progress (intro_log, daily_log, streak, pace) is tracked PER DIRECTION so
    the two learners — one studying German, one studying Dutch — stay independent."""
    if "config" not in state:
        state["config"] = {}
    if state["config"].get("active_dir") not in DIRECTIONS:
        state["config"]["active_dir"] = "de2nl"
    # new_per_day is per direction (each learner sets their own pace).
    npd = state["config"].get("new_per_day")
    if isinstance(npd, int):                       # migrate legacy shared int
        npd = {d: npd for d in DIRECTIONS}
    if not isinstance(npd, dict):
        npd = {}
    state["config"]["new_per_day"] = {
        d: (npd.get(d) if isinstance(npd.get(d), int) else DEFAULT_NEW_PER_DAY)
        for d in DIRECTIONS}
    # intro_log / daily_log: one independent record per direction.
    state["intro_log"] = _per_dir(state.get("intro_log"))
    state["daily_log"] = _per_dir(state.get("daily_log"))
    # streak: one independent day-streak per direction.
    sk = state.get("streak") if isinstance(state.get("streak"), dict) else {}
    state["streak"] = {
        d: (sk[d] if isinstance(sk.get(d), dict) and "days" in sk[d]
            else {"last_date": None, "days": 0})
        for d in DIRECTIONS}


def active_dir(state: dict) -> str:
    return state.get("config", {}).get("active_dir", "de2nl")


# --- per-direction progress accessors: `d` selects which learner's track ---

def today_log(state: dict, d: str) -> dict:
    dl = state["daily_log"][d]
    k = today_key()
    if k not in dl:
        dl[k] = {"reviews": 0, "mastered": 0}
    return dl[k]


def new_intros_today(state: dict, d: str) -> int:
    return state["intro_log"][d].get(today_key(), 0)


def new_per_day(state: dict, d: str) -> int:
    return state["config"]["new_per_day"].get(d, DEFAULT_NEW_PER_DAY)


def bump_streak(state: dict, d: str) -> bool:
    """Update direction d's day-streak; True if it's the first review of a new day."""
    s = state["streak"][d]
    today = today_key()
    if s["last_date"] == today:
        return False
    if s["last_date"] == yesterday_key():
        s["days"] += 1
    else:
        s["days"] = 1
    s["last_date"] = today
    return True


def new_card(cid: int, word: dict, d: str) -> dict:
    """Build one directional card from a seed word dict for direction d."""
    return {
        "id": cid,
        "word_id": word["word_id"],
        "dir": d,
        "german": word.get("german", ""),
        "dutch": word.get("dutch", ""),
        "dutch_alt": word.get("dutch_alt", ""),
        "dutch_article": word.get("dutch_article", ""),
        "pos": word.get("pos", ""),
        "level": word.get("level"),
        "sublevel": word.get("sublevel"),
        "ease": 2.5,
        "interval_days": 0.0,
        "due_ts": now(),
        "reps": 0,
        "lapses": 0,
    }


def load_state() -> dict:
    seed = []
    if os.path.exists(SEED_PATH):
        with open(SEED_PATH, "r", encoding="utf-8") as f:
            seed = json.load(f)

    def expand(state: dict) -> None:
        """Add a card for every (seed word, direction) pair not already present."""
        have = {(c.get("word_id"), c.get("dir")) for c in state["cards"]}
        for w in seed:
            for d in DIRECTIONS:
                if (w["word_id"], d) in have:
                    continue
                cid = state["next_id"]
                state["next_id"] = cid + 1
                state["cards"].append(new_card(cid, w, d))

    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        ensure_state_defaults(state)
        expand(state)
        return state

    state = {"next_id": 1, "cards": []}
    ensure_state_defaults(state)
    expand(state)
    return state


def save_state(state: dict) -> None:
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_PATH)


def review(card: dict, rating: int, state: dict) -> dict:
    """SM-2 lite. rating: 0=Again, 1=Hard, 2=Good, 3=Easy.
    Returns gamification events."""
    was_new = card["reps"] == 0
    was_mature = card["interval_days"] >= 21
    d = card["dir"]
    ease = card["ease"]
    interval = card["interval_days"]
    if rating == 0:
        card["lapses"] += 1
        ease = max(1.3, ease - 0.2)
        interval = 0.007  # ~10 minutes; in-session relearn queue handles immediate re-show.
    else:
        if interval < 1:
            interval = 1.0 if rating >= 2 else 0.5
        else:
            mult = {1: 1.2, 2: ease, 3: ease * 1.3}[rating]
            interval = interval * mult
        ease = max(1.3, ease + {1: -0.15, 2: 0.0, 3: 0.15}[rating])
    card["ease"] = round(ease, 3)
    card["interval_days"] = round(interval, 3)
    card["due_ts"] = now() + int(interval * DAY)
    card["reps"] += 1

    if was_new:
        k = today_key()
        state["intro_log"][d][k] = state["intro_log"][d].get(k, 0) + 1

    log = today_log(state, d)
    log["reviews"] = log.get("reviews", 0) + 1
    is_mature = card["interval_days"] >= 21
    if not was_mature and is_mature:
        log["mastered"] = log.get("mastered", 0) + 1

    streak_bumped = bump_streak(state, d)

    for r in RELEARN_QUEUE:
        r["grades_left"] -= 1
    if rating <= 1 and card["interval_days"] < 1:
        RELEARN_QUEUE.append({"id": card["id"], "dir": d, "grades_left": RELEARN_GAP})

    return {
        "card": card,
        "mastered": (not was_mature) and is_mature,
        "streak_bumped": streak_bumped,
        "streak_days": state["streak"][d]["days"],
        "new_today": new_intros_today(state, d),
        "reviews_today": log["reviews"],
        "was_new": was_new,
    }


def pick_due(state: dict) -> dict | None:
    """Order (within the active direction): relearn queue → review-due → new (capped/day)."""
    d = active_dir(state)
    by_id = {c["id"]: c for c in state["cards"]}
    # 1. In-session relearn queue: any elapsed entry for the active direction.
    for i, r in enumerate(RELEARN_QUEUE):
        if r["grades_left"] <= 0 and r["dir"] == d:
            cid = r["id"]
            del RELEARN_QUEUE[i]
            if cid in by_id:
                return by_id[cid]
            break
    # 2. Review-due, skipping cards already queued.
    in_queue = {r["id"] for r in RELEARN_QUEUE}
    t = now()
    review_due = [c for c in state["cards"]
                  if c["dir"] == d and c["due_ts"] <= t and c["reps"] > 0
                  and c.get("dutch") and c["id"] not in in_queue]
    if review_due:
        review_due.sort(key=lambda c: (-c["lapses"], c["due_ts"]))
        return review_due[0]
    # 3. New cards, capped per local day per direction.
    if new_intros_today(state, d) < new_per_day(state, d):
        new_cards = [c for c in state["cards"]
                     if c["dir"] == d and c["reps"] == 0 and c.get("dutch")]
        if new_cards:
            new_cards.sort(key=lambda c: (level_rank(c), c["word_id"]))
            return new_cards[0]
    # 4. Fallback: a relearn entry for this direction is still pending — show it now.
    for i, r in enumerate(RELEARN_QUEUE):
        if r["dir"] == d:
            del RELEARN_QUEUE[i]
            if r["id"] in by_id:
                return by_id[r["id"]]
            break
    return None


def stats(state: dict) -> dict:
    d = active_dir(state)
    t = now()
    cards = [c for c in state["cards"] if c["dir"] == d and c.get("dutch")]
    due = sum(1 for c in cards if c["due_ts"] <= t and c["reps"] > 0)
    learning = sum(1 for c in cards if c["interval_days"] < 1 and c["reps"] > 0)
    new = sum(1 for c in cards if c["reps"] == 0)
    mature = sum(1 for c in cards if c["interval_days"] >= 21)
    log = state["daily_log"][d].get(today_key(), {})
    return {
        "dir": d,
        "total": len(cards), "due": due, "new": new,
        "learning": learning, "mature": mature,
        "new_today": new_intros_today(state, d),
        "new_per_day": new_per_day(state, d),
        "reviews_today": log.get("reviews", 0),
        "mastered_today": log.get("mastered", 0),
        "streak_days": state["streak"][d]["days"],
    }


# ---------- HTTP ----------


INDEX_HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Deutsch — Karteikarten</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root { --bg:#0f1115; --fg:#e8e8ea; --muted:#8b8f99; --accent:#ffb86b; --card:#1a1d24; --border:#262a33; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "SF Pro", system-ui, sans-serif;
         background:var(--bg); color:var(--fg); display:flex; flex-direction:column; min-height:100vh; }
  header { padding:1rem 1.25rem; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; gap:.75rem; }
  header h1 { margin:0; font-size:1.1rem; font-weight:600; letter-spacing:.02em; white-space:nowrap; }
  .stats { color:var(--muted); font-size:.85rem; font-variant-numeric: tabular-nums; text-align:right; }
  .stats span { margin-left:1rem; }
  #dir-toggle { background:var(--card); color:var(--fg); border:1px solid var(--border); border-radius:999px;
                padding:.3rem .8rem; cursor:pointer; font-size:.9rem; font-family:inherit; white-space:nowrap; }
  #dir-toggle:hover { border-color:var(--accent); }
  main { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:2rem 1rem; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:14px;
          width:min(560px, 100%); padding:3rem 2rem; text-align:center; min-height:280px;
          display:flex; flex-direction:column; justify-content:center; gap:1rem; }
  .word { font-size:2.4rem; font-weight:600; word-break:break-word; }
  .answer { font-size:1.6rem; color:var(--accent); }
  .alt { color:var(--muted); font-size:1rem; }
  .hint { color:var(--muted); font-size:.85rem; }
  .btns { display:flex; gap:.5rem; flex-wrap:wrap; justify-content:center; margin-top:1.5rem; width:min(560px,100%); }
  button { background:var(--card); color:var(--fg); border:1px solid var(--border); padding:.65rem 1.1rem;
           border-radius:8px; cursor:pointer; font-size:.95rem; font-family:inherit; flex:1; min-width:90px; }
  button:hover { border-color:var(--accent); }
  button.show { background:var(--accent); color:#1a1d24; border-color:var(--accent); flex:1; }
  .r0 { color:#ff6b6b; } .r1 { color:#f8d36b; } .r2 { color:#7bd88f; } .r3 { color:#6bb7ff; }
  .badge { display:inline-block; align-self:center; font-size:.7rem; letter-spacing:.06em;
           color:var(--muted); border:1px solid var(--border); border-radius:999px;
           padding:.15rem .55rem; font-variant-numeric:tabular-nums; }
  .empty { text-align:center; color:var(--muted); }
  .empty h2 { color:var(--fg); }
  footer { padding:1rem 1.25rem; border-top:1px solid var(--border); display:flex; gap:.5rem; }
  footer input, footer select { background:var(--bg); color:var(--fg); border:1px solid var(--border);
                 padding:.5rem .65rem; border-radius:6px; font-family:inherit; font-size:.9rem; }
  footer input { flex:1; }
  footer select, footer #sub { flex:0 0 auto; width:auto; }
  footer #sub { width:4rem; }
  footer button { flex:0; }
  kbd { background:var(--bg); border:1px solid var(--border); border-radius:4px; padding:1px 5px; font-size:.75rem; }
  .toast { position: fixed; top: 1rem; left: 50%; transform: translate(-50%, -16px);
           background: var(--accent); color: #1a1d24; padding: .5rem .9rem; border-radius: 999px;
           font-weight: 600; font-size: .85rem; opacity: 0; pointer-events: none;
           transition: opacity .25s ease, transform .25s ease; z-index: 100;
           box-shadow: 0 4px 16px rgba(0,0,0,.45); max-width: 90vw;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .toast.show { opacity: 1; transform: translate(-50%, 0); }
  .streak { color: #ffb86b; font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>Deutsch ↔ Nederlands</h1>
  <button id="dir-toggle" title="Switch learner — each has fully independent progress">…</button>
  <div class="stats" id="stats">…</div>
</header>
<main id="main"><div class="empty">Loading…</div></main>
<footer>
  <input id="de" placeholder="duits" autocomplete="off" list="de-suggest">
  <datalist id="de-suggest"></datalist>
  <input id="nl" placeholder="nederlands" autocomplete="off">
  <select id="lvl" title="CEFR level">
    <option>A1</option><option>A2</option><option>B1</option>
    <option>B2</option><option>C1</option><option>C2</option>
  </select>
  <input id="sub" type="number" min="1" max="99" value="1" title="sublevel">
  <button id="add">Add</button>
</footer>
<script>
let current = null;
let revealed = false;
let lastStats = null;
let sessionCombo = 0;

const DIR_LABEL = { de2nl: "🇳🇱 Learning Dutch", nl2de: "🇩🇪 Learning German" };

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}

function dutchPhrase(c) {
  return (c.dutch_article ? c.dutch_article + " " : "") + c.dutch;
}

async function refreshStats() {
  const s = await api('/api/stats');
  lastStats = s;
  document.getElementById('dir-toggle').textContent = DIR_LABEL[s.dir] || s.dir;
  const streakHtml = s.streak_days >= 2 ? `<span class="streak" title="day streak">🔥 ${s.streak_days}d</span>` : '';
  document.getElementById('stats').innerHTML =
    `${streakHtml}<span>due <b>${s.due}</b></span><span>new ${s.new}</span><span>learning ${s.learning}</span><span>mature ${s.mature}</span><span>total ${s.total}</span><span id="today-cap" style="cursor:pointer;text-decoration:underline dotted" title="tap to change daily new-card limit">today ${s.new_today}/${s.new_per_day}</span>`;
  document.getElementById('today-cap').onclick = editDailyCap;
}

async function editDailyCap() {
  const cur = lastStats ? lastStats.new_per_day : 15;
  const v = prompt(`New cards per day for this learner (currently ${cur}). Use a high number like 999 for unlimited.`, String(cur));
  if (v === null) return;
  const n = parseInt(v, 10);
  if (!Number.isFinite(n) || n < 0) return;
  await api('/api/config', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({new_per_day: n})});
  refreshStats();
  if (!current) loadNext();
}

async function toggleDir() {
  const next = (lastStats && lastStats.dir === 'de2nl') ? 'nl2de' : 'de2nl';
  await api('/api/direction', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({dir: next})});
  sessionCombo = 0;
  loadNext();
}

function toast(html, ms=2200) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.innerHTML = html;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, ms);
}

async function loadNext() {
  revealed = false;
  const r = await api('/api/next');
  current = r.card;
  render();
  refreshStats();
}

function render() {
  const m = document.getElementById('main');
  if (!current) {
    const capped = lastStats && lastStats.new > 0 && lastStats.new_today >= lastStats.new_per_day;
    if (capped) {
      m.innerHTML = `<div class="empty"><h2>Daily new-card limit reached</h2>
        <p>${lastStats.new_today} new cards introduced today (cap ${lastStats.new_per_day}) for this learner.
        ${lastStats.new} new cards still in the deck. Reviews are caught up.</p>
        <div class="btns" style="margin-top:1rem"><button onclick="loadMore()">+10 more today</button><button onclick="editDailyCap()">Change cap…</button></div></div>`;
    } else {
      m.innerHTML = `<div class="empty"><h2>All caught up 🎉</h2><p>No cards due right now for this learner. Switch learner above, add words below, or come back later.</p></div>`;
    }
    return;
  }
  const tag = current.level ? `${current.level}.${current.sublevel || 1}` : '—';
  const front = current.dir === 'de2nl' ? current.german : dutchPhrase(current);
  if (!revealed) {
    m.innerHTML = `
      <div class="card">
        <div class="badge">${tag}</div>
        <div class="word">${escapeHtml(front)}</div>
      </div>
      <div class="btns"><button class="show" onclick="reveal()">Show answer <kbd>enter</kbd></button></div>`;
  } else {
    let answerHtml;
    if (current.dir === 'de2nl') {
      const alt = current.dutch_alt ? `<div class="alt">also: ${escapeHtml(current.dutch_alt)}</div>` : '';
      answerHtml = `<div class="answer">${escapeHtml(dutchPhrase(current))}</div>${alt}`;
    } else {
      answerHtml = `<div class="answer">${escapeHtml(current.german)}</div>`;
    }
    m.innerHTML = `
      <div class="card">
        <div class="badge">${tag}</div>
        <div class="word">${escapeHtml(front)}</div>
        ${answerHtml}
        <div class="hint">reps ${current.reps} · ease ${current.ease} · interval ${current.interval_days}d</div>
      </div>
      <div class="btns">
        <button class="r0" onclick="grade(0)">Again <kbd>1</kbd></button>
        <button class="r1" onclick="grade(1)">Hard <kbd>2</kbd></button>
        <button class="r2" onclick="grade(2)">Good <kbd>3</kbd></button>
        <button class="r3" onclick="grade(3)">Easy <kbd>4</kbd></button>
      </div>`;
  }
}

function reveal() { revealed = true; render(); }

async function loadMore() {
  await api('/api/relax_cap', {method: 'POST', headers: {'content-type':'application/json'}, body: '{}'});
  loadNext();
}

async function grade(rating) {
  if (!current) return;
  const word = current.dir === 'de2nl' ? current.german : dutchPhrase(current);
  const r = await api('/api/review', {
    method: 'POST', headers: {'content-type':'application/json'},
    body: JSON.stringify({id: current.id, rating}),
  });
  sessionCombo = rating >= 2 ? sessionCombo + 1 : 0;
  if (r.mastered) {
    toast(`🌟 Mastered: ${escapeHtml(word)}`);
  } else if (r.streak_bumped && r.streak_days >= 2) {
    toast(`🔥 Day ${r.streak_days} streak!`);
  } else if (r.was_new && r.new_today > 0 && r.new_today % 5 === 0) {
    toast(`🌱 ${r.new_today} new words today!`);
  } else if ([25, 50, 100, 200].includes(r.reviews_today)) {
    toast(`⚡ ${r.reviews_today} reviews today!`);
  } else if ([5, 10, 20, 50].includes(sessionCombo)) {
    toast(`💫 ${sessionCombo}× combo!`);
  }
  loadNext();
}

async function addWord() {
  const de = document.getElementById('de').value.trim();
  const nl = document.getElementById('nl').value.trim();
  const lvl = document.getElementById('lvl').value;
  const sub = parseInt(document.getElementById('sub').value, 10) || 1;
  if (!de || !nl) return;
  await api('/api/add', {
    method: 'POST', headers: {'content-type':'application/json'},
    body: JSON.stringify({german: de, dutch: nl, level: lvl, sublevel: sub}),
  });
  document.getElementById('de').value = '';
  document.getElementById('nl').value = '';
  document.getElementById('de').focus();
  refreshStats();
  if (!current) loadNext();
}
document.getElementById('add').onclick = addWord;
document.getElementById('dir-toggle').onclick = toggleDir;
for (const id of ['de','nl','sub']) {
  document.getElementById(id).addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); addWord(); }
  });
}

let suggestTimer = null;
document.getElementById('de').addEventListener('input', e => {
  const q = e.target.value.trim();
  clearTimeout(suggestTimer);
  if (q.length < 2) { document.getElementById('de-suggest').innerHTML = ''; return; }
  suggestTimer = setTimeout(async () => {
    const r = await api('/api/suggest?q=' + encodeURIComponent(q));
    document.getElementById('de-suggest').innerHTML =
      (r.matches || []).map(w => `<option value="${escapeHtml(w)}">`).join('');
  }, 120);
});

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (!current) return;
  if (!revealed && (e.key === ' ' || e.key === 'Enter')) { e.preventDefault(); reveal(); return; }
  if (revealed && ['1','2','3','4'].includes(e.key)) { grade(parseInt(e.key)-1); }
});

function escapeHtml(s) { return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

loadNext();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/next":
            with LOCK:
                state = load_state()
                card = pick_due(state)
            self._send_json({"card": card})
            return
        if self.path == "/api/stats":
            with LOCK:
                state = load_state()
                self._send_json(stats(state))
            return
        if self.path.startswith("/api/suggest"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            q = (qs.get("q", [""])[0] or "")[:64]
            self._send_json({"matches": suggest(q)})
            return
        self.send_error(404)

    def do_POST(self):
        if self.path == "/api/review":
            data = self._read_json()
            cid = int(data.get("id", 0))
            rating = int(data.get("rating", -1))
            if rating not in (0, 1, 2, 3):
                self._send_json({"error": "bad rating"}, 400)
                return
            with LOCK:
                state = load_state()
                for c in state["cards"]:
                    if c["id"] == cid:
                        events = review(c, rating, state)
                        save_state(state)
                        self._send_json({"ok": True, **events})
                        return
            self._send_json({"error": "not found"}, 404)
            return
        if self.path == "/api/direction":
            data = self._read_json()
            d = (data.get("dir") or "").strip()
            if d not in DIRECTIONS:
                self._send_json({"error": "bad dir"}, 400)
                return
            with LOCK:
                state = load_state()
                state["config"]["active_dir"] = d
                save_state(state)
            self._send_json({"ok": True, "dir": d})
            return
        if self.path == "/api/config":
            data = self._read_json()
            n = data.get("new_per_day")
            if not isinstance(n, int) or n < 0:
                self._send_json({"error": "bad new_per_day"}, 400)
                return
            with LOCK:
                state = load_state()
                state["config"]["new_per_day"][active_dir(state)] = n
                save_state(state)
            self._send_json({"ok": True, "new_per_day": n})
            return
        if self.path == "/api/relax_cap":
            # "Load 10 more today" for the active direction, without raising the cap.
            with LOCK:
                state = load_state()
                d = active_dir(state)
                k = today_key()
                state["intro_log"][d][k] = max(0, state["intro_log"][d].get(k, 0) - 10)
                save_state(state)
            self._send_json({"ok": True, "new_today": new_intros_today(state, d)})
            return
        if self.path == "/api/add":
            data = self._read_json()
            de = (data.get("german") or "").strip()
            nl = (data.get("dutch") or "").strip()
            lvl = (data.get("level") or "A1").strip().upper()
            sub = int(data.get("sublevel") or 1)
            if not de or not nl:
                self._send_json({"error": "missing fields"}, 400)
                return
            if lvl not in LEVEL_ORDER:
                lvl = "A1"
            with LOCK:
                state = load_state()
                # New word_id above any existing, so it introduces after the seed.
                wid = max((c.get("word_id", 0) for c in state["cards"]), default=0) + 1
                word = {"word_id": wid, "german": de, "dutch": nl, "dutch_alt": "",
                        "dutch_article": data.get("dutch_article", ""), "pos": "",
                        "level": lvl, "sublevel": sub}
                for d in DIRECTIONS:
                    cid = state["next_id"]
                    state["next_id"] = cid + 1
                    state["cards"].append(new_card(cid, word, d))
                save_state(state)
            self._send_json({"ok": True, "word_id": wid})
            return
        self.send_error(404)


def serve(host: str, port: int) -> None:
    global WORDBANK
    with LOCK:
        state = load_state()
        save_state(state)
    WORDBANK = load_wordbank()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Deutsch serving on http://{host}:{port}  "
          f"({len(state['cards'])} cards, {len(WORDBANK)} wordbank entries)")
    print(f"Data persisted to {DATA_PATH}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ntschüss")


def main() -> None:
    p = argparse.ArgumentParser(description="Deutsch<->Nederlands flashcard server")
    p.add_argument("--serve", action="store_true", help="start the web server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    if args.serve:
        serve(args.host, args.port)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
