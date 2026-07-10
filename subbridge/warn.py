# -*- coding: utf-8 -*-
"""
Statistical anomaly warnings for translated cache.

Port from ainiee-translate warn.py. Four checks:
  length_anomaly       — target too short/long vs source
  line_count_mismatch  — \\n count differs
  repeated_target      — same target for different sources
  capitalization_drop  — ALL-CAPS source word missing in target
"""
import json, os, re, sys
from collections import Counter

cn = re.compile(r'[\u4e00-\u9fff\u3000-\u303f]')
caps_pattern = re.compile(r'\b[A-Z][A-Z]+[A-Z]\b')
caps_stop = {'THE', 'AND', 'FOR', 'NOT', 'YOU', 'ALL', 'CAN', 'HAD', 'HER',
             'WAS', 'ONE', 'OUR', 'OUT', 'HAS', 'HAVE', 'ARE', 'BUT'}


def check_length_anomaly(segs, ratio_min=0.2, ratio_max=5.0):
    issues = []
    for s in segs:
        status = s.get('translation_status', 0)
        if status not in (1, 2):
            continue
        src = s.get('source_text', '')
        tgt = s.get('translated_text', '')
        if not src or not tgt or len(src) < 5:
            continue
        ratio = len(tgt) / len(src)
        if ratio < ratio_min:
            issues.append({
                'type': 'length_anomaly_short',
                'text_index': s.get('text_index', 0),
                'ratio': round(ratio, 2),
                'source': src[:80], 'target': tgt[:80],
            })
        elif ratio > ratio_max:
            issues.append({
                'type': 'length_anomaly_long',
                'text_index': s.get('text_index', 0),
                'ratio': round(ratio, 2),
                'source': src[:80], 'target': tgt[:80],
            })
    return issues


def check_line_count_mismatch(segs):
    issues = []
    for s in segs:
        status = s.get('translation_status', 0)
        if status not in (1, 2):
            continue
        src = s.get('source_text', '')
        tgt = s.get('translated_text', '')
        if not src or not tgt:
            continue
        src_lines = src.count('\\N') + src.count('\n')
        tgt_lines = tgt.count('\\N') + tgt.count('\n')
        if src_lines != tgt_lines:
            issues.append({
                'type': 'line_count_mismatch',
                'text_index': s.get('text_index', 0),
                'src_lines': src_lines, 'tgt_lines': tgt_lines,
                'source': src[:60], 'target': tgt[:60],
            })
    return issues


def check_repeated_target(segs, min_repeat_len=20):
    # Build map: target -> [(text_index, source)]
    target_map = {}
    for s in segs:
        status = s.get('translation_status', 0)
        if status not in (1, 2):
            continue
        src = s.get('source_text', '')
        tgt = s.get('translated_text', '')
        if not src or not tgt or len(tgt) < min_repeat_len:
            continue
        if src == tgt:
            continue
        tgt_clean = tgt.strip()
        if tgt_clean not in target_map:
            target_map[tgt_clean] = []
        target_map[tgt_clean].append((s.get('text_index', 0), src[:60]))

    issues = []
    for tgt, entries in target_map.items():
        if len(entries) >= 2:
            unique_src = set(e[1] for e in entries)
            if len(unique_src) >= 2:
                issues.append({
                    'type': 'repeated_target',
                    'target': tgt[:80],
                    'count': len(entries),
                    'entries': [(idx, src) for idx, src in entries[:5]],
                })

    return sorted(issues, key=lambda x: -x['count'])[:30]


def check_capitalization_drop(segs, min_caps_len=4):
    issues = []
    for s in segs:
        status = s.get('translation_status', 0)
        if status not in (1, 2):
            continue
        src = s.get('source_text', '')
        tgt = s.get('translated_text', '')
        if not src or not tgt:
            continue
        for m in caps_pattern.finditer(src):
            word = m.group()
            if len(word) < min_caps_len or word in caps_stop:
                continue
            if word.lower() not in tgt.lower():
                issues.append({
                    'type': 'capitalization_drop',
                    'text_index': s.get('text_index', 0),
                    'word': word,
                    'source': src[:80], 'target': tgt[:80],
                })
                break  # one per segment max
    return issues


def cmd_warn(args):
    cache_path = args.cache
    with open(cache_path, encoding='utf-8') as f:
        cache = json.load(f)

    segs = cache.get('segments', [])

    output = {}

    if args.all or 'length' in args.checks:
        output['length_anomaly'] = check_length_anomaly(
            segs, args.ratio_min, args.ratio_max)
    if args.all or 'line' in args.checks:
        output['line_count_mismatch'] = check_line_count_mismatch(segs)
    if args.all or 'repeat' in args.checks:
        output['repeated_target'] = check_repeated_target(
            segs, args.min_repeat_len)
    if args.all or 'caps' in args.checks:
        output['capitalization_drop'] = check_capitalization_drop(
            segs, args.min_caps_len)

    total = sum(len(v) for v in output.values())
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\nTotal warnings: {total}", file=sys.stderr)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Statistical anomaly warnings")
    ap.add_argument('cache', help='Path to cache.json')
    ap.add_argument('--checks', nargs='+',
                    choices=['length', 'line', 'repeat', 'caps'],
                    default=['length', 'line', 'repeat', 'caps'],
                    help='Which checks to run')
    ap.add_argument('--all', action='store_true', help='Run all checks')
    ap.add_argument('--ratio-min', type=float, default=0.2)
    ap.add_argument('--ratio-max', type=float, default=5.0)
    ap.add_argument('--min-repeat-len', type=int, default=20)
    ap.add_argument('--min-caps-len', type=int, default=4)
    args = ap.parse_args(argv)
    cmd_warn(args)


if __name__ == '__main__':
    main()
