"""Language and encoding detection for subtitle files.
Deep/bilingual detection for ASS files with dual JP+CN styles."""

import argparse
import os
import re
import sys


def detect_file_encoding(path: str) -> str:
    try:
        import chardet
        with open(path, "rb") as f:
            raw = f.read(8192)
        result = chardet.detect(raw)
        return result.get("encoding", "utf-8")
    except ImportError:
        pass
    for enc in ["utf-8", "utf-16le", "shift_jis", "big5", "euc-kr", "latin-1"]:
        try:
            with open(path, "r", encoding=enc) as f:
                f.read(256)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "utf-8"


_LANG_KEYWORDS = {
    "ja": {"は", "が", "を", "に", "の", "です", "ます", "た", "~", "さん", "ちゃん", "くん"},
    "zh": {"的", "了", "是", "在", "我", "他", "她", "这", "那", "不", "也", "就", "都", "而"},
    "ko": {"은", "는", "이", "가", "을", "를", "의", "에", "다", "요", "습니다"},
    "en": {"the", "is", "are", "was", "were", "been", "have", "has", "do", "does"},
    "fr": {"le", "la", "les", "de", "du", "des", "que", "qui", "est", "dans"},
    "de": {"der", "die", "das", "den", "dem", "des", "ein", "eine", "und", "ist"},
    "es": {"el", "la", "los", "las", "de", "del", "que", "es", "por", "para"},
    "pt": {"o", "a", "os", "as", "de", "do", "da", "que", "é", "para", "com"},
    "it": {"il", "la", "le", "gli", "dei", "che", "e", "per", "con", "sono"},
    "ru": {"и", "в", "на", "с", "что", "он", "она", "это", "не", "как"},
    "ar": {"في", "من", "على", "كان", "هذا", "هذه", "و", "لا", "ما", "هل"},
    "th": {"ที่", "ใน", "มี", "เป็น", "การ", "และ", "ไม่", "ได้", "กับ", "ว่า"},
    "vi": {"là", "và", "có", "của", "không", "với", "một", "trong", "được", "người"},
}

_SCRIPT_PATTERNS = {
    "ja": lambda t: bool(any("\u3040" <= c <= "\u309F" or "\u30A0" <= c <= "\u30FF" for c in t)),
    "zh": lambda t: bool(any("\u4E00" <= c <= "\u9FFF" for c in t)),
    "ko": lambda t: bool(any("\uAC00" <= c <= "\uD7AF" or "\u1100" <= c <= "\u11FF" for c in t)),
    "th": lambda t: bool(any("\u0E00" <= c <= "\u0E7F" for c in t)),
    "ar": lambda t: bool(any("\u0600" <= c <= "\u06FF" for c in t)),
    "ru": lambda t: bool(any("\u0400" <= c <= "\u04FF" for c in t)),
}

_CN_STYLE_RE = re.compile(r'Style:\s*\w*[Cc][Nn]\d*,')
_CN_FONT_RE = re.compile(r'GBK|GB18030|GB2312')
_CN_CHAR_RE = re.compile(r'[\u4e00-\u9fff\u3000-\u303f]')
_DIALOGUE_RE = re.compile(r'^Dialogue:')


def guess_language(text: str) -> str:
    for lang, checker in _SCRIPT_PATTERNS.items():
        if checker(text):
            return lang

    words = text.split()
    if not words:
        return "en"

    scores = {}
    for lang, keywords in _LANG_KEYWORDS.items():
        score = sum(1 for w in words if w.lower().strip(".,!?;:\"'") in keywords)
        if score > 0:
            scores[lang] = score

    if scores:
        return max(scores, key=scores.get)
    return "en"


def detect_languages_deep(path: str) -> list:
    """Deep-detect all languages present in a subtitle file.
    For ASS files, checks for bilingual style tracks (e.g., InsCN, OPCN).
    Returns a list of language codes.
    """
    ext = os.path.splitext(path)[1].lower()
    enc = detect_file_encoding(path)
    langs = []

    try:
        with open(path, "r", encoding=enc, errors="replace") as f:
            content = f.read()
    except Exception:
        return ["unknown"]

    dom_lang = guess_language(content)
    langs.append(dom_lang)

    if ext in (".ass", ".ssa"):
        cn_styles_found = False
        cn_dialogue_found = False

        for line in content.splitlines():
            if _CN_STYLE_RE.search(line):
                cn_styles_found = True
            if _CN_FONT_RE.search(line):
                cn_styles_found = True
            if _DIALOGUE_RE.match(line) and _CN_CHAR_RE.search(line):
                cn_dialogue_found = True

        if cn_styles_found or cn_dialogue_found:
            if dom_lang != "zh":
                langs.append("zh")

    return list(dict.fromkeys(langs))  # dedup, preserve order


