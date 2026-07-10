# -*- coding: utf-8 -*-
"""
Glossary scan — discover terms/strays/merges issues in translated caches.

Port from ainiee-translate scan.py. Four modes:
  discover  — find names NOT in glossary that were translated/deleted
  terms     — find glossary terms that leaked as English in target
  strays    — find hallucinated Latin tokens in target (not in source)
  merges    — find OCR/parse whitespace-loss concatenations (camelCase)
"""
import json, os, re, sys
from collections import Counter

cn = re.compile(r'[\u4e00-\u9fff\u3000-\u303f]')
latin_word = re.compile(r'[a-zA-Z]{3,}')
upper_word = re.compile(r'\b[A-Z][a-zA-Z]*[a-z]\w*')
caps_word = re.compile(r'\b[A-Z]{2,}\b')
camel = re.compile(r'[a-z][A-Z]')
merge_long = re.compile(r'[a-zA-Z]{13,}')

STOP_WORDS = {
    'the','and','for','are','but','not','you','all','can','had','her','was',
    'one','our','out','has','have','been','some','them','than','that','this',
    'with','from','they','were','what','when','where','which','will','your',
    'into','about','over','after','before','between','through','during',
    'without','because','just','also','very','well','even','still','already',
}


def discover(cache, glossary):
    """Find names NOT in glossary that were inconsistently preserved or always deleted."""
    segs = cache.get('segments', [])
    chars = glossary.get('characters', [])
    terms_list = glossary.get('terms', [])

    known = set()
    for c in chars:
        known.add(c.get('canonical','').lower())
        known.add(c.get('render','').lower())
        for a in c.get('aliases', []):
            known.add(a.lower())
    for t in terms_list:
        known.add(t.get('src','').lower())

    # Build corpus proper-noun vocabulary
    corpus_nouns = Counter()
    for s in segs:
        src = s.get('source_text', '')
        for m in upper_word.finditer(src):
            w = m.group()
            if w.lower() not in STOP_WORDS and len(w) >= 3:
                corpus_nouns[w.lower()] += 1

    inconsistent = {}
    never_preserved = {}

    for s in segs:
        status = s.get('translation_status', 0)
        if status not in (1, 2):
            continue
        src = s.get('source_text', '')
        tgt = s.get('translated_text', '')
        if not src or not tgt:
            continue
        src_lower = src.lower()
        tgt_lower = tgt.lower()
        idx = s.get('text_index', 0)

        for noun, total_count in corpus_nouns.items():
            if noun in known or len(noun) < 3:
                continue
            if noun not in src_lower:
                continue
            in_tgt = noun in tgt_lower

            if noun not in inconsistent and noun not in never_preserved:
                if in_tgt:
                    inconsistent[noun] = {'kept': 0, 'lost': 0, 'examples': []}
                else:
                    never_preserved[noun] = {'lost': 0, 'examples': []}

            if noun in inconsistent:
                if in_tgt:
                    inconsistent[noun]['kept'] += 1
                else:
                    inconsistent[noun]['lost'] += 1
                    if len(inconsistent[noun]['examples']) < 3:
                        inconsistent[noun]['examples'].append(
                            (idx, src[:80], tgt[:80]))

            if noun in never_preserved:
                never_preserved[noun]['lost'] += 1
                if len(never_preserved[noun]['examples']) < 3:
                    never_preserved[noun]['examples'].append(
                        (idx, src[:80], tgt[:80]))

    return {
        'inconsistent': sorted(
            [{'name': n, **v} for n, v in inconsistent.items()],
            key=lambda x: -(x['kept'] + x['lost'])),
        'never_preserved': sorted(
            [{'name': n, **v} for n, v in never_preserved.items()],
            key=lambda x: -x['lost']),
    }


