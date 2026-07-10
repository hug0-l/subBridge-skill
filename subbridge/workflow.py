# -*- coding: utf-8 -*-
"""
subbridge agent workflow — Mode C (Agent Bulk Translate).

Handles the full pipeline for subtitle translation:
  parse → cross-TM → dedup unique → agent translate → apply → verify → export

Usage:
  python workflow.py parse --src DIR --work DIR --series NAME
  python workflow.py collect --cache FILE --out FILE
  python workflow.py apply --cache FILE --tm FILE
  python workflow.py verify --cache FILE
  python workflow.py export --cache FILE --out FILE
"""
import json, os, re, subprocess, sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_DIR, "subbridge"))

from helpers import TranslationStatus

cn = re.compile(r'[\u4e00-\u9fff\u3000-\u303f]')
keep_as_is = re.compile(
    r'(Noctu|Aude|Fraetor|Tia|Freyre|Papilliodya|Murowa|Unity|'
    r'Louperial|Ral|Alcor|Metamorphie|Faciesse|Phaidoari|Arae|Aryrha)',
    re.I
)
romaji = re.compile(r'^[a-z\s\'.,!?;:\-"]+$')

def run_sub(m, *args):
    """Run a subbridge module."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(SKILL_DIR, "subbridge")
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        ["python", "-m", m] + list(args),
        capture_output=True, text=True, env=env
    )
    if r.returncode != 0 and r.stderr:
        print("  ERR: %s" % r.stderr[:150], file=sys.stderr)
    return r.stdout


def cmd_parse(args):
    """Parse all .en.srt files in source dir."""
    src_dir = args.src
    work_dir = args.work
    os.makedirs(work_dir, exist_ok=True)
    
    files = sorted([f for f in os.listdir(src_dir) if f.endswith('.en.srt')])
    print("Parsing %d episodes..." % len(files))
    
    for f in files:
        ep_match = re.search(r'E(\d{2})', f)
        ep = ep_match.group(1) if ep_match else "XX"
        cache_path = os.path.join(work_dir, "cache_ep%s.json" % ep)
        
        if os.path.exists(cache_path):
            print("  Ep%s: cached" % ep)
            continue
        
        src_path = os.path.join(src_dir, f)
        output = run_sub("parse",
            "--input", src_path,
            "--source-lang", "en", "--target-lang", "zh",
            "--region", "hk", "--context", "casual", "--market", "asia",
            "--out", cache_path)
        if output:
            print("  Ep%s: parsed" % ep)
        else:
            print("  Ep%s: FAILED" % ep)


def cmd_apply_tm(args):
    """Apply cross-TM from completed episodes to all caches."""
    work_dir = args.work
    tm_src = args.tm  # JSON file with translations
    
    if not os.path.exists(tm_src):
        print("TM file not found: %s" % tm_src)
        return
    
    with open(tm_src, encoding='utf-8') as f:
        tm = json.load(f)
    
    print("Applying TM (%d entries)..." % len(tm))
    
    for f in sorted(os.listdir(work_dir)):
        if not f.endswith('.json') or not f.startswith('cache_ep'):
            continue
        cp = os.path.join(work_dir, f)
        
        with open(cp, encoding='utf-8') as fh:
            cache = json.load(fh)
        
        applied = 0
        for s in cache['segments']:
            if s.get('translated_text', '') and cn.search(s['translated_text']):
                continue
            src = s.get('source_text', '').strip()
            if not src:
                continue
            clean = src.replace('\\N', ' ').replace('  ', ' ').strip()
            for key in (src, clean, src.lower(), clean.lower()):
                if key in tm:
                    s['translated_text'] = tm[key]
                    applied += 1
                    break
        
        with open(cp, 'w', encoding='utf-8') as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
        
        ep = f.replace('cache_ep', '').replace('.json', '')
        print("  Ep%s: +%d from TM" % (ep, applied))


def cmd_collect(args):
    """Collect unique untranslated texts from a cache for agent translation."""
    cache_path = args.cache
    out_path = args.out
    
    with open(cache_path, encoding='utf-8') as f:
        cache = json.load(f)
    
    unique = {}
    ep = os.path.basename(cache_path).replace('cache_ep', '').replace('.json', '')
    
    for s in cache['segments']:
        tgt = s.get('translated_text', '').strip()
        if tgt and cn.search(tgt) and tgt != s.get('source_text', ''):
            continue
        src = s.get('source_text', '').strip()
        if not src:
            continue
        if keep_as_is.search(src) or romaji.match(src):
            s['translated_text'] = src
            continue
        
        clean = src.replace('\\N', ' ').replace('  ', ' ').strip()
        if clean not in unique:
            unique[clean] = {'indices': [], 'count': 0}
        unique[clean]['indices'].append(s['text_index'])
        unique[clean]['count'] += 1
        unique[clean]['source'] = src
    
    # Build output: for agent to translate
    out_data = {
        "episode": ep,
        "note": "Fill in 'translated_text' for each item. Use HK Cantonese (咗,嘅,啦,喎,唔,係,哋). Character names: refer to existing TM.",
        "items": []
    }
    for clean, info in sorted(unique.items(), key=lambda x: -x[1]['count']):
        out_data['items'].append({
            "text_index": info['indices'][0],
            "count": info['count'],
            "source_text": info['source'],
            "translated_text": ""
        })
    
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    
    print("Ep%s: %d unique texts -> %s" % (ep, len(out_data['items']), out_path))
    print("Total segments needing translation: %d" % sum(item['count'] for item in out_data['items']))


def cmd_apply(args):
    """Apply agent translations from a completed TM file to cache."""
    cache_path = args.cache
    tm_path = args.tm
    
    with open(cache_path, encoding='utf-8') as f:
        cache = json.load(f)
    
    with open(tm_path, encoding='utf-8') as f:
        tm_data = json.load(f)
    
    # Build lookup
    lookup = {}
    if isinstance(tm_data, dict):
        # Flat TM: {source_text: translated_text}
        lookup = tm_data
    elif isinstance(tm_data, list):
        # List format from collection
        for item in tm_data:
            if item.get('translated_text', '').strip():
                lookup[item['source_text'].strip()] = item['translated_text']
    elif isinstance(tm_data, dict) and 'items' in tm_data:
        # Collection format
        for item in tm_data['items']:
            if item.get('translated_text', '').strip():
                lookup[item['source_text'].strip()] = item['translated_text']
    
    applied = 0
    for s in cache['segments']:
        if s.get('translated_text', '') and cn.search(s['translated_text']):
            continue
        src = s.get('source_text', '').strip()
        if not src:
            continue
        clean = src.replace('\\N', ' ').replace('  ', ' ').strip()
        for key in (src, clean):
            if key in lookup:
                s['translated_text'] = lookup[key]
                applied += 1
                break
    
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    
    ep = os.path.basename(cache_path).replace('cache_ep', '').replace('.json', '')
    print("Ep%s: applied %d translations" % (ep, applied))


def cmd_verify(args):
    """Verify cache completeness."""
    cache_path = args.cache
    
    with open(cache_path, encoding='utf-8') as f:
        cache = json.load(f)
    
    segs = cache['segments']
    total = len(segs)
    real = 0
    empty = 0
    fake = 0
    
    for s in segs:
        src = s.get('source_text', '').strip()
        tgt = s.get('translated_text', '').strip()
        
        if not src:
            continue
        if not tgt:
            empty += 1
        elif cn.search(tgt) and tgt != src:
            real += 1
        elif keep_as_is.search(src) or romaji.match(src):
            real += 1  # intentional keep-as-is
        else:
            fake += 1
    
    pct = real * 100 // total if total else 0
    ep = os.path.basename(cache_path).replace('cache_ep', '').replace('.json', '')
    
    print("Ep%s: %d/%d = %d%% real (empty:%d, fake:%d)" % (ep, real, total, pct, empty, fake))
    
    if fake > 0:
        print("  Fake segments (need review):")
        for s in segs:
            tgt = s.get('translated_text', '').strip()
            src = s.get('source_text', '').strip()
            if not tgt or (not cn.search(tgt) and not keep_as_is.search(src) and not romaji.match(src)):
                print("    #%d: %s -> %s" % (s['text_index'], src[:80], tgt[:80] if tgt else "(empty)"))
                break
    
    return pct >= 95 and fake <= total * 0.05


def cmd_export(args):
    """Export cache to .zh-hk.srt."""
    cache_path = args.cache
    out_path = args.out
    run_sub("export", "--cache", cache_path, "--output", out_path, "--format", "srt", "--region", "hk")
    print("Exported: %s" % out_path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="subbridge agent workflow")
    sub = ap.add_subparsers(dest="cmd")
    
    p = sub.add_parser("parse", help="Parse all .en.srt in a directory")
    p.add_argument("--src", required=True, help="Source .en.srt directory")
    p.add_argument("--work", required=True, help="Work directory for caches")
    
    p = sub.add_parser("apply-tm", help="Apply TM to all caches")
    p.add_argument("--work", required=True)
    p.add_argument("--tm", required=True, help="TM JSON file")
    
    p = sub.add_parser("collect", help="Collect unique untranslated for agent")
    p.add_argument("--cache", required=True)
    p.add_argument("--out", required=True)
    
    p = sub.add_parser("apply", help="Apply agent translations to cache")
    p.add_argument("--cache", required=True)
    p.add_argument("--tm", required=True)
    
    p = sub.add_parser("verify", help="Verify cache completeness")
    p.add_argument("--cache", required=True)
    
    p = sub.add_parser("export", help="Export cache to .zh-hk.srt")
    p.add_argument("--cache", required=True)
    p.add_argument("--out", required=True)
    
    args = ap.parse_args()
    if args.cmd == "parse":
        cmd_parse(args)
    elif args.cmd == "apply-tm":
        cmd_apply_tm(args)
    elif args.cmd == "collect":
        cmd_collect(args)
    elif args.cmd == "apply":
        cmd_apply(args)
    elif args.cmd == "verify":
        cmd_verify(args)
    elif args.cmd == "export":
        cmd_export(args)
    else:
        ap.print_help()
