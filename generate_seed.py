#!/usr/bin/env python3
"""Generate seed.json (the flashcard deck) and wordbank.txt (Add-form
autocomplete) from german_dutch_vocab.csv.

seed.json holds ONE row per vocab word; app.py / index.html expand each row
into two cards (German->Dutch and Dutch->German) with independent schedules."""

import csv
import re
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(ROOT, "german_dutch_vocab.csv")
FREQ_PATH = os.path.join(ROOT, "de_full.txt")
SEED_PATH = os.path.join(ROOT, "seed.json")
WORDBANK_PATH = os.path.join(ROOT, "wordbank.txt")
CATS_PATH = os.path.join(ROOT, "categories.json")
SITS_PATH = os.path.join(ROOT, "situations.json")

DECK_SIZE = 5000          # number of vocab words -> 2x cards. Raise freely.
WORDBANK_SIZE = 50_000    # German words offered for Add-form autocomplete.

GERMAN_RE = re.compile(r"^[a-zäöüß]+$")

# Frequency-rank -> CEFR-ish band so new cards are introduced most-frequent-first
# and the badge is meaningful. (upper_rank_bound, level).
BANDS = [(600, "A1"), (1500, "A2"), (3000, "B1"), (6000, "B2"), (10_000, "C1")]


def level_for_rank(rank: int) -> tuple[str, int]:
    for upper, level in BANDS:
        if rank <= upper:
            sub = (rank - 1) // 100 % 6 + 1  # 1..6 within the band, keeps order
            return level, sub
    return "C1", 6


def build_seed() -> list[dict]:
    cats = json.load(open(CATS_PATH, encoding="utf-8")) if os.path.exists(CATS_PATH) else {}
    sits = json.load(open(SITS_PATH, encoding="utf-8")) if os.path.exists(SITS_PATH) else {}
    rows = []
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r["match_type"] == "none" or not r["dutch"].strip():
                continue
            rank = int(r["rank"])
            level, sub = level_for_rank(rank)
            rows.append({
                "word_id": rank,          # rank == frequency order == word_id
                "german": r["german"],
                "dutch": r["dutch"],
                "dutch_alt": r["dutch_alt"],
                "dutch_article": r["dutch_article"],
                "pos": r["pos"],
                "level": level,
                "sublevel": sub,
                "category": cats.get(str(rank), "Other"),
                "situations": sits.get(str(rank), []),
            })
            if len(rows) >= DECK_SIZE:
                break
    return rows


def build_wordbank() -> list[str]:
    """Top German words (cleaned) from the frequency list, for autocomplete."""
    words = []
    with open(FREQ_PATH, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            w = parts[0]
            if len(w) >= 2 and GERMAN_RE.match(w):
                words.append(w)
            if len(words) >= WORDBANK_SIZE:
                break
    return words


def main() -> None:
    seed = build_seed()
    with open(SEED_PATH, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=1)

    wordbank = build_wordbank()
    with open(WORDBANK_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(wordbank) + "\n")

    print(f"seed.json:     {len(seed)} words  ->  {len(seed) * 2} cards")
    print(f"wordbank.txt:  {len(wordbank)} German words")
    if seed:
        print("sample:", json.dumps(seed[0], ensure_ascii=False))


if __name__ == "__main__":
    main()
