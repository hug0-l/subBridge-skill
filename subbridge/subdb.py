"""
Subtitle source detection and fetching.

Pipeline:
  1. detect_local()   — scan folder for existing subtitle files
  2. detect_embedded() — ffprobe MKV/MP4 for softsubs
  3. fetch_opensubtitles() — API search + download (requires API key)
  4. pipeline() — orchestrate: detect → user choose → translate → export

API key flow:
  - Environment variable: OPENSUBTITLES_API_KEY
  - CLI flag: --api-key
  - Interactive prompt when neither is provided
"""

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess as sp
import sys
import time
from pathlib import Path
from typing import Optional


# ── Language helpers ────────────────────────────────────────

# Map common file suffixes to OpenSubtitles language codes
_LANG_FROM_SUFFIX = {
    ".en": "en", ".eng": "en", ".english": "en",
    ".ja": "ja", ".jp": "ja", ".jpn": "ja", ".japanese": "ja",
    ".zh": "zh", ".chi": "zh", ".zho": "zh", ".chinese": "zh",
    ".fr": "fr", ".fre": "fr", ".french": "fr",
    ".es": "es", ".spa": "es", ".spanish": "es",
    ".de": "de", ".ger": "de", ".german": "de",
    ".ko": "ko", ".kor": "ko", ".korean": "ko",
    ".pt": "pt", ".por": "pt", ".portuguese": "pt",
    ".it": "it", ".ita": "it", ".italian": "it",
    ".ru": "ru", ".rus": "ru", ".russian": "ru",
    ".ar": "ar", ".ara": "ar", ".arabic": "ar",
    ".th": "th", ".tha": "th", ".thai": "th",
    ".vi": "vi", ".vie": "vi", ".vietnamese": "vi",
}

SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".smi", ".lrc"}

# Bitmap subtitle codecs that cannot be extracted as text
_BITMAP_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle",
                  "xsub", "pgssub"}


# ── 1. Detect Local ─────────────────────────────────────────

def detect_local(path: str) -> list[dict]:
    """Scan video's directory for matching subtitle files.
    
    Returns list of:
      {"path": str, "lang": str, "format": str, "source": "local"}
    """
    video = Path(path)
    parent = video.parent
    stem = video.stem  # e.g. "Show - S01E01 - Title WEBDL-1080p"
    results = []

    # Try exact basename match first
    for f in parent.iterdir():
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in SUBTITLE_EXTENSIONS:
            continue

        # Build the base name without all extensions
        f_stem = f.name
        # Remove subtitle extension
        no_ext = f_stem[:f_stem.rfind('.')]
        # Try to detect language from remaining suffix
        lang = "??"
        for suffix, code in sorted(_LANG_FROM_SUFFIX.items(),
                                    key=lambda x: -len(x[0])):
            if no_ext.lower().endswith(suffix):
                lang = code
                no_ext = no_ext[:no_ext.rfind(suffix)] if suffix else no_ext
                break

        # Also check for zh-hk, zh-tw, zh-cn variants
        if lang == "??" and no_ext.lower().endswith((".zh-hk", ".zh-tw", ".zh-cn", ".zho")):
            lang = "zh"

        # Check if base matches video stem
        seen = {r["path"] for r in results}
        if str(f) in seen:
            continue
        if no_ext == stem or no_ext.startswith(stem) or stem.startswith(no_ext):
            results.append({
                "path": str(f),
                "lang": lang,
                "format": ext.lstrip("."),
                "source": "local",
            })
            seen.add(str(f))

    # Also scan for files that share episode identifier (S01E01)
    seen = {r["path"] for r in results}
    ep_match = re.search(r'[Ss](\d+)[Ee](\d+)', stem)
    if ep_match:
        ep_id = f"S{ep_match.group(1)}E{ep_match.group(2)}"
        for f in parent.iterdir():
            if not f.is_file() or f.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue
            if str(f) in seen:
                continue
            if ep_id in f.name:
                f_stem = f.name
                no_ext = f_stem[:f_stem.rfind('.')]
                lang = "??"
                for suffix, code in sorted(_LANG_FROM_SUFFIX.items(),
                                            key=lambda x: -len(x[0])):
                    if no_ext.lower().endswith(suffix):
                        lang = code
                        break
                results.append({
                    "path": str(f),
                    "lang": lang,
                    "format": f.suffix.lstrip("."),
                    "source": "local",
                })
                seen.add(str(f))

    return results


