"""Convert subtitle files between formats (no translation, pure format conversion).

Usage:
  python convert.py --input episode.srt --output episode.ass
  python convert.py --input episode.ass --output episode.vtt
"""

import argparse
import os
import sys

from helpers import detect_subtitle_format
from parse import get_protector


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert subtitle format (no translation)")
    ap.add_argument("--input", "-i", required=True, help="Input subtitle file")
    ap.add_argument("--output", "-o", required=True, help="Output subtitle file")
    ap.add_argument("--input-format", default="auto",
                    help="Input format (auto-detect)")
    ap.add_argument("--output-format", default="auto",
                    help="Output format (auto from extension)")
    ap.add_argument("--encoding", default="", help="Output encoding")
    args = ap.parse_args(argv)

    # Detect input format
    with open(args.input, "rb") as f:
        raw_bytes = f.read()

    enc = args.encoding
    if not enc:
        try:
            import chardet
            result = chardet.detect(raw_bytes)
            enc = result.get("encoding", "utf-8")
        except ImportError:
            enc = "utf-8"

    raw = raw_bytes.decode(enc, errors="replace")

    input_fmt = args.input_format
    if input_fmt == "auto":
        input_fmt = detect_subtitle_format(raw)

    # Detect output format
    output_fmt = args.output_format
    if output_fmt == "auto":
        ext = os.path.splitext(args.output)[1].lower()
        ext_map = {".srt": "srt", ".ass": "ass", ".ssa": "ass",
                   ".vtt": "vtt", ".sub": "sub", ".smi": "smi",
                   ".lrc": "lrc"}
        output_fmt = ext_map.get(ext, "srt")

    protector = get_protector(input_fmt)
    segments = protector.parse(raw, enc)

    out_protector = get_protector(output_fmt)
    result = out_protector.export(segments)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"Converted: {args.input} ({input_fmt}) → {args.output} ({output_fmt})")
    print(f"Segments: {len(segments)}")


if __name__ == "__main__":
    main()
