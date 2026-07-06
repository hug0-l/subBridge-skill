"""Agent translation helper script.

Usage:
  1. First read a batch:
     python batch.py read work/cache.json --size 50 --output work/batch.json

  2. Agent translates: reads batch.json, writes translations_NNN.json
     python translate_batch.py --input work/batch.json \\
       --glossary work/glossary.locked.json \\
       --rules ../references/translation_rules.md \\
       --region hk \\
       --output work/translations_001.json

  3. Write back:
     python batch.py write work/cache.json work/translations_001.json

This script simply loads the batch, prints formatted text for the agent,
accepts translations, and writes the output.
"""
import json, argparse, sys, os, re

def main():
    ap = argparse.ArgumentParser(description="Translate a batch of subtitle segments")
    ap.add_argument("--input", "-i", required=True, help="Input batch.json")
    ap.add_argument("--glossary", "-g", help="glossary.locked.json")
    ap.add_argument("--rules", help="translation_rules.md")
    ap.add_argument("--region", default="", help="Target region (tw/cn/hk/pt/br...)")
    ap.add_argument("--output", "-o", required=True, help="Output translations.json")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        batch = json.load(f)

    # Load glossary for name mapping
    name_map = {}
    if args.glossary and os.path.exists(args.glossary):
        with open(args.glossary, encoding="utf-8") as f:
            gl = json.load(f)
        for ch in gl.get("characters", []):
            region = ch.get("region", {})
            render = region.get(args.region, ch.get("render", ch["canonical"]))
            name_map[ch["canonical"]] = render
            for alias in ch.get("aliases", []):
                name_map[alias] = render
        for t in gl.get("terms", []):
            region = t.get("region", {})
            if args.region in region:
                name_map[t["src"]] = region[args.region]

    # Display segments for translation
    for seg in batch:
        idx = seg["text_index"]
        src = seg["source_text"]
        print(f"--- Segment {idx} ---")
        print(src)
        print()

    # Accept translations - each line: text_index||translated_text
    # Or: the agent can write the translations JSON directly
    # For now, just create a stub output
    translations = []
    for seg in batch:
        idx = seg["text_index"]
        src = seg["source_text"]
        # Agent replaces this with actual translation
        translations.append({"text_index": idx, "translated_text": src})

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(translations, f, ensure_ascii=False, indent=2)

    print(f"\nWritten {len(translations)} translations to {args.output}")

if __name__ == "__main__":
    main()