# ── 2. Detect Embedded ──────────────────────────────────────

def detect_embedded(path: str) -> list[dict]:
    """Use ffprobe to detect embedded subtitle streams.
    
    Returns list of:
      {"index": int, "lang": str, "codec": str, "title": str, "source": "embedded"}
    """
    if not _has_ffprobe():
        return []

    try:
        result = sp.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "s", path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        streams = []
        for s in data.get("streams", []):
            codec = s.get("codec_name", "?")
            if codec in _BITMAP_CODECS:
                continue  # bitmap, cannot extract as text
            streams.append({
                "index": s.get("index", 0),
                "lang": s.get("tags", {}).get("language", "??"),
                "codec": codec,
                "title": s.get("tags", {}).get("title", ""),
                "source": "embedded",
            })
        return streams
    except Exception:
        return []


def _has_ffprobe() -> bool:
    try:
        sp.run(["ffprobe", "-version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


# ── 3. OpenSubtitles Movie Hash ─────────────────────────────

def _os_hash(path: str) -> str:
    """Compute OpenSubtitles moviehash."""
    LONG = 8 * 1024  # 8KB
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            if size < LONG:
                return ""
            data = f.read(LONG)
            f.seek(-LONG, 2)
            data += f.read(LONG)
        h = size
        for i in range(0, len(data), 8):
            chunk = data[i:i + 8]
            if len(chunk) < 8:
                chunk += b'\x00' * (8 - len(chunk))
            h += struct.unpack("<Q", chunk)[0]
        return f"{h & 0xFFFFFFFFFFFFFFFF:016x}"
    except Exception:
        return ""


# ── 4. OpenSubtitles API ────────────────────────────────────

OS_API_BASE = "https://www.opensubtitles.com/api/v1"


def _os_login(api_key: str) -> Optional[str]:
    """Login to OpenSubtitles, return bearer token."""
    try:
        import httpx
        resp = httpx.post(
            f"{OS_API_BASE}/login",
            json={"api_key": api_key},
            headers={"User-Agent": "subbridge v1.0"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("token")
        print(f"  OpenSubtitles login failed: {resp.status_code}", file=sys.stderr)
        return None
    except ImportError:
        print("  httpx required for OpenSubtitles API. pip install httpx",
              file=sys.stderr)
        return None
    except Exception as e:
        print(f"  OpenSubtitles login error: {e}", file=sys.stderr)
        return None


def _os_search(token: str, moviehash: str, lang: str) -> list[dict]:
    """Search OpenSubtitles by movie hash."""
    try:
        import httpx
        resp = httpx.get(
            f"{OS_API_BASE}/subtitles",
            params={"moviehash": moviehash, "languages": lang, "type": "movie"},
            headers={"Authorization": f"Bearer {token}",
                      "User-Agent": "subbridge v1.0"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("data", [])
        print(f"  OpenSubtitles search failed: {resp.status_code}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  OpenSubtitles search error: {e}", file=sys.stderr)
        return []


def _os_download(token: str, file_id: int, output: str) -> bool:
    """Download subtitle file from OpenSubtitles."""
    try:
        import httpx
        resp = httpx.post(
            f"{OS_API_BASE}/download",
            json={"file_id": file_id},
            headers={"Authorization": f"Bearer {token}",
                      "User-Agent": "subbridge v1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"  OpenSubtitles download failed: {resp.status_code}",
                  file=sys.stderr)
            return False

        link = resp.json().get("link", "")
        if not link:
            print("  OpenSubtitles: no download link", file=sys.stderr)
            return False

        dl = httpx.get(link, follow_redirects=True, timeout=30)
        if dl.status_code != 200:
            print(f"  Download failed: {dl.status_code}", file=sys.stderr)
            return False

        # Detect format from URL or content
        raw = dl.content
        text = raw.decode("utf-8", errors="replace")
        # Autodetect format
        fmt = "srt"
        if text.strip().startswith("WEBVTT"):
            fmt = "vtt"
        elif "[Script Info]" in text[:500]:
            fmt = "ass"

        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception as e:
        print(f"  OpenSubtitles download error: {e}", file=sys.stderr)
        return False


def fetch_opensubtitles(video_path: str, lang: str = "en",
                        api_key: Optional[str] = None,
                        output: Optional[str] = None) -> Optional[str]:
    """Download subtitle from OpenSubtitles.
    
    Returns path to downloaded file, or None.
    """
    api_key = api_key or os.environ.get("OPENSUBTITLES_API_KEY")
    if not api_key:
        print("  No OpenSubtitles API key.", file=sys.stderr)
        print("  Get one free at https://www.opensubtitles.com/en/request",
              file=sys.stderr)
        return None

    # Compute hash
    moviehash = _os_hash(video_path)
    if not moviehash:
        print(f"  Cannot compute hash: {video_path}", file=sys.stderr)
        return None

    print(f"  Logging in to OpenSubtitles...", file=sys.stderr)
    token = _os_login(api_key)
    if not token:
        return None

    print(f"  Searching subtitles (hash={moviehash[:12]}..., lang={lang})",
          file=sys.stderr)
    results = _os_search(token, moviehash, lang)
    if not results:
        print(f"  No subtitles found on OpenSubtitles", file=sys.stderr)
        return None

    # Pick the best result (prefer hearing_impaired=false, highest downloads)
    best = None
    for r in results:
        attrs = r.get("attributes", {})
        downloads = attrs.get("download_count", 0)
        if best is None or downloads > best.get("downloads", 0):
            best = {
                "file_id": attrs.get("files", [{}])[0].get("file_id", 0),
                "lang": attrs.get("language", ""),
                "downloads": downloads,
                "hi": attrs.get("hearing_impaired", False),
            }

    if not best or not best["file_id"]:
        print(f"  No downloadable subtitle found", file=sys.stderr)
        return None

    print(f"  Downloading: {best['lang']} (dl={best['downloads']})",
          file=sys.stderr)

    out_path = output or (Path(video_path).stem + "." + lang + ".srt")
    success = _os_download(token, best["file_id"], out_path)
    if success:
        print(f"  Saved: {out_path}", file=sys.stderr)
        return out_path
    return None


# ── 5. Pipeline ─────────────────────────────────────────────

def _ask_apikey() -> Optional[str]:
    """Prompt user for OpenSubtitles API key."""
    print(file=sys.stderr)
    print("  OpenSubtitles API key required.", file=sys.stderr)
    print("  Get one free at https://www.opensubtitles.com/en/request",
          file=sys.stderr)
    try:
        key = input("  Enter API key (or leave blank to skip): ").strip()
        if key:
            return key
    except (EOFError, KeyboardInterrupt):
        pass
    return None


def _prompt_choice(local, embedded, opensub_possible) -> Optional[dict]:
    """Show user the available options and let them choose."""
    options = []
    idx = 0

    print(file=sys.stderr)
    print("  ── Available subtitle sources ──", file=sys.stderr)

    for s in local:
        idx += 1
        label = f"  [{idx}] {os.path.basename(s['path'])} (lang: {s['lang']})"
        print(label, file=sys.stderr)
        options.append(s)

    for s in embedded:
        idx += 1
        label = f"  [{idx}] Stream #{s['index']} — {s['lang']} ({s['codec']})"
        if s['title']:
            label += f" — {s['title']}"
        print(label, file=sys.stderr)
        options.append(s)

    if opensub_possible:
        idx += 1
        print(f"  [{idx}] Search OpenSubtitles (online)", file=sys.stderr)
        options.append({"source": "opensubtitles"})

    if not options:
        print("  No subtitle sources found.", file=sys.stderr)
        return None

    try:
        choice = input(f"\n  Choose [1-{len(options)}], 0 to skip: ").strip()
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(options):
                return options[n - 1]
    except (EOFError, KeyboardInterrupt):
        pass
    return None


def _extract_embedded(video_path: str, stream_index: int,
                      output: str) -> Optional[str]:
    """Extract embedded subtitle stream via ffmpeg."""
    try:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        result = sp.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-map", f"0:{stream_index}", output],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and os.path.exists(output):
            return output
        print(f"  ffmpeg extraction failed", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Extraction error: {e}", file=sys.stderr)
        return None


def pipeline(video_path: str, target_lang: str = "zh",
             region: str = "hk", context: str = "auto",
             market: str = "asia", auto: bool = False,
             api_key: Optional[str] = None,
             glossary_path: Optional[str] = None) -> Optional[str]:
    """
    Full pipeline: detect → choose → translate → export.
    
    Returns path to exported translated SRT, or None.
    """
    video_path = os.path.abspath(video_path)
    if not os.path.exists(video_path):
        print(f"Error: file not found: {video_path}", file=sys.stderr)
        return None

    work_dir = os.path.join(os.path.dirname(video_path) or ".", ".subbridge_work")
    os.makedirs(work_dir, exist_ok=True)

    api_key = api_key or os.environ.get("OPENSUBTITLES_API_KEY")

    # Step 1: Detect local
    print("◆ Step 1: Scanning local subtitles...", file=sys.stderr)
    local = detect_local(video_path)

    # Step 2: Detect embedded
    print("◆ Step 2: Scanning embedded softsubs...", file=sys.stderr)
    embedded = detect_embedded(video_path)

    # Step 3: Choose source
    print("◆ Step 3: Selecting subtitle source...", file=sys.stderr)
    opensub_possible = True  # Always possible to ask
    chosen = None

    if auto:
        # Auto mode: local > embedded > opensubtitles
        if local:
            chosen = local[0]
            print(f"  Auto-selected local: {chosen['lang']}", file=sys.stderr)
        elif embedded:
            chosen = embedded[0]
            print(f"  Auto-selected embedded: #{chosen['index']}", file=sys.stderr)
        elif api_key:
            print(f"  Auto-downloading from OpenSubtitles...", file=sys.stderr)
            out = fetch_opensubtitles(video_path, api_key=api_key,
                                       output=os.path.join(work_dir, "download.srt"))
            if out:
                return _run_translation(out, target_lang, region, context,
                                        market, glossary_path, work_dir, video_path)
            return None
        else:
            api_key = _ask_apikey()
            if api_key:
                out = fetch_opensubtitles(video_path, api_key=api_key,
                                           output=os.path.join(work_dir, "download.srt"))
                if out:
                    return _run_translation(out, target_lang, region, context,
                                            market, glossary_path, work_dir, video_path)
            print("  No subtitle source available.", file=sys.stderr)
            return None
    else:
        # Interactive mode
        chosen = _prompt_choice(local, embedded, opensub_possible)
        if chosen is None:
            print("  Skipped.", file=sys.stderr)
            return None

    # Step 4: Get the subtitle file
    print("◆ Step 4: Acquiring subtitle file...", file=sys.stderr)
    sub_path = None

    if chosen.get("source") == "local":
        sub_path = chosen["path"]
    elif chosen.get("source") == "embedded":
        out_path = os.path.join(work_dir, f"stream_{chosen['index']}.srt")
        sub_path = _extract_embedded(video_path, chosen["index"], out_path)
    elif chosen.get("source") == "opensubtitles":
        api_key = api_key or _ask_apikey()
        if not api_key:
            print("  OpenSubtitles requires API key.", file=sys.stderr)
            return None
        sub_path = fetch_opensubtitles(video_path, api_key=api_key,
                                        output=os.path.join(work_dir, "download.srt"))

    if not sub_path or not os.path.exists(sub_path):
        print("  Failed to acquire subtitle file.", file=sys.stderr)
        return None

    # Step 5: Run translation pipeline
    return _run_translation(sub_path, target_lang, region, context,
                            market, glossary_path, work_dir, video_path)


def _run_translation(sub_path, target_lang, region, context, market,
                     glossary_path, work_dir, video_path) -> Optional[str]:
    """Run parse → auto-translate → subagent → verify → export."""
    import subprocess as sp_sub

    pypath = os.path.join(os.path.dirname(__file__))
    env = os.environ.copy()
    env["PYTHONPATH"] = pypath
    python = sys.executable

    cache_json = os.path.join(work_dir, "cache.json")
    auto_batch = os.path.join(work_dir, "auto_batch.json")
    uncertain_json = os.path.join(work_dir, "uncertain.json")
    tm_json = os.path.join(work_dir, "tm.json")

    # Parse
    print("◆ Step 5: Parsing...", file=sys.stderr)
    r = sp_sub.run([python, "-m", "parse", "--input", sub_path,
                    "--source-lang", "en", "--target-lang", target_lang,
                    "--region", region, "--context", context, "--market", market,
                    "--out", cache_json],
                   capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(f"  Parse failed: {r.stderr[:200]}", file=sys.stderr)
        return None

    # Auto-translate
    print("◆ Step 6: Auto-translating...", file=sys.stderr)
    r = sp_sub.run([python, "-m", "batch", "read", cache_json,
                    "--size", "3000", "--auto",
                    "--glossary", glossary_path or "",
                    "--tm", tm_json, "--tm-save", tm_json,
                    "--uncertain", uncertain_json,
                    "--context", context, "--output", auto_batch],
                   capture_output=True, text=True, env=env)

    # Write back auto-translations
    if os.path.exists(auto_batch):
        sp_sub.run([python, "-m", "batch", "write", cache_json, auto_batch],
                   capture_output=True, env=env)

    # Check for uncertain segments
    if os.path.exists(uncertain_json):
        with open(uncertain_json, encoding="utf-8") as f:
            uncertain = json.load(f)
        if uncertain:
            print(f"  {len(uncertain)} segments need subagent translation.",
                  file=sys.stderr)
            print(f"  Run with: subagent prompt using prompt_builder",
                  file=sys.stderr)

    # Build output filename
    video_stem = Path(video_path).stem
    out_name = f"{video_stem}.{target_lang}-{region}.srt"
    if region:
        out_name = f"{video_stem}.{target_lang}-{region}.srt"
    else:
        out_name = f"{video_stem}.{target_lang}.srt"
    out_path = os.path.join(os.path.dirname(video_path), out_name)

    # Export
    print("◆ Step 7: Exporting...", file=sys.stderr)
    r = sp_sub.run([python, "-m", "export", "--cache", cache_json,
                    "--output", out_path, "--format", "srt",
                    "--region", region, "--no-credit"],
                   capture_output=True, text=True, env=env)
    if r.returncode == 0 and os.path.exists(out_path):
        sz = os.path.getsize(out_path)
        print(f"  ✓ Exported: {os.path.basename(out_path)} ({sz/1024:.0f}KB)",
              file=sys.stderr)
        return out_path
    print(f"  Export failed: {r.stderr[:200]}", file=sys.stderr)
    return None


# ── CLI Commands ────────────────────────────────────────────

def cmd_detect(args):
    local = detect_local(args.input)
    embedded = detect_embedded(args.input)

    print("── Local files ──")
    if local:
        for s in local:
            print(f"  [{s['lang']}] {os.path.basename(s['path'])} ({s['format']})")
    else:
        print("  (none)")

    print("\n── Embedded softsubs ──")
    if embedded:
        for s in embedded:
            line = f"  #{s['index']} — {s['lang']} ({s['codec']})"
            if s['title']:
                line += f" — {s['title']}"
            print(line)
    else:
        print("  (none)")


def cmd_fetch(args):
    api_key = args.api_key or os.environ.get("OPENSUBTITLES_API_KEY")
    if not api_key:
        api_key = _ask_apikey()
    if not api_key:
        print("API key required.", file=sys.stderr)
        sys.exit(1)

    out = args.output or f"{Path(args.input).stem}.{args.lang}.srt"
    result = fetch_opensubtitles(args.input, args.lang, api_key, out)
    if result:
        print(result)
    else:
        sys.exit(1)


def cmd_translate(args):
    api_key = args.api_key or os.environ.get("OPENSUBTITLES_API_KEY")
    result = pipeline(
        args.input,
        target_lang=args.target_lang,
        region=args.region,
        context=args.context,
        market=args.market,
        auto=args.auto,
        api_key=api_key,
        glossary_path=args.glossary,
    )
    if result:
        print(result)
    else:
        sys.exit(1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Subtitle source detection & download")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("detect", help="Scan local folder and video for subtitles")
    d.add_argument("--input", "-i", required=True, help="Video file path")

    f = sub.add_parser("fetch", help="Download subtitle from OpenSubtitles")
    f.add_argument("--input", "-i", required=True, help="Video file path")
    f.add_argument("--lang", default="en", help="Language code (default: en)")
    f.add_argument("--output", "-o", help="Output path (default: auto)")
    f.add_argument("--api-key", help="OpenSubtitles API key")

    t = sub.add_parser("translate", help="Detect → translate → export in one step")
    t.add_argument("--input", "-i", required=True, help="Video file path")
    t.add_argument("--target-lang", default="zh", help="Target language code")
    t.add_argument("--region", default="hk", help="Target region")
    t.add_argument("--context", default="auto",
                   choices=["military", "medical", "casual", "auto"])
    t.add_argument("--market", default="asia",
                   choices=["nordic", "western", "asia"])
    t.add_argument("--auto", action="store_true",
                   help="Non-interactive: local→embedded→online")
    t.add_argument("--api-key", help="OpenSubtitles API key")
    t.add_argument("--glossary", help="Path to glossary.locked.json")

    args = ap.parse_args(argv)
    if args.command == "detect":
        cmd_detect(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "translate":
        cmd_translate(args)


if __name__ == "__main__":
    main()