def scan_directory(path: str, deep: bool = False) -> list:
    """Recursively scan directory for subtitle files and detect languages.
    Returns list of dicts with file info.
    """
    EXT_SUBS = {".ass", ".srt", ".ssa", ".sub", ".idx", ".vtt"}
    results = []

    for dirpath, dirnames, filenames in os.walk(path):
        for f in sorted(filenames):
            ext = os.path.splitext(f)[1].lower()
            if ext not in EXT_SUBS:
                continue
            full = os.path.join(dirpath, f)

            if deep:
                langs = detect_languages_deep(full)
            else:
                enc = detect_file_encoding(full)
                try:
                    with open(full, "r", encoding=enc, errors="replace") as fh:
                        text = fh.read()
                except Exception:
                    langs = ["unknown"]
                else:
                    langs = [guess_language(text)]

            results.append({
                "file": full,
                "ext": ext,
                "encoding": detect_file_encoding(full),
                "languages": langs,
                "size": os.path.getsize(full),
            })

    return results


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Detect language/encoding of subtitle file(s)"
    )
    ap.add_argument("--input", "-i", help="Input file or directory")
    ap.add_argument("--text", help="Text to analyze (instead of file)")
    ap.add_argument("--encoding", action="store_true", help="Detect encoding")
    ap.add_argument("--language", action="store_true", help="Detect language")
    ap.add_argument("--deep", action="store_true",
                    help="Deep bilingual detection: check ASS CN-style tracks")
    ap.add_argument("--scan", action="store_true",
                    help="Recursively scan directory for subtitle files")
    args = ap.parse_args(argv)

    if args.scan and args.input:
        if not os.path.isdir(args.input):
            print(f"Error: --scan requires a directory, got: {args.input}", file=sys.stderr)
            sys.exit(1)
        results = scan_directory(args.input, deep=args.deep)
        cn_total = 0
        no_cn = []
        for r in results:
            has_cn = "zh" in r["languages"]
            if has_cn:
                cn_total += 1
            langs_str = "+".join(r["languages"])
            print(f"  [{langs_str:>8s}] {r['file']}")
            if not has_cn:
                rel = os.path.relpath(r["file"], args.input)
                no_cn.append(rel)
        print(f"\n  Total subtitle files: {len(results)}")
        print(f"  With Chinese:         {cn_total}")
        print(f"  Without Chinese:      {len(no_cn)}")
        if no_cn:
            print(f"\n  Files without Chinese ({len(no_cn)}):")
            for f in no_cn[:20]:
                print(f"    {f}")
            if len(no_cn) > 20:
                print(f"    ... and {len(no_cn)-20} more")
        return

    if args.deep and args.input:
        if os.path.isdir(args.input):
            results = scan_directory(args.input, deep=True)
            for r in results:
                langs_str = "+".join(r["languages"])
                print(f"{langs_str:>8s}  {r['file']}")
            return
        else:
            langs = detect_languages_deep(args.input)
            print(f"Languages: {'+'.join(langs)}")
            return

    if args.encoding and args.input:
        enc = detect_file_encoding(args.input)
        print(f"Encoding: {enc}")

    if args.language:
        if args.text:
            text = args.text
        elif args.input:
            if os.path.isdir(args.input):
                print("Error: --language with a directory requires --deep or --scan", file=sys.stderr)
                sys.exit(1)
            enc = detect_file_encoding(args.input)
            with open(args.input, "r", encoding=enc) as f:
                text = f.read()
        else:
            text = ""
        lang = guess_language(text)
        print(f"Language: {lang}")

    if not args.encoding and not args.language and not args.deep and not args.scan:
        if args.input and not os.path.isdir(args.input):
            enc = detect_file_encoding(args.input)
            with open(args.input, "r", encoding=enc) as f:
                text = f.read()
            lang = guess_language(text)
            print(f"Encoding: {enc}")
            print(f"Language: {lang}")


if __name__ == "__main__":
    main()
