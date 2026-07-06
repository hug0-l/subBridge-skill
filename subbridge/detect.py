"""Language and encoding detection for subtitle files."""

import argparse
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="Detect language/encoding of subtitle file")
    ap.add_argument("--input", "-i", required=True, help="Input file")
    ap.add_argument("--text", help="Text to analyze (instead of file)")
    ap.add_argument("--encoding", action="store_true", help="Detect encoding")
    ap.add_argument("--language", action="store_true", help="Detect language")
    args = ap.parse_args(argv)

    if args.encoding and args.input:
        enc = detect_file_encoding(args.input)
        print(f"Encoding: {enc}")

    if args.language:
        if args.text:
            text = args.text
        elif args.input:
            enc = detect_file_encoding(args.input)
            with open(args.input, "r", encoding=enc) as f:
                text = f.read()
        else:
            text = ""
        lang = guess_language(text)
        print(f"Language: {lang}")

    if not args.encoding and not args.language:
        if args.input:
            enc = detect_file_encoding(args.input)
            with open(args.input, "r", encoding=enc) as f:
                text = f.read()
            lang = guess_language(text)
            print(f"Encoding: {enc}")
            print(f"Language: {lang}")


if __name__ == "__main__":
    main()
