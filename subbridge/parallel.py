# -*- coding: utf-8 -*-
"""
Parallel subagent translation — split → subagents → serial collect.

Port from ainiee-translate parallel.py.

Iron rule: subagents write ONLY their agent_N_output.json, never cache.json.
Only the main agent's collect step writes to cache.json serially.
"""
import json, os, math, sys
from collections import Counter

from helpers import load_cache, save_cache, TranslationStatus


def cmd_split(args):
    """Split untranslated segments into N agent batches."""
    cache_path = args.cache
    out_dir = args.out_dir
    num_agents = args.agents

    os.makedirs(out_dir, exist_ok=True)

    cache = load_cache(cache_path)
    segs = cache.get('segments', [])

    untranslated = [s for s in segs
                    if s.get('translation_status', 0) == TranslationStatus.UNTRANSLATED
                    and s.get('source_text', '').strip()]

    if not untranslated:
        print("No untranslated segments found.")
        return

    chunk_size = math.ceil(len(untranslated) / num_agents)
    manifest = []

    for i in range(num_agents):
        start = i * chunk_size
        end = min(start + chunk_size, len(untranslated))
        chunk = untranslated[start:end]
        if not chunk:
            break

        input_data = [
            {'text_index': s['text_index'], 'source_text': s['source_text']}
            for s in chunk
        ]

        input_path = os.path.join(out_dir, f'agent_{i}_input.json')
        output_path = os.path.join(out_dir, f'agent_{i}_output.json')
        instr_path = os.path.join(out_dir, f'agent_{i}_instructions.md')

        with open(input_path, 'w', encoding='utf-8') as f:
            json.dump(input_data, f, ensure_ascii=False, indent=2)

        # Instructions for subagent
        first_idx = chunk[0]['text_index']
        last_idx = chunk[-1]['text_index']
        with open(instr_path, 'w', encoding='utf-8') as f:
            f.write(f"""# Agent {i}: Segments {first_idx}–{last_idx} ({len(chunk)} items)

## Rules
1. Read `agent_{i}_input.json` — array of {{text_index, source_text}}
2. Translate each source_text to HK Cantonese (咗,嘅,啦,喎,唔,係,哋)
3. Write output to `agent_{i}_output.json` as:
   [{{"text_index": N, "translated_text": "..."}}, ...]
4. **DO NOT write to cache.json** — only write your output file.
5. Preserve \\N line breaks and <i> tags.
6. Character names: refer to glossary if available.
""")

        manifest.append({
            'agent_id': i,
            'first_index': first_idx,
            'last_index': last_idx,
            'count': len(chunk),
            'input_file': input_path,
            'output_file': output_path,
        })

    manifest_path = os.path.join(out_dir, 'manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Split {len(untranslated)} segments into {len(manifest)} agents")
    print(f"Manifest: {manifest_path}")
    print(f"Agent dir: {out_dir}/")
    print("\nNext steps:")
    print(f"  1. Dispatch subagents to translate each agent_N_input.json")
    print(f"  2. Run: python -m parallel collect {out_dir}/ --cache {cache_path}")


def cmd_collect(args):
    """Collect agent outputs and write to cache serially."""
    manifest_path = os.path.join(args.out_dir, 'manifest.json')
    if not os.path.exists(manifest_path):
        print(f"Manifest not found: {manifest_path}")
        return

    with open(manifest_path, encoding='utf-8') as f:
        manifest = json.load(f)

    if args.dry_run:
        print("DRY RUN — checking agent outputs...")

    # Check all outputs exist
    all_results = []
    for entry in manifest:
        output_path = entry['output_file']
        if not os.path.exists(output_path):
            print(f"  MISSING: {output_path}")
            return
        with open(output_path, encoding='utf-8') as f:
            data = json.load(f)
        all_results.extend(data)

    # Check for duplicate text_index
    indices = [r['text_index'] for r in all_results]
    dupes = [idx for idx, count in Counter(indices).items() if count > 1]
    if dupes:
        print(f"  DUPLICATE text_index: {dupes}")
        return

    # Sort by text_index
    all_results.sort(key=lambda x: x['text_index'])

    print(f"  Collected {len(all_results)} translations from {len(manifest)} agents")

    if args.dry_run:
        first = all_results[0]
        last = all_results[-1]
        print(f"  Range: #{first['text_index']} – #{last['text_index']}")
        print("  DRY RUN complete — no changes written")
        return

    # Write to cache serially
    cache = load_cache(args.cache)
    segs = cache['segments']
    seg_map = {s['text_index']: s for s in segs}

    written = 0
    for r in all_results:
        idx = r['text_index']
        if idx in seg_map:
            seg_map[idx]['translated_text'] = r['translated_text']
            seg_map[idx]['translation_status'] = TranslationStatus.TRANSLATED
            written += 1

    save_cache(cache, args.cache)
    print(f"  Written {written} translations to cache.json")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Parallel subagent translation")
    sub = ap.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('split', help='Split cache into N agent batches')
    s.add_argument('--cache', required=True, help='Path to cache.json')
    s.add_argument('--out-dir', required=True, help='Output directory for agent files')
    s.add_argument('--agents', type=int, default=4, help='Number of agents')

    c = sub.add_parser('collect', help='Collect agent outputs and write to cache')
    c.add_argument('out_dir', help='Agent output directory (with manifest.json)')
    c.add_argument('--cache', required=True, help='Path to cache.json')
    c.add_argument('--dry-run', action='store_true', help='Validate without writing')

    args = ap.parse_args(argv)
    if args.cmd == 'split':
        cmd_split(args)
    elif args.cmd == 'collect':
        cmd_collect(args)


if __name__ == '__main__':
    main()
