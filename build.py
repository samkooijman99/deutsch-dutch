#!/usr/bin/env python3
"""Build a German->Dutch frequency vocabulary CSV from a subtitle frequency
list (hermitdave/FrequencyWords) joined against WikDict (CC BY-SA)."""

import csv
import re
import sqlite3
import sys
from collections import Counter

import simplemma

FREQ_FILE = "de_full.txt"
DENL_DB = "de-nl.sqlite3"      # German->Dutch bilingual (translations + POS)
NL_DB = "nl.sqlite3"           # Dutch monolingual (gender -> article)
OUT_CSV = "german_dutch_vocab.csv"
TOP_N = 10_000

# German-word character set (freq list is already lowercased)
GERMAN_RE = re.compile(r"^[a-zäöüß]+$")

# German Wiktionary POS labels that denote a noun (-> may carry a Dutch article)
NOUN_POS = {"Substantiv", "Eigenname"}


def normalize_ij(s: str) -> str:
    """Normalize the ij-ligature (U+0133 / U+0132) to plain 'ij' / 'IJ'."""
    return s.replace("ĳ", "ij").replace("Ĳ", "IJ")


def load_frequency():
    """Return (cleaned_top_entries, total_tokens).

    cleaned_top_entries: list of (word, count) in frequency order, top TOP_N.
    total_tokens: sum of ALL counts in the full list (for per-million)."""
    total = 0
    cleaned = []
    with open(FREQ_FILE, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            word, cnt = parts[0], parts[1]
            if not cnt.isdigit():
                continue
            cnt = int(cnt)
            total += cnt
            if len(cleaned) >= TOP_N:
                continue
            if len(word) < 2:                 # drop single letters
                continue
            if not GERMAN_RE.match(word):     # drop digits/punct/non-German
                continue
            cleaned.append((word, cnt))
    return cleaned, total


def build_translation_lookup():
    """lower(German) -> trans_list string (' | ' separated, importance-ranked).
    On homograph collision keep the higher-scoring row."""
    con = sqlite3.connect(DENL_DB)
    best = {}
    for written_rep, trans_list, max_score in con.execute(
        "SELECT written_rep, trans_list, max_score FROM simple_translation"
    ):
        if not written_rep or not trans_list:
            continue
        key = written_rep.lower()
        score = max_score if isinstance(max_score, (int, float)) else 0
        if key not in best or score > best[key][1]:
            best[key] = (trans_list, score)
    con.close()
    return {k: v[0] for k, v in best.items()}


def build_pos_lookup():
    """lower(German) -> POS label (from the highest-scoring lexentry row)."""
    con = sqlite3.connect(DENL_DB)
    best = {}
    for written_rep, lexentry, score in con.execute(
        "SELECT written_rep, lexentry, score FROM translation "
        "WHERE lexentry LIKE 'deu/%'"
    ):
        if not written_rep or not lexentry:
            continue
        segs = lexentry.split("__")
        if len(segs) < 2:
            continue
        pos = segs[1]
        key = written_rep.lower()
        sc = score if isinstance(score, (int, float)) else 0
        if key not in best or sc > best[key][1]:
            best[key] = (pos, sc)
    con.close()
    return {k: v[0] for k, v in best.items()}


def build_article_lookup():
    """lower(Dutch noun) -> 'de' | 'het' | 'de/het'.
    neuter -> het; masculine/feminine (common gender) -> de.
    Aggregates across source Wiktionaries; majority wins, ties -> 'de/het'."""
    con = sqlite3.connect(NL_DB)
    genders = {}
    for written_rep, gender in con.execute(
        "SELECT written_rep, gender FROM entry "
        "WHERE part_of_speech='noun' AND gender IN "
        "('neuter','masculine','feminine')"
    ):
        if not written_rep:
            continue
        art = "het" if gender == "neuter" else "de"
        genders.setdefault(written_rep.lower(), Counter())[art] += 1
    con.close()

    out = {}
    for word, counter in genders.items():
        if len(counter) == 1:
            out[word] = next(iter(counter))
        else:
            de_n, het_n = counter["de"], counter["het"]
            out[word] = "de" if de_n > het_n else "het" if het_n > de_n else "de/het"
    return out


def main():
    print("Loading frequency list...", file=sys.stderr)
    freq, total_tokens = load_frequency()
    print(f"  cleaned top {len(freq)} words; corpus total = {total_tokens:,} tokens",
          file=sys.stderr)

    print("Building lookups from WikDict...", file=sys.stderr)
    trans = build_translation_lookup()
    pos_map = build_pos_lookup()
    article_map = build_article_lookup()
    print(f"  translations: {len(trans):,}  pos: {len(pos_map):,}  "
          f"articles: {len(article_map):,}", file=sys.stderr)

    rows = []
    stats = {"direct": 0, "lemma": 0, "none": 0}
    band_defs = [("1-1000", 1, 1000), ("1001-3000", 1001, 3000),
                 ("3001-10000", 3001, 10000)]
    band_hit = {b[0]: 0 for b in band_defs}
    band_tot = {b[0]: 0 for b in band_defs}
    article_emitted = 0
    noun_rows = 0

    for rank, (word, cnt) in enumerate(freq, start=1):
        per_million = cnt / total_tokens * 1_000_000
        lemma_used = ""
        match_type = "none"
        trans_list = None

        # pass (a): direct match on surface form
        if word in trans:
            match_type = "direct"
            trans_list = trans[word]
            pos = pos_map.get(word, "")
        else:
            # pass (b): lemmatize and retry
            lemma = simplemma.lemmatize(word, lang="de")
            if lemma and lemma.lower() != word and lemma.lower() in trans:
                match_type = "lemma"
                lemma_used = lemma
                trans_list = trans[lemma.lower()]
                pos = pos_map.get(lemma.lower(), "")
            else:
                pos = ""

        dutch = dutch_alt = dutch_article = ""
        if trans_list:
            options = [normalize_ij(t.strip())
                       for t in trans_list.split("|") if t.strip()]
            if options:
                dutch = options[0]
                dutch_alt = "; ".join(options[1:4])
                if pos in NOUN_POS:
                    noun_rows += 1
                    art = article_map.get(dutch.lower(), "")
                    if art:
                        dutch_article = art
                        article_emitted += 1

        stats[match_type] += 1
        for name, lo, hi in band_defs:
            if lo <= rank <= hi:
                band_tot[name] += 1
                if match_type != "none":
                    band_hit[name] += 1

        rows.append({
            "rank": rank,
            "german": word,
            "dutch": dutch,
            "dutch_alt": dutch_alt,
            "dutch_article": dutch_article,
            "pos": pos,
            "lemma_used": lemma_used,
            "match_type": match_type,
            "frequency": cnt,
            "per_million": f"{per_million:.2f}",
        })

    fields = ["rank", "german", "dutch", "dutch_alt", "dutch_article", "pos",
              "lemma_used", "match_type", "frequency", "per_million"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # ---- coverage report ----
    n = len(rows)
    matched = stats["direct"] + stats["lemma"]
    print("\n" + "=" * 60)
    print("COVERAGE REPORT")
    print("=" * 60)
    print(f"Total words:        {n}")
    print(f"Matched (any):      {matched}  ({matched/n*100:.1f}%)")
    print(f"  direct:           {stats['direct']}  ({stats['direct']/n*100:.1f}%)")
    print(f"  via lemma:        {stats['lemma']}  ({stats['lemma']/n*100:.1f}%)")
    print(f"  unmatched (none): {stats['none']}  ({stats['none']/n*100:.1f}%)")
    print("\nBy rank band:")
    for name, _, _ in band_defs:
        t = band_tot[name]
        h = band_hit[name]
        if t:
            print(f"  {name:<12} {h}/{t}  ({h/t*100:.1f}%)")
    print(f"\nNoun rows (POS=Substantiv/Eigenname): {noun_rows}")
    print(f"Dutch articles emitted:               {article_emitted} "
          f"({article_emitted/noun_rows*100:.1f}% of noun rows)")
    print(f"\nWrote {OUT_CSV}")


if __name__ == "__main__":
    main()
