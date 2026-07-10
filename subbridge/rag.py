# -*- coding: utf-8 -*-
"""
RAG context — extract recent translations for cross-episode consistency.

Port from ainiee-translate rag.py.
Agent injects this context into system prompt before translating new batch.
"""
import json, os, sys, time
from pathlib import Path


def get_context(cache_path: str, last_n: int = 20, fmt: str = 'markdown') -> str:
    """Extract last N translated segments as context."""
    with open(cache_path, encoding='utf-8') as f:
        cache = json.load(f)

    segs = cache.get('segments', [])
    translated = []
    for s in segs:
        status = s.get('translation_status', 0)
        tgt = s.get('translated_text', '')
        src = s.get('source_text', '')
        if status in (1, 2) and tgt and tgt != src:
            translated.append({
                'index': s.get('text_index', 0),
                'source': src[:120],
                'target': tgt[:120],
            })

    recent = translated[-last_n:]

    if fmt == 'json':
        return json.dumps(recent, ensure_ascii=False, indent=2)

    # Markdown
    lines = [
        f"## Recent Translation Context ({len(recent)} segments)",
        "",
    ]
    for r in recent:
        lines.append(f"- #{r['index']}")
        lines.append(f"  **SRC:** {r['source']}")
        lines.append(f"  **TGT:** {r['target']}")
        lines.append("")

    lines.append(
        "Use the above as reference for character names, terminology, "
        "and style consistency."
    )
    return "\n".join(lines)


def cmd_context(args):
    result = get_context(args.cache, args.last, args.format)
    print(result)


def cmd_serve(args):
    """Poll cache.json and print updated context when translation count changes."""
    last_count = -1
    cache_path = args.cache
    while True:
        try:
            with open(cache_path, encoding='utf-8') as f:
                cache = json.load(f)
            segs = cache.get('segments', [])
            done = sum(1 for s in segs if s.get('translation_status', 0) in (1, 2)
                       and s.get('translated_text', ''))
            if done != last_count:
                last_count = done
                ctx = get_context(cache_path, args.last, args.format)
                print(f"\n--- Context updated ({done} translated) ---")
                print(ctx)
        except (json.JSONDecodeError, FileNotFoundError):
            pass
        time.sleep(args.interval)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="RAG context for translation consistency")
    sub = ap.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('context', help='Get recent translation context')
    c.add_argument('--cache', required=True, help='Path to cache.json')
    c.add_argument('--last', type=int, default=20, help='Number of recent segments')
    c.add_argument('--format', choices=['markdown', 'json'], default='markdown')

    s = sub.add_parser('serve', help='Watch mode: poll and print updates')
    s.add_argument('--cache', required=True)
    s.add_argument('--last', type=int, default=20)
    s.add_argument('--interval', type=int, default=5, help='Poll interval in seconds')
    s.add_argument('--format', choices=['markdown', 'json'], default='markdown')

    args = ap.parse_args(argv)
    if args.cmd == 'context':
        cmd_context(args)
    elif args.cmd == 'serve':
        cmd_serve(args)


if __name__ == '__main__':
    main()
