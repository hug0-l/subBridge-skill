"""Export translated cache.json back to subtitle file with format preservation.

Features:
  - Format-preserving template synthesis
  - Bilingual mode (source + translation)
  - Credit footer for SRT, header for ASS/VTT
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from parse import get_protector, detect_subtitle_format
from helpers import load_cache, save_cache


CREDIT_LINES = [
    "# ─────────────────────────────────────────────────────",
    "# AI-translated by subbridge",
    "# https://github.com/hug0-l/subBridge-skill",
    "# ─────────────────────────────────────────────────────"
]


def build_credit_block(cache: dict) -> str:
    src = cache.get("source_language", "")
    tgt = cache.get("target_language", "")
    region = cache.get("target_region", "")
    lang_info = f"({src} → {tgt}"
    if region:
        lang_info += f"-{region}"
    lang_info += ")"
    output = list(CREDIT_LINES)
    output.append(f"# Language: {lang_info}")
    output.append("")
    return "\n".join(output)


def is_sound_or_music(text: str) -> bool:
    """Check if segment is sound effect / music / on-screen only — skip bilingual."""
    t = text.strip()
    if not t:
        return True
    if re.match(r"^[\s♪♫♬]*$", t):
        return True
    if t.startswith("[") and t.endswith("]"):
        return True
    if t.startswith("(") and t.endswith(")"):
        return True
    if t.startswith("[畫面") or t.startswith("[聽唔清") or t.startswith("（重疊）"):
        return True
    return False


def build_bilingual(source: str, translation: str, order: str) -> str:
    """Combine source and translation for bilingual output."""
    if not translation or translation == source:
        return source
    src_lines = source.replace("\\N", "\n")
    tgt_lines = translation.replace("\\N", "\n")
    if order == "source-first":
        return src_lines + "\n" + tgt_lines
    else:
        return tgt_lines + "\n" + src_lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export cache.json to subtitle file")
    ap.add_argument("--cache", "-c", required=True, help="Input cache.json path")
    ap.add_argument("--output", "-o", required=True, help="Output subtitle file path")
    ap.add_argument("--format", "-f", default="auto",
                    choices=["auto", "srt", "ass", "vtt", "sub", "smi", "lrc"],
                    help="Output format (auto = original format from cache)")
    ap.add_argument("--region", default="", help="Target region variant")
    ap.add_argument("--encoding", default="", help="Output encoding")
    ap.add_argument("--no-credit", action="store_true",
                    help="Skip credit in output file")
    ap.add_argument("--bilingual", action="store_true",
                    help="Output bilingual (source + translation)")
    ap.add_argument("--bilingual-order", default="source-first",
                    choices=["source-first", "translation-first"],
                    help="Which language comes first (default: source-first)")
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

    # Bilingual mode: bypass protector, generate from scratch
    if args.bilingual:
        result = ""
    else:
        result = protector.export(segments, raw=raw_text)

    # Fallback SRT generation (when protector returns nothing or bilingual)
    if not result and segments:
        lines = []
        for seg in segments:
            source = seg.get("source_text", "")
            translation = seg.get("translated_text", "")
            p = seg.get("_preserved", {})
            raw_timing = p.get("raw_timing", "")
            if raw_timing:
                lines.append(str(seg["text_index"]))
                lines.append(raw_timing)
                if args.bilingual and not is_sound_or_music(source):
                    text = build_bilingual(source, translation or source, args.bilingual_order)
                else:
                    text = (translation or source).replace("\\N", "\n")
                lines.append(text)
                lines.append("")
        result = "\n".join(lines)

    encoding = args.encoding or cache.get("original_encoding", "utf-8")
    if encoding and encoding.lower() in ("ascii",):
        encoding = "utf-8"

    # Credit: SRT → footer, ASS/VTT → header
    credit_block = ""
    if not args.no_credit and result:
        credit_block = build_credit_block(cache)
        if fmt == "srt":
            # Append to end of file
            result = result.rstrip("\n") + "\n\n" + credit_block
        else:
            # Prepend (ASS/VTT support comments at start)
            result = credit_block + "\n" + result

    folder = os.path.dirname(args.output)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(args.output, "w", encoding=encoding) as f:
        f.write(result)

    translated_count = sum(1 for s in segments if s.get("translated_text"))
    modes = []
    if args.bilingual:
        modes.append("bilingual")
    if not args.no_credit:
        modes.append("credit")
    mode_str = f" [{','.join(modes)}]" if modes else ""
    print(f"Exported {len(segments)} segments ({translated_count} translated){mode_str}")
    print(f"Written: {args.output}")


if __name__ == "__main__":
    main()