def terms(cache, glossary):
    """Find glossary terms with dst that leaked as English in target."""
    segs = cache.get('segments', [])
    terms_list = glossary.get('terms', [])
    results = []

    for t in terms_list:
        src_term = t.get('src', '')
        dst_term = t.get('dst', '')
        if not src_term or not dst_term or t.get('keep_source', False):
            continue
        occurrences = []
        for s in segs:
            status = s.get('translation_status', 0)
            if status not in (1, 2):
                continue
            tgt = s.get('translated_text', '')
            if not tgt:
                continue
            if src_term.lower() in tgt.lower() and dst_term.lower() not in tgt.lower():
                occurrences.append(s.get('text_index', 0))
        if occurrences:
            results.append({
                'src': src_term, 'dst': dst_term,
                'count': len(occurrences),
                'segments': occurrences[:20],
            })

    return results


def strays(cache, glossary):
    """Find hallucinated Latin tokens in target that don't appear in source."""
    segs = cache.get('segments', [])
    chars = glossary.get('characters', [])
    terms_list = glossary.get('terms', [])

    known = set()
    for c in chars:
        known.add(c.get('canonical','').lower())
        known.add(c.get('render','').lower())
        for a in c.get('aliases', []):
            known.add(a.lower())
    for t in terms_list:
        known.add(t.get('src','').lower())

    results = []
    for s in segs:
        status = s.get('translation_status', 0)
        if status not in (1, 2):
            continue
        src = s.get('source_text', '')
        tgt = s.get('translated_text', '')
        if not src or not tgt:
            continue
        src_lower = src.lower()
        idx = s.get('text_index', 0)

        for m in latin_word.finditer(tgt):
            token = m.group().lower()
            if token in known or token in STOP_WORDS:
                continue
            if len(token) < 2:
                continue
            if token not in src_lower:
                results.append({
                    'token': token, 'text_index': idx,
                    'source': src[:60], 'target': tgt[:60],
                })

    return results[:100]


def merges(cache):
    """Find OCR/parse whitespace-loss concatenations."""
    segs = cache.get('segments', [])
    results = []
    for s in segs:
        status = s.get('translation_status', 0)
        if status not in (1, 2):
            continue
        src = s.get('source_text', '')
        if not src:
            continue

        for m in merge_long.finditer(src):
            token = m.group()
            results.append({
                'token': token,
                'text_index': s.get('text_index', 0),
                'source': src[:80],
            })

        # CamelCase
        for m in camel.finditer(src):
            # Get the full token containing the camelCase transition
            start = max(0, m.start() - 10)
            end = min(len(src), m.end() + 10)
            token = src[start:end].strip()
            if len(token) >= 10:
                results.append({
                    'token': f"...{token}...",
                    'text_index': s.get('text_index', 0),
                    'source': src[:80],
                })

    return results[:100]


def cmd_scan(args):
    cache_path = args.cache
    locked_path = args.locked
    mode = args.mode

    with open(cache_path, encoding='utf-8') as f:
        cache = json.load(f)

    glossary = {}
    if locked_path and os.path.exists(locked_path):
        with open(locked_path, encoding='utf-8') as f:
            glossary = json.load(f)

    output = {'mode': mode, 'issues': []}

    if mode in ('all', 'discover'):
        d = discover(cache, glossary)
        output['discover_inconsistent'] = d['inconsistent'][:30]
        output['discover_never_preserved'] = d['never_preserved'][:30]

    if mode in ('all', 'terms'):
        output['terms'] = terms(cache, glossary)[:30]

    if mode in ('all', 'strays'):
        output['strays'] = strays(cache, glossary)

    if mode in ('all', 'merges'):
        output['merges'] = merges(cache)

    print(json.dumps(output, ensure_ascii=False, indent=2))
    total = sum(len(v) for v in output.values() if isinstance(v, list))
    print(f"\nTotal issues: {total}", file=sys.stderr)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Scan cache for glossary issues")
    ap.add_argument('cache', help='Path to cache.json')
    ap.add_argument('--locked', help='Path to glossary.locked.json')
    ap.add_argument('--mode', choices=['discover', 'terms', 'strays', 'merges', 'all'],
                    default='all', help='Scan mode')
    args = ap.parse_args(argv)
    cmd_scan(args)


if __name__ == '__main__':
    main()
