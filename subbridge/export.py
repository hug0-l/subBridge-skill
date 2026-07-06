"""Export translated cache.json back to subtitle file with format preservation.

Uses template synthesis: reads original raw text, replaces only the
text portion of each dialogue line. Non-text bytes stay identical.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from parse import get_protector, detect_subtitle_format
from helpers import load_cache, save_cache


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export cache.json to subtitle file")
    ap.add_argument("--cache", "-c", required=True, help="Input cache.json path")
    ap.add_argument("--output", "-o", required=True, help="Output subtitle file path")
    ap.add_argument("--format", "-f", default="auto",
                    choices=["auto", "srt", "ass", "vtt", "sub", "smi", "lrc"],
                    help="Output format (auto = original format from cache)")
    ap.add_argument("--region", default="", help="Target region variant")
    ap.add_argument("--encoding", default="", help="Output encoding")
    args = ap.parse_args(argv)

    cache = load_cache(args.cache)
    segments = cache.get("segments", [])

    fmt = args.format
    if fmt == "auto":
        fmt = cache.get("original_format", "srt")

    protector = get_protector(fmt)

    raw_text = None
    source_path = cache.get("source_path", "")
    if source_path and os.path.exists(source_path):
        try:
            with open(source_path, "r", encoding=cache.get("original_encoding", "utf-8")) as f:
                raw_text = f.read()
        except Exception:
            raw_text = None

    result = protector.export(segments, raw=raw_text)

    if not result and segments:
        lines = []
        for seg in segments:
            text = seg.get("translated_text") or seg["source_text"]
            p = seg.get("_preserved", {})
            raw_timing = p.get("raw_timing", "")
            if raw_timing:
                lines.append(str(seg["text_index"]))
                lines.append(raw_timing)
                lines.append(text.replace("\\N", "\n"))
                lines.append("")
        result = "\n".join(lines)

    encoding = args.encoding or cache.get("original_encoding", "utf-8")
    if encoding and encoding.lower() in ("ascii",):
        encoding = "utf-8"

    folder = os.path.dirname(args.output)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(args.output, "w", encoding=encoding) as f:
        f.write(result)

    translated_count = sum(1 for s in segments if s.get("translated_text"))
    print(f"Exported {len(segments)} segments ({translated_count} translated)")
    print(f"Written: {args.output}")


if __name__ == "__main__":
    main()
