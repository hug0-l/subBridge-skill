"""Verify translation quality and format integrity.

Checks:
  - CPD (characters per second) readability
  - Line length constraints
  - Glossary compliance (names/terms preserved correctly)
  - Empty translations
  - Format integrity (non-text bytes unchanged)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from helpers import load_cache, TranslationStatus


def calc_cps(seg: dict) -> float:
    text = seg.get("translated_text") or seg.get("source_text", "")
    duration = seg["end_ms"] - seg["start_ms"]
    if duration <= 0:
        return 0
    plain = re.sub(r"\{[^}]*\}", "", text)
    plain = re.sub(r"<[^>]*>", "", plain)
    return len(plain) / (duration / 1000)


def calc_lines(text: str) -> int:
    plain = re.sub(r"\{[^}]*\}", "", text)
    plain = re.sub(r"<[^>]*>", "", plain)
    return plain.count("\\N") + plain.count("\n") + 1


def max_line_len(text: str) -> int:
    plain = re.sub(r"\{[^}]*\}", "", text)
    plain = re.sub(r"<[^>]*>", "", plain)
    lines = plain.replace("\\N", "\n").split("\n")
    return max((len(l) for l in lines), default=0)


def cmd_quality(args):
    cache = load_cache(args.cache)
    segments = cache.get("segments", [])
    max_cps = args.cps
    max_chars = args.max_chars
    max_lines = args.max_lines

    issues = []

    for seg in segments:
        if seg.get("translation_status") != TranslationStatus.TRANSLATED:
            continue
        text = seg.get("translated_text", "")
        if not text and seg.get("source_text", "").strip():
            issues.append({
                "text_index": seg["text_index"],
                "type": "empty_translation",
                "detail": "Source has content but translation is empty",
            })
            continue

        if max_cps:
            cps = calc_cps(seg)
            if cps > max_cps:
                issues.append({
                    "text_index": seg["text_index"],
                    "type": "cps_exceeded",
                    "detail": f"CPD={cps:.1f} > {max_cps}",
                    "value": round(cps, 1),
                })

        if max_lines:
            nlines = calc_lines(text)
            if nlines > max_lines:
                issues.append({
                    "text_index": seg["text_index"],
                    "type": "too_many_lines",
                    "detail": f"{nlines} lines > {max_lines}",
                    "value": nlines,
                })

        if max_chars:
            mll = max_line_len(text)
            if mll > max_chars:
                issues.append({
                    "text_index": seg["text_index"],
                    "type": "line_too_long",
                    "detail": f"max line {mll} chars > {max_chars}",
                    "value": mll,
                })

    print(json.dumps(issues, ensure_ascii=False, indent=2))
    print(f"\nTotal issues: {len(issues)}", file=sys.stderr)


def cmd_glossary(args):
    cache = load_cache(args.cache)
    with open(args.glossary, "r", encoding="utf-8") as f:
        glossary = json.load(f)

    segments = cache.get("segments", [])
    characters = glossary.get("characters", [])
    terms = glossary.get("terms", [])

    name_map = {}
    for ch in characters:
        src = ch.get("canonical", "")
        render = ch.get("render", "")
        region = ch.get("region", {})
        aliases = ch.get("aliases", [])
        all_names = [src] + aliases
        if region:
            region_render = region.get(cache.get("target_region", ""))
            if region_render:
                render = region_render
        name_map[src] = {
            "canonical": src,
            "render": render,
            "all_names": set(all_names),
            "keep_source": ch.get("forced_keep", True),
        }

    term_map = {}
    for t in terms:
        src = t.get("src", "")
        dst = t.get("dst", "")
        region = t.get("region", {})
        if region:
            region_dst = region.get(cache.get("target_region", ""))
            if region_dst:
                dst = region_dst
        term_map[src] = {
            "src": src,
            "dst": dst,
            "keep_source": t.get("keep_source", False),
        }

    issues = []

    for seg in segments:
        if seg.get("translation_status") != TranslationStatus.TRANSLATED:
            continue
        src = seg.get("source_text", "")
        tgt = seg.get("translated_text", "")
        if not src or not tgt:
            continue

        for ch_name, ch_info in name_map.items():
            if ch_info["keep_source"]:
                if ch_name in src and ch_name not in tgt:
                    # Check if any alias is in target
                    if not any(a in tgt for a in ch_info["all_names"]):
                        issues.append({
                            "text_index": seg["text_index"],
                            "type": "name_not_preserved",
                            "detail": f"'{ch_name}' should appear in translation",
                            "name": ch_name,
                        })

        for term_src, term_info in term_map.items():
            if not term_info["keep_source"] and term_info["dst"]:
                if term_src in src and term_info["dst"] not in tgt:
                    if term_src not in tgt:
                        issues.append({
                            "text_index": seg["text_index"],
                            "type": "term_not_translated",
                            "detail": f"'{term_src}' should be '{term_info['dst']}'",
                            "term": term_src,
                        })

    print(json.dumps(issues, ensure_ascii=False, indent=2))
    print(f"\nTotal glossary issues: {len(issues)}", file=sys.stderr)


def cmd_integrity(args):
    """Compare original and output for non-text differences."""
    with open(args.original, "r", encoding="utf-8-sig") as f:
        orig = f.read()
    with open(args.output, "r", encoding="utf-8-sig") as f:
        out = f.read()

    orig_lines = orig.split("\n")
    out_lines = out.split("\n")

    differences = []
    max_len = min(len(orig_lines), len(out_lines))

    for i in range(max_len):
        if orig_lines[i] != out_lines[i]:
            differences.append({
                "line": i + 1,
                "original": orig_lines[i][:120],
                "output": out_lines[i][:120],
            })

    if len(orig_lines) != len(out_lines):
        differences.append({
            "line": 0,
            "original": f"{len(orig_lines)} lines",
            "output": f"{len(out_lines)} lines",
            "type": "line_count_mismatch",
        })

    if differences:
        print(f"Format integrity: {len(differences)} difference(s) found")
        print(json.dumps(differences[:50], ensure_ascii=False, indent=2))
    else:
        print("Format integrity: PASS (no non-text differences)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify subtitle translations")
    sub = ap.add_subparsers(dest="command", required=True)

    q = sub.add_parser("quality", help="Check translation quality")
    q.add_argument("cache", help="Path to cache.json")
    q.add_argument("--cps", type=float, default=0, help="Max CPD threshold")
    q.add_argument("--max-chars", type=int, default=0, help="Max chars per line")
    q.add_argument("--max-lines", type=int, default=0, help="Max lines per subtitle")

    g = sub.add_parser("glossary", help="Check glossary compliance")
    g.add_argument("cache", help="Path to cache.json")
    g.add_argument("glossary", help="Path to glossary.locked.json")

    integ = sub.add_parser("integrity", help="Check format integrity")
    integ.add_argument("--original", "-i", required=True, help="Original subtitle file")
    integ.add_argument("--output", "-o", required=True, help="Output subtitle file")

    args = ap.parse_args(argv)
    if args.command == "quality":
        cmd_quality(args)
    elif args.command == "glossary":
        cmd_glossary(args)
    elif args.command == "integrity":
        cmd_integrity(args)


if __name__ == "__main__":
    main()
