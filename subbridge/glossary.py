"""Glossary management for subtitle-translate.

Three sub-commands:
  discover  — Extract candidate named entities from subtitle text
  fetch     — Auto-lookup translations via Wikipedia/Wikidata API
  lock      — Finalize and lock glossary after review
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from helpers import load_cache

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


# ── Candidate Discovery ───────────────────────────────────────

# Common words to exclude from candidate extraction
_EN_STOP_WORDS = {
    "the", "a", "an", "this", "that", "these", "those",
    "i", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "his", "its", "our", "their", "your",
    "is", "are", "was", "were", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "shall", "should", "may", "might",
    "can", "could", "must", "need", "dare",
    "not", "no", "nor", "neither",
    "if", "then", "else", "when", "where", "why", "how",
    "and", "or", "but", "so", "for", "yet",
    "with", "without", "from", "to", "in", "on", "at", "by",
    "of", "as", "up", "down", "out", "off", "over", "under",
    "again", "further", "more", "once",
    "here", "there", "what", "which",
    "who", "whom", "whose",
    "say", "says", "said", "go", "goes", "went", "come", "came",
    "see", "look", "know", "think", "tell", "ask",
    "man", "men", "woman", "women", "child", "children",
    "yes", "no", "ok", "okay", "please", "thanks", "thank",
    "well", "right", "good", "bad", "big", "little",
}


_JA_PATTERN = re.compile(r"[\u30A0-\u30FF]+")  # Katakana sequences
_CJK_PATTERN = re.compile(r"[\u4E00-\u9FFF]+")
_LATIN_UPPER = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")


def discover_candidates(segments: list[dict], source_lang: str = "") -> list[dict]:
    text = "\n".join(s["source_text"] for s in segments)

    candidates = Counter()
    contexts = {}

    source_lang = source_lang.lower() if source_lang else ""

    # Clean ASS/VTT tags and SRT/ASS newline markers
    from helpers import normalize_apostrophes
    clean = re.sub(r"\{[^}]*\}", "", text)
    clean = re.sub(r"<[^>]*>", "", clean)
    clean = re.sub(r"\\[Nn]", " ", clean)  # strip \N \n markers
    clean = clean.replace("\n", " ").replace("\r", " ")
    clean = normalize_apostrophes(clean)  # curly quotes -> straight

    if source_lang in ("ja", "jp"):
        for m in _JA_PATTERN.finditer(clean):
            word = m.group().strip()
            if len(word) >= 2:
                candidates[word] += 1
    elif source_lang in ("zh", "zh-cn", "zh-tw", "zh-hk"):
        for m in _CJK_PATTERN.finditer(clean):
            word = m.group().strip()
            if len(word) >= 2 and len(word) <= 6:
                candidates[word] += 1
    else:
        # Default: extract capitalized phrases
        for m in _LATIN_UPPER.finditer(clean):
            word = m.group().strip()
            parts = word.split()
            if len(parts) <= 4 and word.lower() not in _EN_STOP_WORDS:
                candidates[word] += 1

    # Filter by frequency
    MIN_FREQ = 3
    filtered = {k: v for k, v in candidates.items() if v >= MIN_FREQ}

    # Collect context for each candidate
    for word in filtered:
        ctx = []
        for s in segments:
            if word in s["source_text"]:
                ctx.append(s["source_text"][:100])
                if len(ctx) >= 3:
                    break
        contexts[word] = ctx

    result = []
    for word, freq in sorted(filtered.items(), key=lambda x: -x[1]):
        result.append({
            "candidate": word,
            "frequency": freq,
            "contexts": contexts.get(word, []),
            "length": len(word),
        })

    return result


def cmd_discover(args):
    cache = load_cache(args.cache)
    segments = cache.get("segments", [])
    source_lang = cache.get("source_language", args.source_lang)

    candidates = discover_candidates(segments, source_lang)

    output = json.dumps(candidates, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
            f.write("\n")
        print(f"Written: {args.output}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(output)
    print(f"\nDiscovered {len(candidates)} candidates", file=sys.stderr)


# ── Wikipedia Fetch ───────────────────────────────────────────

WIKI_API = "https://{lang}.wikipedia.org/w/api.php"


def _wiki_client() -> httpx.Client:
    headers = {
        "User-Agent": "subtitle-translate/1.0 (opencode skill; https://opencode.ai)"
    }
    return httpx.Client(headers=headers, follow_redirects=True)


def wiki_search(lang: str, term: str, client: httpx.Client) -> dict:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": term,
        "format": "json",
        "srlimit": 3,
    }
    try:
        resp = client.get(WIKI_API.format(lang=lang), params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def wiki_langlinks(lang: str, titles: str, target_lang: str,
                   client: httpx.Client) -> dict:
    params = {
        "action": "query",
        "prop": "langlinks",
        "titles": titles,
        "lllang": target_lang,
        "format": "json",
        "lllimit": 50,
    }
    try:
        resp = client.get(WIKI_API.format(lang=lang), params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _extract_all_candidates(cache_path: str) -> list[dict]:
    """Extract ALL unique capitalized phrases from the entire cache text,
    without frequency filtering. Every proper noun, every song title,
    every speaker name — everything."""
    cache = load_cache(cache_path)
    # Collect ALL text, stripping ASS/VTT/HTML tags
    all_text = ""
    for s in cache.get("segments", []):
        raw = s.get("source_text", "")
        raw = re.sub(r"\{[^}]*\}", "", raw)
        raw = re.sub(r"<[^>]*>", "", raw)
        all_text += raw + "\n"

    # Extract ALL capitalized phrases (2+ words) and single capitalized words
    # Look for: "Michael Jackson", "Billie Jean", "Beat It", "Thriller" etc.
    phrases = set(re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", all_text))
    single_words = set(re.findall(r"\b([A-Z][a-z]{2,})\b", all_text))

    # Filter out stop words and sentence starters
    stop = _EN_STOP_WORDS | {"You", "Don", "Let", "Get", "Now", "Just", "One",
                              "Sit", "Wait", "Sure", "Hi", "Ma", "Na", "Mr",
                              "God", "Hello", "Sir", "Sorry", "Excuse", "Alright",
                              "Really", "Watch", "Show", "Take", "Leave", "Uncle",
                              "Doctor", "Because", "Even", "Someone", "President",
                              "French", "Sugar", "Five", "Only", "Come", "Well",
                              "Know", "See", "Like", "Got", "Say", "Back", "Way",
                              "Right", "Still", "Never", "Oh", "Yes", "Hey",
                              "Look", "Tell", "Think", "Good", "Old", "Love",
                              "Want", "Need", "Said", "Went", "Will", "Can",
                              "Goes", "Going", "Keep", "All", "Ooh", "Yeah",
                              "Shh", "Ow", "Whoo", "Mm", "Hmm", "Uh", "Ah",
                              "Ow", "Hey", "Wow", "Man", "Boy", "Girl", "Kid",
                              "Everybody", "Nobody", "Somebody", "Anything",
                              "Nothing", "Everything", "Something"}

    results = []
    for p in sorted(phrases):
        if p.split()[0] not in stop:
            results.append({"candidate": p, "frequency": all_text.count(p),
                            "length": len(p), "source": "phrase"})
    for w in sorted(single_words):
        if w not in stop and len(w) >= 3 and w not in {r["candidate"] for r in results}:
            results.append({"candidate": w, "frequency": all_text.count(w),
                            "length": len(w), "source": "single"})

    results.sort(key=lambda x: (-x["frequency"], -x["length"]))
    return results


def cmd_fetch(args):
    if not HAS_HTTPX:
        print("Error: httpx required. pip install httpx", file=sys.stderr)
        sys.exit(1)

    src_lang = args.source_lang
    tgt_lang = args.target_lang
    region = args.region

    # Two modes: from candidates file, or directly from cache
    if args.cache:
        candidates = _extract_all_candidates(args.cache)
        print(f"Extracted {len(candidates)} potential terms from cache", file=sys.stderr)
    else:
        with open(args.candidates, "r", encoding="utf-8") as f:
            candidates = json.load(f)

    api_lang = _api_lang_for_region(tgt_lang, region)
    print(f"Fetching: {src_lang} -> {tgt_lang} (region={region}, api_lang={api_lang})",
          file=sys.stderr)

    client = _wiki_client()
    characters = []
    terms = []
    gaps = []

    for c in candidates[:args.limit]:
        candidate = c["candidate"]
        result = wiki_search(src_lang, candidate, client)

        pages = _extract_pages(result)
        if not pages:
            gaps.append({
                "candidate": candidate,
                "frequency": c["frequency"],
                "reason": "no_wiki_article",
                "contexts": c.get("contexts", [])[:2],
                "search_urls": {
                    f"wikipedia_{src_lang}": f"https://{src_lang}.wikipedia.org/w/index.php?search={candidate}",
                    f"wikipedia_{api_lang}": f"https://{api_lang}.wikipedia.org/w/index.php?search={candidate}",
                },
            })
            continue

        # Get langlinks for each page
        titles = "|".join(p["title"] for p in pages[:3])
        ll_result = wiki_langlinks(src_lang, titles, api_lang, client)

        matched = False
        for page_id, page_data in _parse_query_pages(ll_result).items():
            langlinks = page_data.get("langlinks", [])
            if langlinks:
                ll = langlinks[0]
                target_title = ll.get("*") or ll.get("title", "")

                is_name = _looks_like_name(candidate)
                render_key = "region" if region else "dst"

                entry = {
                    "canonical": candidate,
                    "render": target_title,
                    "region": {region: target_title} if region else {},
                    "aliases": [candidate],
                    "sources": [{
                        "type": "wikipedia",
                        "langlink": api_lang,
                        "url": f"https://{api_lang}.wikipedia.org/wiki/{_url_encode(target_title)}",
                        "confidence": "high",
                    }],
                }

                if is_name:
                    entry["gender"] = "-"
                    entry["note"] = ""
                    characters.append(entry)
                else:
                    terms.append({
                        "src": candidate,
                        "dst": target_title,
                        "region": {region: target_title} if region else {},
                        "category": "term",
                        "keep_source": False,
                        "sources": entry["sources"],
                    })
                matched = True
                break

        if not matched:
            gaps.append({
                "candidate": candidate,
                "frequency": c["frequency"],
                "reason": "no_langlink",
                "contexts": c.get("contexts", [])[:2],
                "search_urls": {
                    f"wikipedia_{src_lang}": f"https://{src_lang}.wikipedia.org/w/index.php?search={candidate}",
                    f"wikipedia_{api_lang}": f"https://{api_lang}.wikipedia.org/w/index.php?search={candidate}",
                },
            })

    client.close()

    glossary = {
        "characters": characters,
        "terms": terms,
        "non_translate_patterns": [
            {"pattern": "\\{\\\\p[0-9]+.*?\\{\\\\p0\\}", "category": "ass_drawing",
             "note": "ASS drawing commands"},
            {"pattern": "\\{\\\\k[f]?[0-9]+\\}", "category": "karaoke_timing",
             "note": "Karaoke timing tags"},
        ],
        "never_translate": [
            {"src": "OK", "note": "universal"},
            {"src": "Lt.", "note": "rank abbreviation"},
            {"src": "Dr.", "note": "title abbreviation"},
        ],
        "regions": [],
        "rules": [
            {"type": "line_max_chars", "value": 36},
            {"type": "line_max_count", "value": 2},
            {"type": "cps_max", "value": 12},
            {"type": "min_duration_s", "value": 1.0},
            {"type": "max_duration_s", "value": 6.0},
        ],
        "metadata": {
            "glossary_version": "1.0",
            "created_at": __import__("datetime").datetime.now().isoformat(),
            "source_language": src_lang,
            "target_language": tgt_lang,
            "target_region": region,
            "total_entries": len(characters) + len(terms),
            "filled_by_api": len(characters) + len(terms),
            "filled_by_webfetch": 0,
            "user_reviewed": False,
        },
        "_gaps": gaps,
    }

    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)

    print(f"\nCharacters: {len(characters)}", file=sys.stderr)
    print(f"Terms: {len(terms)}", file=sys.stderr)
    print(f"Gaps: {len(gaps)}", file=sys.stderr)
    print(f"Written: {out}", file=sys.stderr)
    print(f"\nGaps to fill via webfetch:", file=sys.stderr)
    for g in gaps:
        print(f"  {g['candidate']} ({g['reason']})", file=sys.stderr)


def cmd_lock(args):
    with open(args.input, "r", encoding="utf-8") as f:
        glossary = json.load(f)

    glossary["metadata"]["user_reviewed"] = True
    glossary["metadata"]["updated_at"] = __import__("datetime").datetime.now().isoformat()

    if "_gaps" in glossary:
        del glossary["_gaps"]

    out = args.out or args.input
    with open(out, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)

    print(f"Locked: {out}")
    print(f"Characters: {len(glossary.get('characters', []))}")
    print(f"Terms: {len(glossary.get('terms', []))}")


def _extract_pages(result: dict) -> list[dict]:
    try:
        return result["query"]["search"]
    except (KeyError, TypeError):
        return []


def _parse_query_pages(result: dict) -> dict:
    try:
        return result["query"]["pages"]
    except (KeyError, TypeError):
        return {}


def _url_encode(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


def _api_lang_for_region(tgt_lang: str, region: str) -> str:
    if tgt_lang == "zh":
        return f"zh"
    return tgt_lang


def _looks_like_name(word: str) -> bool:
    return bool(re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*$", word))


# ── Glossary Update ──────────────────────────────────────────

def _existing_keys(glossary: dict) -> set:
    """Collect all existing canonical names and aliases."""
    keys = set()
    for c in glossary.get("characters", []):
        keys.add(c["canonical"].lower())
        for a in c.get("aliases", []):
            keys.add(a.lower())
    for t in glossary.get("terms", []):
        keys.add(t["src"].lower())
    return keys


def cmd_update(args):
    """Update existing glossary with new candidates from cache."""
    # Load existing glossary
    if not os.path.exists(args.glossary):
        print(f"Error: glossary not found: {args.glossary}", file=sys.stderr)
        sys.exit(1)
    with open(args.glossary, "r", encoding="utf-8") as f:
        glossary = json.load(f)

    existing = _existing_keys(glossary)

    # Extract new candidates from cache
    cache = load_cache(args.cache)
    segments = cache.get("segments", [])
    all_text = "\n".join(
        re.sub(r"\{[^}]*\}", "", s.get("source_text", ""))
        for s in segments
    )
    all_text = re.sub(r"<[^>]*>", "", all_text)
    all_text = re.sub(r"\\[Nn]", " ", all_text)

    # Find capitalized words/phrases not in glossary
    candidates = set()
    for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", all_text):
        word = m.group().strip()
        if word.lower() not in existing and len(word) >= 3:
            candidates.add(word)

    # Filter common sentence starters
    stop = {"You", "Don", "Let", "Get", "Now", "Just", "One",
            "Sit", "Wait", "Sure", "Hi", "Ma", "Mr", "Sir",
            "Hello", "Sorry", "Please", "Thanks", "Well", "So",
            "Go", "Come", "See", "Know", "Look", "Think", "Say",
            "Yeah", "Yes", "No", "Oh", "Hey", "Wow", "Man",
            "God", "Jesus", "Christ", "Damn", "Shit", "Fuck"}
    candidates = {c for c in candidates if c.split()[0] not in stop}
    # Remove single-letter
    candidates = {c for c in candidates if len(c) >= 3}

    if not candidates:
        print("No new candidates found", file=sys.stderr)
        print(json.dumps({"updated": False, "new_candidates": 0}))
        return

    print(f"Found {len(candidates)} new candidate(s)", file=sys.stderr)
    for c in sorted(candidates)[:20]:
        print(f"  - {c}", file=sys.stderr)
    if len(candidates) > 20:
        print(f"  ... and {len(candidates) - 20} more", file=sys.stderr)

    # Try Wikipedia fetch for new candidates
    new_entries = {"characters": [], "terms": []}
    if args.source_lang and HAS_HTTPX:
        client = httpx.Client(headers={
            "User-Agent": "subtitle-translate/1.0 (opencode skill)",
        }, follow_redirects=True)
        api_lang = "zh" if args.target_lang == "zh" else args.target_lang
        for cand in sorted(candidates)[:args.limit]:
            result = wiki_search(args.source_lang, cand, client)
            pages = _extract_pages(result)
            if pages:
                titles = "|".join(p["title"] for p in pages[:3])
                ll_result = wiki_langlinks(args.source_lang, titles, api_lang, client)
                for page_id, page_data in _parse_query_pages(ll_result).items():
                    langlinks = page_data.get("langlinks", [])
                    if langlinks:
                        ll = langlinks[0]
                        target_title = ll.get("*") or ll.get("title", "")
                        is_name = _looks_like_name(cand)
                        entry = {
                            "canonical": cand, "render": target_title,
                            "region": {args.region: target_title} if args.region else {},
                            "aliases": [cand],
                            "sources": [{"type": "wikipedia", "confidence": "medium"}],
                        }
                        if is_name:
                            entry["gender"] = "-"
                            entry["note"] = ""
                            new_entries["characters"].append(entry)
                        else:
                            new_entries["terms"].append({
                                "src": cand, "dst": target_title,
                                "region": {args.region: target_title} if args.region else {},
                                "category": "term", "keep_source": False,
                            })
                        break
            else:
                pass  # No Wikipedia article, skip
        client.close()

    # Merge into existing glossary
    existing_names = {c["canonical"].lower() for c in glossary.get("characters", [])}
    existing_terms = {t["src"].lower() for t in glossary.get("terms", [])}
    for c in new_entries["characters"]:
        if c["canonical"].lower() not in existing_names:
            glossary.setdefault("characters", []).append(c)
            existing_names.add(c["canonical"].lower())
    for t in new_entries["terms"]:
        if t["src"].lower() not in existing_terms:
            glossary.setdefault("terms", []).append(t)
            existing_terms.add(t["src"].lower())

    # Update metadata
    glossary.setdefault("metadata", {})["updated_at"] = (
        __import__("datetime").datetime.now().isoformat())
    glossary["metadata"]["total_entries"] = (
        len(glossary.get("characters", [])) + len(glossary.get("terms", [])))

    out = args.out or args.glossary
    with open(out, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)

    print(f"Updated: {out}", file=sys.stderr)
    print(f"  Characters: {len(glossary.get('characters', []))}", file=sys.stderr)
    print(f"  Terms: {len(glossary.get('terms', []))}", file=sys.stderr)
    print(json.dumps({
        "updated": True, "new_characters": len(new_entries["characters"]),
        "new_terms": len(new_entries["terms"]), "new_candidates": len(candidates),
    }))


# ── CLI ────────────────────────────────────────────────────────


def main(argv=None):
    ap = argparse.ArgumentParser(description="Glossary management")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="Discover candidate terms from subtitle")
    d.add_argument("--cache", required=True, help="Path to cache.json")
    d.add_argument("--source-lang", default="", help="Source language code")
    d.add_argument("--output", "-o", help="Output file (default: stdout)")

    f = sub.add_parser("fetch", help="Fetch translations via Wikipedia API")
    f.add_argument("--candidates", help="Candidates JSON from discover")
    f.add_argument("--cache", help="Directly extract from cache.json (no prior discover needed)")
    f.add_argument("--source-lang", default="en", help="Source language code")
    f.add_argument("--target-lang", default="zh", help="Target language code")
    f.add_argument("--region", default="", help="Target region variant (tw/cn/hk)")
    f.add_argument("--out", "-o", required=True, help="Output glossary JSON path")
    f.add_argument("--limit", type=int, default=50, help="Max candidates to process")
    f.add_argument("--output-template", default="",
                   help="JSON string with default glossary fields")

    l = sub.add_parser("lock", help="Lock glossary after review")
    l.add_argument("--input", "-i", required=True, help="Populated glossary JSON")
    l.add_argument("--out", "-o", help="Output locked file (default: overwrite input)")

    u = sub.add_parser("update", help="Update existing glossary with new cache candidates")
    u.add_argument("--glossary", "-g", required=True, help="Existing glossary.locked.json")
    u.add_argument("--cache", "-c", required=True, help="Cache.json with new segments")
    u.add_argument("--source-lang", default="en", help="Source language for Wikipedia lookup")
    u.add_argument("--target-lang", default="zh", help="Target language for Wikipedia lookup")
    u.add_argument("--region", default="", help="Target region variant")
    u.add_argument("--out", "-o", help="Output path (default: overwrite input)")
    u.add_argument("--limit", type=int, default=30, help="Max Wikipedia lookups")

    args = ap.parse_args(argv)
    if args.command == "fetch" and not args.candidates and not args.cache:
        ap.error("fetch requires --candidates or --cache")
    if args.command == "discover":
        cmd_discover(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "lock":
        cmd_lock(args)
    elif args.command == "update":
        cmd_update(args)


if __name__ == "__main__":
    main()
