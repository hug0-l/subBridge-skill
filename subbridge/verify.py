"""Verify translation quality and format integrity.

Checks:
  - CPS (characters per second) based on original timeline
  - Line length constraints (CJK-aware)
  - Min/max subtitle duration (1s-6s per industry standard)
  - Reading speed vs available time
  - Glossary compliance
  - Empty translations
  - Format integrity
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from helpers import load_cache, TranslationStatus


# Industry CPS defaults per market (from AVT research)
# Subtitling countries (Nordic, NL): higher reading speed
# Dubbing countries (DE, FR, IT, ES): lower reading speed
# Asia: CJK characters are visually denser
MARKET_CPS = {
    "nordic": 14,
    "western": 12,
    "asia": 10,
}

DEFAULT_MARKET = "asia"


def calc_cps(seg: dict) -> float:
    """Characters per second based on segment's actual timeline."""
    text = seg.get("translated_text") or seg.get("source_text", "")
    duration = seg["end_ms"] - seg["start_ms"]
    if duration <= 0:
        return 0
    plain = re.sub(r"\{[^}]*\}", "", text)
    plain = re.sub(r"<[^>]*>", "", plain)
    return len(plain) / (duration / 1000)


def calc_duration_ms(seg: dict) -> int:
    return seg["end_ms"] - seg["start_ms"]


def calc_lines(text: str) -> int:
    plain = re.sub(r"\{[^}]*\}", "", text)
    plain = re.sub(r"<[^>]*>", "", plain)
    return plain.count("\\N") + plain.count("\n") + 1


def cjk_visual_len(line: str) -> int:
    """CJK chars count as 2 visual units, Latin chars as 1."""
    return sum(2 if ord(c) >= 0x4E00 and ord(c) <= 0x9FFF else 1 for c in line)


def max_line_len(text: str, cjk_aware: bool = True) -> int:
    plain = re.sub(r"\{[^}]*\}", "", text)
    plain = re.sub(r"<[^>]*>", "", plain)
    lines = plain.replace("\\N", "\n").split("\n")
    if cjk_aware:
        return max((cjk_visual_len(l) for l in lines), default=0)
    return max((len(l) for l in lines), default=0)


def cmd_quality(args):
    cache = load_cache(args.cache)
    segments = cache.get("segments", [])
    market = args.market or cache.get("market", DEFAULT_MARKET)
    max_cps = args.cps or MARKET_CPS.get(market, MARKET_CPS[DEFAULT_MARKET])
    max_chars = args.max_chars or 36
    max_lines = args.max_lines or 2
    min_duration = args.min_duration
    max_duration = args.max_duration

    issues = []

    for seg in segments:
        if seg.get("translation_status") != TranslationStatus.TRANSLATED:
            continue
        text = seg.get("translated_text", "")
        duration_ms = calc_duration_ms(seg)
        duration_s = duration_ms / 1000

        if not text and seg.get("source_text", "").strip():
            issues.append({
                "text_index": seg["text_index"],
                "type": "empty_translation",
                "detail": "Source has content but translation is empty",
            })
            continue

        # Min/max duration checks
        if min_duration and duration_s < min_duration:
            issues.append({
                "text_index": seg["text_index"],
                "type": "duration_too_short",
                "detail": f"{duration_s:.1f}s < {min_duration}s min",
                "value": round(duration_s, 1),
            })

        if max_duration and duration_s > max_duration:
            issues.append({
                "text_index": seg["text_index"],
                "type": "duration_too_long",
                "detail": f"{duration_s:.1f}s > {max_duration}s max",
                "value": round(duration_s, 1),
            })

        # CPS check
        cps = calc_cps(seg)
        if cps > max_cps:
            severity = "WARN" if cps <= max_cps * 1.2 else "FAIL"
            issues.append({
                "text_index": seg["text_index"],
                "type": "cps_exceeded",
                "severity": severity,
                "detail": f"{cps:.1f} CPS > {max_cps} (market={market})",
                "value": round(cps, 1),
            })

        # Line count check
        if max_lines:
            nlines = calc_lines(text)
            if nlines > max_lines:
                issues.append({
                    "text_index": seg["text_index"],
                    "type": "too_many_lines",
                    "detail": f"{nlines} lines > {max_lines}",
                    "value": nlines,
                })

        # CJK-aware line length check
        if max_chars:
            mll = max_line_len(text, cjk_aware=True)
            if mll > max_chars:
                issues.append({
                    "text_index": seg["text_index"],
                    "type": "line_too_long",
                    "detail": f"max line {mll} visual units > {max_chars}",
                    "value": mll,
                })

    print(json.dumps(issues, ensure_ascii=False, indent=2))
    summary = {"total_issues": len(issues), "market": market,
               "cps_threshold": max_cps, "cpl_threshold": max_chars}
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


