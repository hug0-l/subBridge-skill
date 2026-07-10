"""Verify subtitle translation quality and alignment."""

import argparse
import json
import os
import random
import re
import sys

from helpers import TranslationStatus


def has_cjk(text):
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', text))


def is_keep_as_is(text):
    t = text.strip()
    if not t:
        return True
    if re.match(r'^♪[\s♪]*$', t):
        return True
    if re.match(r'^\([A-Za-z]+\)$', t):
        return True
    return False


def cmd_alignment(args):
    """Verify segment alignment between EN source and ZH translation."""
    import pysubs2

    en_path = args.en_source
    zh_path = args.zh_target

    if not os.path.isfile(en_path):
        print(f"EN source not found: {en_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(zh_path):
        print(f"ZH target not found: {zh_path}", file=sys.stderr)
        sys.exit(1)

    en_subs = pysubs2.load(en_path, encoding='utf-8')
    zh_subs = pysubs2.load(zh_path, encoding='utf-8')

    en_count = len(en_subs)
    zh_count = len(zh_subs)
    issues = []
    total_score = 0
    max_score = 0

    print(f"EN: {en_count} segments")
    print(f"ZH: {zh_count} segments")
    print()

    # Check 1: Segment count match
    if en_count != zh_count:
        print(f"❌ SEGMENT COUNT MISMATCH: EN={en_count} vs ZH={zh_count}")
        issues.append(f"Count mismatch: EN={en_count} vs ZH={zh_count}")
    else:
        print("✅ Segment count matches")
        total_score += 10

    max_score += 10
    min_len = min(en_count, zh_count)

    # Check 2: Sample random segments for translation presence
    sample_count = min(50, min_len)
    sample_indices = random.sample(range(min_len), sample_count)
    missing_translation = 0
    english_residue = 0
    alignment_issues = 0

    for idx in sample_indices:
        en = en_subs[idx].plaintext.strip()
        zh = zh_subs[idx].plaintext.strip()

        if not en:
            continue

        # Check if ZH exists
        if not zh:
            missing_translation += 1
            if idx < 10:
                issues.append(f"  [{idx}] MISSING: EN=\"{en[:60]}\" -> ZH=\"\"")
            continue

        # Check if ZH has CJK
        if not has_cjk(zh) and not is_keep_as_is(zh):
            english_residue += 1
            if idx < 10:
                issues.append(f"  [{idx}] ENGLISH: EN=\"{en[:60]}\" -> ZH=\"{zh[:60]}\"")
            continue

    if missing_translation > 0:
        print(f"❌ {missing_translation}/{sample_count} segments MISSING translation")
    else:
        print(f"✅ All sampled segments have translation content")
        total_score += 10

    max_score += 10

    if english_residue > 0:
        print(f"⚠️ {english_residue}/{sample_count} segments have English residue")
        total_score -= 5
    else:
        print(f"✅ No English residue in sampled segments")
        total_score += 10

    max_score += 10

    # Check 3: Full scan for English residue
    full_english = 0
    full_empty = 0
    full_total = 0

    for idx in range(min_len):
        en = en_subs[idx].plaintext.strip()
        zh = zh_subs[idx].plaintext.strip()

        if not en:
            continue

        full_total += 1

        if not zh:
            full_empty += 1
            continue

        if not has_cjk(zh) and not is_keep_as_is(zh):
            full_english += 1

    zh_coverage = ((full_total - full_english - full_empty) / full_total * 100) if full_total > 0 else 0

    print(f"\nFull scan:")
    print(f"  Total non-empty EN segments: {full_total}")
    print(f"  Translated (CJK): {full_total - full_english - full_empty} ({zh_coverage:.1f}%)")
    print(f"  English residue: {full_english}")
    print(f"  Empty: {full_empty}")

    if zh_coverage >= 95:
        print("✅ Translation coverage ≥ 95%")
        total_score += 20
    elif zh_coverage >= 85:
        print(f"⚠️ Translation coverage ≥ 85% ({zh_coverage:.1f}%)")
        total_score += 10
    else:
        print(f"❌ Translation coverage too low ({zh_coverage:.1f}%)")
        total_score -= 10

    max_score += 20

    # Check 4: Speaker label consistency
    en_speakers = set()
    zh_speakers = set()
    for idx in range(min_len):
        en = en_subs[idx].plaintext
        zh = zh_subs[idx].plaintext

        m_en = re.findall(r'\b([A-Z][A-Z]+):', en)
        en_speakers.update(m_en)

        m_zh = re.findall(r'\(([A-Za-z]+)\)', zh)
        zh_speakers.update(m_zh)

    if en_speakers:
        missing_in_zh = en_speakers - zh_speakers
        if missing_in_zh:
            print(f"⚠️ Speakers in EN but not found in ZH: {missing_in_zh}")
        else:
            print(f"✅ All EN speakers found in ZH")

    # Check 5: Segment-level alignment (spot-check first/last segments)
    print()
    print("Alignment spot checks:")
    for idx in [0, 1, 2, min_len - 3, min_len - 2, min_len - 1]:
        if idx < min_len:
            en = en_subs[idx].plaintext.strip()[:60]
            zh = zh_subs[idx].plaintext.strip()[:60]
            if en or zh:
                emoji = "✅" if (has_cjk(zh) or is_keep_as_is(zh) or not en) else "❌"
                print(f"  {emoji} [{idx}] EN: {en}")
                print(f"        ZH: {zh}")

    # Output score
    print(f"\n{'='*40}")
    print(f"QUALITY SCORE: {total_score}/{max_score}")
    if total_score < max_score * 0.7:
        print("❌ FAIL - Quality too low for export")
    elif total_score < max_score * 0.9:
        print("⚠️ PASS - But needs improvement")
    else:
        print("✅ PASS - Good quality")

    if issues:
        print(f"\nIssues found ({len(issues)}):")
        for i in issues[:15]:
            print(f"  {i}")
        if len(issues) > 15:
            print(f"  ... +{len(issues)-15} more")

    return total_score >= max_score * 0.7


def cmd_completeness(args):
    """Check if all segments in cache have translations. Reports by status."""
    cache_path = args.cache
    if not os.path.isfile(cache_path):
        print(f"Cache not found: {cache_path}", file=sys.stderr)
        sys.exit(1)

    with open(cache_path, encoding='utf-8') as f:
        cache = json.load(f)

    segs = cache.get("segments", [])
    total = len(segs)
    untranslated = sum(1 for s in segs if s.get("translation_status") == TranslationStatus.UNTRANSLATED)
    translated = sum(1 for s in segs if s.get("translation_status") == TranslationStatus.TRANSLATED)
    polished = sum(1 for s in segs if s.get("translation_status") == TranslationStatus.POLISHED)
    excluded = sum(1 for s in segs if s.get("translation_status") == TranslationStatus.EXCLUDED)
    done = translated + polished

    pct = (done / max(total - excluded, 1)) * 100
    print(json.dumps({
        "total": total,
        "untranslated": untranslated,
        "translated": translated,
        "polished": polished,
        "excluded": excluded,
        "completeness_pct": round(pct, 1),
    }, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify subtitle translation quality")
    sub = ap.add_subparsers(dest="command", required=True)

    a = sub.add_parser("alignment", help="Check alignment between EN source and ZH target")
    a.add_argument("--en-source", required=True, help="Original English SRT")
    a.add_argument("--zh-target", required=True, help="Translated Chinese SRT")

    c = sub.add_parser("completeness", help="Check cache translation completeness")
    c.add_argument("--cache", required=True, help="Cache JSON file path")

    args = ap.parse_args(argv)
    if args.command == "alignment":
        sys.exit(0 if cmd_alignment(args) else 1)
    elif args.command == "completeness":
        cmd_completeness(args)


if __name__ == "__main__":
    main()
