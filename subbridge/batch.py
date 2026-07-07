"""Batch read/write translation segments with auto-translate support.

Read: outputs untranslated segments as JSON for agent to translate.
  --auto: runs built-in auto-translate engine instead of manual agent translation.
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
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_str)
            f.write("\n")
        print(f"Batch: {len(result)} segments, {remaining} remaining", file=sys.stderr)
        print(f"Written: {args.output}", file=sys.stderr)
    else:
        print(f"Batch: {len(result)} segments, {remaining} remaining")
        print(output_str)


def cmd_read_auto(args):
    """Read batch and auto-translate it, writing translations directly."""
    from auto_translate import AutoTranslate

    cache = load_cache(args.cache)
    segments = cache.get("segments", [])
    size = args.size

    untranslated = [
        s for s in segments
        if s.get("translation_status") == TranslationStatus.UNTRANSLATED
    ]

    batch = untranslated[:size]
    if not batch:
        print("No untranslated segments remaining", file=sys.stderr)
        return

    context = args.context or cache.get("context", "auto")
    engine = AutoTranslate(
        glossary_path=args.glossary,
        tm_path=args.tm,
        source_lang=cache.get("source_language", "en"),
        target_lang=cache.get("target_language", "zh"),
        region=cache.get("target_region", "hk"),
        context=context,
    )

    translations, uncertain, stats = engine.translate_batch(batch)

    # Write translations
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(translations, f, ensure_ascii=False, indent=2)

    # Write uncertain items separately if any
    uncertain_path = None
    if uncertain and args.uncertain:
        uncertain_path = args.uncertain
        os.makedirs(os.path.dirname(uncertain_path) or ".", exist_ok=True)
        with open(uncertain_path, "w", encoding="utf-8") as f:
            json.dump(uncertain, f, ensure_ascii=False, indent=2)

    # Save updated TM
    if args.tm_save:
        engine._save_tm(args.tm_save)

    remaining = max(0, len(untranslated) - size)
    auto = len(batch) - len(uncertain)
    print(f"Auto-batch: {len(batch)} segments, {auto} auto, "
          f"{len(uncertain)} uncertain, {remaining} remaining", file=sys.stderr)
    if args.output:
        print(f"Written: {args.output}", file=sys.stderr)
    if uncertain_path:
        print(f"Uncertain: {uncertain_path}", file=sys.stderr)


def _validate_translation(item: dict) -> str:
    """Ensure translated_text is always a string. Fix common subagent bugs."""
    raw = item.get("translated_text", "")
    if isinstance(raw, list):
        print(f"  ⚠ Fixed list→string for segment #{item.get('text_index', '?')}", file=sys.stderr)
        return "\n".join(str(x) for x in raw if x is not None)
    if not isinstance(raw, str):
        return str(raw) if raw is not None else ""
    return raw


def _validate_translations(translations: list) -> list:
    """Full validation pass over subagent output."""
    validated = []
    empty_count = 0
    for item in translations:
        if "text_index" not in item:
            print(f"  ⚠ Skipped entry without text_index", file=sys.stderr)
            continue
        text = _validate_translation(item)
        if not text.strip():
            empty_count += 1
        item["translated_text"] = text
        validated.append(item)
    if empty_count:
        print(f"  ⚠ {empty_count} segment(s) have empty translations", file=sys.stderr)
    return validated


def _load_tm(path: str) -> dict:
    import json as _json
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    return {"exact": {}, "version": "1.0"}


def _save_tm(tm: dict, path: str):
    if path:
        import json as _json
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(tm, f, ensure_ascii=False, indent=2)


def _tm_key(text: str) -> str:
    """Normalize text for TM lookup."""
    import re as _re
    from helpers import normalize_apostrophes, strip_newline_markers
    t = normalize_apostrophes(text)
    t = strip_newline_markers(t)
    t = _re.sub(r"[^\w\s]", "", t.lower())
    t = _re.sub(r"\s+", " ", t).strip()
    return t


def cmd_write(args):
    cache = load_cache(args.cache)
    segments = cache.get("segments", [])
    seg_map = {s["text_index"]: s for s in segments}

    with open(args.translations, "r", encoding="utf-8") as f:
        translations = json.load(f)

    # Validate
    translations = _validate_translations(translations)

    # Load TM for update
    tm = _load_tm(args.tm)

    applied = 0
    fixed = 0
    tm_updated = 0
    for item in translations:
        idx = item["text_index"]
        text = item["translated_text"]
        if isinstance(item.get("translated_text", ""), list) or \
           isinstance(item.get("translated_text", ""), list):
            fixed += 1
        if idx in seg_map:
            seg_map[idx]["translated_text"] = text
            seg_map[idx]["translation_status"] = TranslationStatus.TRANSLATED
            applied += 1

            # Update TM from subagent translations
            if args.update_tm and args.tm:
                source = seg_map[idx].get("source_text", "")
                target = text
                if source and target and len(source) > 3:
                    key = _tm_key(source)
                    tm.setdefault("exact", {})[key] = target
                    tm_updated += 1

    save_cache(cache, args.cache)

    # Save updated TM
    if tm_updated > 0 and args.update_tm and args.tm:
        _save_tm(tm, args.tm)

    msg = f"Applied {applied} translation(s)"
    if fixed:
        msg += f" ({fixed} auto-fixed)"
    if tm_updated:
        msg += f" ({tm_updated} TM entries added)"
    print(msg)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Batch read/write translations")
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("read", help="Read untranslated batch")
    r.add_argument("cache", help="Path to cache.json")
    r.add_argument("--size", type=int, default=100, help="Batch size")
    r.add_argument("--output", "-o", help="Write batch to file instead of stdout")
    r.add_argument("--auto", action="store_true", help="Auto-translate using built-in engine")
    r.add_argument("--glossary", help="Path to glossary.locked.json (required for --auto)")
    r.add_argument("--tm", help="Path to translation memory JSON")
    r.add_argument("--tm-save", help="Path to save updated TM")
    r.add_argument("--uncertain", help="Output uncertain items to file")
    r.add_argument("--context", default="auto",
                    choices=["military", "medical", "casual", "auto"],
                    help="Context for disambiguation (default: auto-infer)")

    w = sub.add_parser("write", help="Write translated batch back")
    w.add_argument("cache", help="Path to cache.json")
    w.add_argument("translations", help="Path to translations JSON file")
    w.add_argument("--update-tm", action="store_true",
                   help="Update translation memory with new translations")
    w.add_argument("--tm", help="Path to TM JSON file")

    args = ap.parse_args(argv)
    if args.command == "read":
        if args.auto:
            cmd_read_auto(args)
        else:
            cmd_read(args)
    elif args.command == "write":
        cmd_write(args)


if __name__ == "__main__":
    main()
