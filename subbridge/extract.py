"""Extract soft subtitles from video files (MKV/MP4/MOV/AVI).

Requires one of:
  - ffmpeg + ffprobe (universal)
  - mkvextract + mkvmerge (MKV-specific, better for complex MKV)

Subtitle formats commonly found in containers:
  MKV: SRT, ASS/SSA, VTT, PGS (bluray), HD MV PGS, VobSub
  MP4: TX3G, VTT, SRT (mov_text)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from helpers import detect_subtitle_format


def check_tool(name: str) -> bool:
    try:
        subprocess.run([name, "--help"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        try:
            subprocess.run([name, "-version"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


def probe_streams(video_path: str) -> list[dict]:
    """Use ffprobe to list all subtitle streams."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "s",
        video_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=30,
            # Use raw bytes to avoid locale encoding issues
            encoding=None,
        )
        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            print(f"ffprobe error: {stderr_text}", file=sys.stderr)
            return []
        stdout_text = result.stdout.decode("utf-8", errors="replace")
        data = json.loads(stdout_text)
        return data.get("streams", [])
    except FileNotFoundError:
        print("ffprobe not found. Install ffmpeg or MKVToolNix.", file=sys.stderr)
        return []
    except Exception as e:
        print(f"ffprobe failed: {e}", file=sys.stderr)
        return []


EXTRACT_DIR = Path(tempfile.gettempdir()) / "subtitle_extracted"


def extract_with_ffmpeg(video_path: str, stream_index: int,
                        out_format: str = None) -> str | None:
    """Extract a subtitle stream using ffmpeg."""
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    base = os.path.splitext(os.path.basename(video_path))[0]
    codec_map = {
        "subrip": "srt",
        "ass": "ass",
        "ssa": "ass",
        "webvtt": "vtt",
        "mov_text": "srt",
        "hdmv_pgs_subtitle": "sup",
        "dvd_subtitle": "vobsub",
        "dvb_subtitle": "dvbsub",
    }

    # Probe the stream to detect its codec
    streams = probe_streams(video_path)
    stream_info = None
    for s in streams:
        if s.get("index") == stream_index:
            stream_info = s
            break

    codec = stream_info.get("codec_name", "") if stream_info else ""
    ext = out_format or codec_map.get(codec, "srt")

    # Only extract textual formats (skip bitmap PGS/VobSub)
    if codec in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "dvb_teletext"):
        print(f"Stream {stream_index}: bitmap format ({codec}), "
              f"OCR would be needed. Skipping.", file=sys.stderr)
        return None

    out_path = str(EXTRACT_DIR / f"{base}_track{stream_index}.{ext}")

    cmd = [
        "ffmpeg", "-y", "-v", "quiet",
        "-i", video_path,
        "-map", f"0:{stream_index}",
    ]

    if out_format:
        cmd += ["-c:s", out_format]
    elif codec == "subrip":
        cmd += ["-c:s", "srt"]
    elif codec in ("ass", "ssa"):
        pass  # Keep native format
    elif codec in ("mov_text", "tx3g"):
        cmd += ["-c:s", "srt"]
    elif codec == "webvtt":
        pass
    else:
        cmd += ["-c:s", "srt"]

    cmd.append(out_path)

    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=120, encoding=None,
        )
        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            print(f"ffmpeg error: {stderr_text}", file=sys.stderr)
            return None
        if os.path.getsize(out_path) > 0:
            return out_path
        return None
    except Exception as e:
        print(f"ffmpeg failed: {e}", file=sys.stderr)
        return None


