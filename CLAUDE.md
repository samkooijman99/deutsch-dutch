# D(e)ut(s)ch

*Read the letters in parentheses → **Deutsch** (German); skip them → **Dutch**. One deck, both directions.*

Tiny Anki-style flashcard app for German↔Dutch vocabulary — a clone of the
`spanishh` app, with a **direction toggle**. Same SM-2 scheduler; two ways to run.

## Static (works on any device, GitHub Pages)

Open `index.html` in a browser, or visit the GitHub Pages URL.
State persists in **`localStorage`** (key `deutsch_v1`) — per-browser-per-device.
First load builds the deck from `seed.json` (or `bootstrap.json` if present).

## Local server (desktop)

```bash
python3 app.py --serve          # http://localhost:8000
python3 app.py --serve --port 9000
```

State persists to **`data.json`**. No dependencies (Python 3.9+ stdlib only).
Server-mode and static-mode state are independent — they don't sync.

## Direction toggle (the difference from spanishh)

Each vocab word is studied in **both directions, as two independent cards** with
their own SM-2 schedule:

- **de2nl** — see German, recall Dutch (recognition).
- **nl2de** — see Dutch, recall German (production).

The header button (`🇩🇪 → 🇳🇱` / `🇳🇱 → 🇩🇪`) switches the active direction. Due counts,
new-card queue, and the per-day new-card cap are tracked **per direction**; streak
and total daily reviews are global. The active direction is persisted
(`config.active_dir` server-side, `localStorage` static-side).

## Rich answers

For German→Dutch, the answer shows the Dutch **article** (`de`/`het`) when known
plus up to 3 **alternative** translations. For Dutch→German, the Dutch prompt is
shown with its article; the answer is the German word.

## Files

- `index.html` — static single-file app (localStorage).
- `app.py` — desktop HTTP server (same scheduler, frontend embedded in `INDEX_HTML`).
- `generate_seed.py` — builds `seed.json` + `wordbank.txt` from `german_dutch_vocab.csv`.
- `seed.json` — one row per vocab word `{word_id, german, dutch, dutch_alt, dutch_article, pos, level, sublevel}`; each row is expanded into two direction cards on load.
- `bootstrap.json` — optional snapshot to seed the static app's first load (gitignored `data.json` is the server's live state).
- `wordbank.txt` — top German words for Add-form autocomplete.
- `german_dutch_vocab.csv`, `build.py`, `LICENSE.txt`, `*.sqlite3` — the upstream
  data pipeline that produced the vocabulary (see `LICENSE.txt` for WikDict CC BY-SA
  + FrequencyWords MIT attribution).

## Data model

Each card: `{id, word_id, dir, german, dutch, dutch_alt, dutch_article, pos, level,
sublevel, ease, interval_days, due_ts, reps, lapses}`.
Ratings: `0=Again, 1=Hard, 2=Good, 3=Easy` — SM-2 in `review()` (Python and JS match).

## Scheduling

`pick_due()`/`pickDue()` filters to the active direction, then prioritizes: in-session
relearn queue → review-due (already seen, now due) → new (capped per day, introduced in
`(level, word_id)` = frequency order). Cards with empty `dutch` are skipped.

## Deck size

`generate_seed.py` seeds the top **1000** frequency words with a Dutch match
(→ 2000 cards). Raise `DECK_SIZE` in that script and re-run to grow the deck; existing
progress in `data.json` / `localStorage` is preserved (cards merge by `word_id`+`dir`).

## Adding words

Use the **Add** form (German + Dutch + level/sublevel) — it creates both direction
cards. The server writes to `data.json`; the static app writes to `localStorage`.

## Cloud sync (optional, Supabase)

The static app can sync each learner's progress to Supabase so it survives
clearing browser data and follows you across phones. Tap **☁︎ Sync** and sign in
with an email magic link.

- The `anon` key embedded in `index.html` is **public by design** — Row-Level
  Security (see the `deck_state` table policies) restricts every row to its owner
  (`auth.uid() = user_id`), so the public key cannot read or write anyone else's
  data. The `service_role` key must never be committed.
- On sign-in the app pulls the cloud state and **merges** it with local
  (`mergeStates`, by `word_id`+`dir`, keeping the more-studied card) — non-destructive.
- **Export/Import** buttons provide file backup/restore as an offline fallback.
- The `app.py` desktop server persists to `data.json` only (no cloud).
