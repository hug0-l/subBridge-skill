"""Shared utilities for subtitle-translate skill.

Format protection layer: each format knows exactly which bytes are safe to modify.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

CACHE_VERSION = "1.0"

SEGMENT_KEYS = [
    "text_index", "start_ms", "end_ms", "source_text",
    "translated_text", "translation_status",
    "style", "layer", "format",
]


class TranslationStatus:
    UNTRANSLATED = 0
    TRANSLATED = 1
    POLISHED = 2
    EXCLUDED = 7


def make_cache_path(work_dir: str) -> str:
    return os.path.join(work_dir, "cache.json")


def load_cache(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache: dict, path: str) -> None:
    bak = path + ".bak." + datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.path.exists(path):
        os.replace(path, bak)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def ms_to_timecode(ms: int, fmt: str = "srt") -> str:
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms_f = divmod(ms, 1000)
    if fmt == "srt":
        return f"{h:02d}:{m:02d}:{s:02d},{ms_f:03d}"
    elif fmt == "ass":
        return f"{h}:{m:02d}:{s:02d}.{ms_f // 10:02d}"
    elif fmt == "vtt":
        return f"{h:02d}:{m:02d}:{s:02d}.{ms_f:03d}"
    return f"{h:02d}:{m:02d}:{s:02d},{ms_f:03d}"


def timecode_to_ms(tc: str) -> int:
    tc = tc.replace(",", ".").strip()
    parts = tc.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = "0"
        m, s = parts
    else:
        return 0
    try:
        sec_parts = s.split(".")
        sec = int(sec_parts[0])
        ms = int(sec_parts[1].ljust(3, "0")[:3]) if len(sec_parts) > 1 else 0
        return int(h) * 3600000 + int(m) * 60000 + sec * 1000 + ms
    except (ValueError, IndexError):
        return 0


def detect_subtitle_format(text: str) -> str:
    first_500 = text[:500].strip()
    if first_500.startswith("\ufeff"):
        first_500 = first_500[1:]
    if first_500.startswith("WEBVTT"):
        return "vtt"
    if first_500.startswith("<?xml") and "<tt " in first_500:
        return "ttml"
    if first_500.startswith("[Script Info]"):
        return "ass"
    if first_500.startswith("<SAMI") or first_500.startswith("<SAMI"):
        return "smi"
    lines = [l.strip() for l in first_500.split("\n") if l.strip()]
    if lines and re.match(r"^\d+$", lines[0]):
        for l in lines[1:3]:
            if "-->" in l:
                return "srt"
    for l in lines[:10]:
        if re.match(r"^\{\d+\}\{\d+\}", l):
            return "sub"
    for l in lines[:10]:
        if re.match(r"^\[(\d+|ti|ar|al|by|offset):", l):
            return "lrc"
    if first_500.startswith("<?xml") or first_500.startswith("<tt "):
        return "ttml"
    return "srt"


def normalize_apostrophes(text: str) -> str:
    """Normalize Unicode quotes/apostrophes to ASCII for reliable matching.
    
    Handles curly single quotes, curly double quotes, dashes, and non-breaking spaces.
    Critical for matching subtitle source text (often has curly quotes) against
    translation dictionaries (which use straight quotes).
    """
    text = text.replace("\u2018", "'").replace("\u2019", "'")  # ' '
    text = text.replace("\u201c", '"').replace("\u201d", '"')  # " "
    text = text.replace("\u2013", "-").replace("\u2014", "--") # – —
    text = text.replace("\u00a0", " ")                         # non-breaking space
    return text


def strip_newline_markers(text: str) -> str:
    """Remove SRT/ASS newline markers and collapse whitespace.
    
    SRT uses backslash-N for line breaks. Dict keys don't have them.
    Always call this before looking up source text in a pattern dict.
    """
    text = text.replace("\\N", " ").replace("\\n", " ").replace("\n", " ").replace("\r", " ")
    text = re.sub(r' +', ' ', text).strip()
    return text


def normalize_text(text: str) -> str:
    """Full normalization: apostrophes, newlines, whitespace.
    
    One function for all text normalization before pattern matching.
    """
    text = normalize_apostrophes(text)
    text = strip_newline_markers(text)
    return text


def make_cache(segments: list[dict], source_path: str = "", source_lang: str = "",
               target_lang: str = "", target_region: str = "",
               original_format: str = "", original_encoding: str = "utf-8",
               raw_header: str = "") -> dict:
    return {
        "version": CACHE_VERSION,
        "created_at": datetime.now().isoformat(),
        "source_path": source_path,
        "source_language": source_lang,
        "target_language": target_lang,
        "target_region": target_region,
        "original_format": original_format,
        "original_encoding": original_encoding,
        "raw_header": raw_header,
        "segments": segments,
    }