def extract_with_mkvextract(video_path: str, track_id: int) -> str | None:
    """Extract a subtitle track using mkvextract (MKV only)."""
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    base = os.path.splitext(os.path.basename(video_path))[0]
    out_path = str(EXTRACT_DIR / f"{base}_track{track_id}.sub")

    cmd = ["mkvextract", video_path, "tracks", f"{track_id}:{out_path}"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=120, encoding=None,
        )
        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            print(f"mkvextract error: {stderr_text}", file=sys.stderr)
            return None
        if os.path.getsize(out_path) > 0:
            # Detect and rename with proper extension
            with open(out_path, "r", encoding="utf-8-sig") as f:
                head = f.read(512)
            fmt = detect_subtitle_format(head)
            proper = out_path.rsplit(".", 1)[0] + "." + fmt
            os.rename(out_path, proper)
            return proper
        return None
    except FileNotFoundError:
        print("mkvextract not found.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"mkvextract failed: {e}", file=sys.stderr)
        return None


def cmd_list(args):
    """List subtitle streams in video file."""
    streams = probe_streams(args.input)
    if not streams:
        print("No subtitle streams found.", file=sys.stderr)
        sys.exit(1)

    print(f"Video: {args.input}")
    print(f"\nFound {len(streams)} subtitle stream(s):\n")
    print(f"  {'Index':>5}  {'Codec':<16}  {'Language':<10}  {'Title'}")
    print(f"  {'-----':>5}  {'-----':<16}  {'--------':<10}  {'-----'}")
    for s in streams:
        idx = s.get("index", "?")
        codec = s.get("codec_name", "?")
        lang = s.get("tags", {}).get("language", "und")
        title = s.get("tags", {}).get("title", "")
        print(f"  {idx:>5}  {codec:<16}  {lang:<10}  {title}")


def cmd_extract(args):
    """Extract subtitle streams from video."""
    streams = probe_streams(args.input)
    if not streams:
        print("No subtitle streams found.", file=sys.stderr)
        sys.exit(1)

    # Filter by track selection
    if args.tracks:
        selected_indices = set(args.tracks)
    else:
        selected_indices = {s.get("index") for s in streams}

    has_mkvextract = check_tool("mkvextract")
    has_ffmpeg = check_tool("ffmpeg")

    if not has_ffmpeg and not has_mkvextract:
        print("Error: need ffmpeg or mkvextract installed.", file=sys.stderr)
        sys.exit(1)

    results = []
    for s in streams:
        idx = s.get("index")
        if idx not in selected_indices:
            continue

        codec = s.get("codec_name", "")
        lang = s.get("tags", {}).get("language", "und")
        title = s.get("tags", {}).get("title", "")

        # Prefer mkvextract for MKV (better ASS preservation)
        out_path = None
        if has_mkvextract and args.input.lower().endswith(".mkv"):
            out_path = extract_with_mkvextract(args.input, idx)
            if out_path:
                print(f"Extracted (mkvextract) track {idx}: {out_path}", file=sys.stderr)

        # Fallback to ffmpeg
        if not out_path and has_ffmpeg:
            out_path = extract_with_ffmpeg(args.input, idx, args.format)
            if out_path:
                print(f"Extracted (ffmpeg) track {idx}: {out_path}", file=sys.stderr)

        if out_path:
            results.append({
                "track_index": idx,
                "codec": codec,
                "language": lang,
                "title": title,
                "output_path": os.path.abspath(out_path),
            })
        else:
            print(f"Failed to extract track {idx} ({codec})", file=sys.stderr)

    if results:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("No tracks extracted.", file=sys.stderr)
        sys.exit(1)


def cmd_auto(args):
    """Extract all subtitle streams then import into cache.json."""
    streams = probe_streams(args.input)
    if not streams:
        print("No subtitle streams found.", file=sys.stderr)
        sys.exit(1)

    has_ffmpeg = check_tool("ffmpeg")

    extracted = []
    for s in streams:
        idx = s.get("index")
        codec = s.get("codec_name", "")
        lang = s.get("tags", {}).get("language", "und")
        title = s.get("tags", {}).get("title", "")

        if codec in ("hdmv_pgs_subtitle", "dvd_subtitle"):
            print(f"Skipping bitmap track {idx} ({codec}). Use OCR.", file=sys.stderr)
            continue

        if has_ffmpeg:
            out_path = extract_with_ffmpeg(args.input, idx)
            if out_path:
                extracted.append(out_path)

    if not extracted:
        print("No extractable subtitle streams.", file=sys.stderr)
        sys.exit(1)

    print(f"Extracted {len(extracted)} subtitle file(s).")
    print(f"Output directory: {EXTRACT_DIR}")
    for p in extracted:
        print(f"  {p}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extract soft subtitles from video files")
    sub = ap.add_subparsers(dest="command", required=True)

    l = sub.add_parser("list", help="List subtitle streams in video")
    l.add_argument("--input", "-i", required=True, help="Video file path")

    e = sub.add_parser("extract", help="Extract subtitle streams")
    e.add_argument("--input", "-i", required=True, help="Video file path")
    e.add_argument("--tracks", type=int, nargs="+", default=None,
                   help="Track indices to extract (default: all)")
    e.add_argument("--format", "-f", choices=["srt", "ass", "vtt"],
                   help="Output format (auto-detect by default)")

    a = sub.add_parser("auto", help="Extract all and output paths for import")
    a.add_argument("--input", "-i", required=True, help="Video file path")

    args = ap.parse_args(argv)
    if args.command == "list":
        cmd_list(args)
    elif args.command == "extract":
        cmd_extract(args)
    elif args.command == "auto":
        cmd_auto(args)


if __name__ == "__main__":
    main()
