"""Batch read/write translation segments.

Read: outputs untranslated segments as JSON for agent to translate.
Write: reads translated JSON and writes back into cache.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from helpers import load_cache, save_cache, TranslationStatus


def cmd_read(args):
    cache = load_cache(args.cache)
    segments = cache.get("segments", [])
    size = args.size

    untranslated = [
        s for s in segments
        if s.get("translation_status") == TranslationStatus.UNTRANSLATED
    ]

    batch = untranslated[:size]
    result = [
        {
            "text_index": s["text_index"],
            "source_text": s["source_text"],
            "start_ms": s["start_ms"],
            "end_ms": s["end_ms"],
            "style": s.get("style", "Default"),
        }
        for s in batch
    ]

    remaining = max(0, len(untranslated) - size)
    output_str = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_str)
            f.write("\n")
        print(f"Batch: {len(result)} segments, {remaining} remaining", file=sys.stderr)
        print(f"Written: {args.output}", file=sys.stderr)
    else:
        print(f"Batch: {len(result)} segments, {remaining} remaining")
        print(output_str)


def cmd_write(args):
    cache = load_cache(args.cache)
    segments = cache.get("segments", [])
    seg_map = {s["text_index"]: s for s in segments}

    with open(args.translations, "r", encoding="utf-8") as f:
        translations = json.load(f)

    applied = 0
    for item in translations:
        idx = item["text_index"]
        text = item.get("translated_text", "")
        if idx in seg_map:
            seg_map[idx]["translated_text"] = text
            seg_map[idx]["translation_status"] = TranslationStatus.TRANSLATED
            applied += 1

    save_cache(cache, args.cache)
    print(f"Applied {applied} translation(s)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Batch read/write translations")
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("read", help="Read untranslated batch")
    r.add_argument("cache", help="Path to cache.json")
    r.add_argument("--size", type=int, default=100, help="Batch size")
    r.add_argument("--output", "-o", help="Write batch to file instead of stdout")

    w = sub.add_parser("write", help="Write translated batch back")
    w.add_argument("cache", help="Path to cache.json")
    w.add_argument("translations", help="Path to translations JSON file")

    args = ap.parse_args(argv)
    if args.command == "read":
        cmd_read(args)
    elif args.command == "write":
        cmd_write(args)


if __name__ == "__main__":
    main()