def cmd_completeness(args):
    cache = load_cache(args.cache)
    segments = cache.get("segments", [])

    untranslated = [
        s for s in segments
        if s.get("translation_status") != TranslationStatus.TRANSLATED
    ]

    empty_translated = [
        s for s in segments
        if s.get("translation_status") == TranslationStatus.TRANSLATED
        and not s.get("translated_text", "").strip()
        and s.get("source_text", "").strip()
    ]

    total = len(segments)
    done = total - len(untranslated)
    pct = (done / total * 100) if total else 0

    print(f"Completeness: {done}/{total} ({pct:.1f}%)", file=sys.stderr)

    if untranslated:
        print(f"  UNTRANSLATED segments: {len(untranslated)}", file=sys.stderr)
        for s in untranslated[:10]:
            src = s.get("source_text", "")[:80]
            print(f"    #{s['text_index']}: {src}", file=sys.stderr)
        if len(untranslated) > 10:
            print(f"    ... and {len(untranslated) - 10} more", file=sys.stderr)

    if empty_translated:
        print(f"  EMPTY translations (marked done but no text): {len(empty_translated)}",
              file=sys.stderr)
        for s in empty_translated[:5]:
            src = s.get("source_text", "")[:80]
            print(f"    #{s['text_index']}: {src}", file=sys.stderr)

    if args.output and untranslated:
        batch = [
            {
                "text_index": s["text_index"],
                "source_text": s["source_text"],
                "start_ms": s["start_ms"],
                "end_ms": s["end_ms"],
                "style": s.get("style", "Default"),
            }
            for s in untranslated
        ]
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False, indent=2)
        print(f"  Exported {len(untranslated)} untranslated to: {args.output}",
              file=sys.stderr)

    result = {
        "total": total,
        "translated": done,
        "untranslated": len(untranslated),
        "empty_translations": len(empty_translated),
        "completeness_pct": round(pct, 1),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify subtitle translations")
    sub = ap.add_subparsers(dest="command", required=True)

    q = sub.add_parser("quality", help="Check translation quality")
    q.add_argument("cache", help="Path to cache.json")
    q.add_argument("--cps", type=float, default=0,
                   help="Max CPS threshold (default: market-appropriate)")
    q.add_argument("--max-chars", type=int, default=0,
                   help="Max visual units per line (default: 36)")
    q.add_argument("--max-lines", type=int, default=0,
                   help="Max lines per subtitle (default: 2)")
    q.add_argument("--min-duration", type=float, default=1.0,
                   help="Min subtitle duration in seconds (default: 1.0)")
    q.add_argument("--max-duration", type=float, default=6.0,
                   help="Max subtitle duration in seconds (default: 6.0)")
    q.add_argument("--market", default="",
                   choices=["nordic", "western", "asia", ""],
                   help="Target market for CPS (default: from cache or asia)")

    g = sub.add_parser("glossary", help="Check glossary compliance")
    g.add_argument("cache", help="Path to cache.json")
    g.add_argument("glossary", help="Path to glossary.locked.json")

    integ = sub.add_parser("integrity", help="Check format integrity")
    integ.add_argument("--original", "-i", required=True, help="Original subtitle file")
    integ.add_argument("--output", "-o", required=True, help="Output subtitle file")

    c = sub.add_parser("completeness", help="Check translation completeness & export gaps")
    c.add_argument("cache", help="Path to cache.json")
    c.add_argument("--output", "-o", help="Export untranslated batch to this path")

    args = ap.parse_args(argv)
    if args.command == "quality":
        cmd_quality(args)
    elif args.command == "glossary":
        cmd_glossary(args)
    elif args.command == "integrity":
        cmd_integrity(args)
    elif args.command == "completeness":
        cmd_completeness(args)


if __name__ == "__main__":
    main()
