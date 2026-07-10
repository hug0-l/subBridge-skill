# -*- coding: utf-8 -*-
"""
ASR Pipeline — video audio → transcription → translation → .zh-hk.srt.

Integrates extract.py (ASR) + workflow.py (translate) into one command.

Usage:
  python -m asr_pipeline --input episode.mkv --language ja
  python -m asr_pipeline --input episode.mkv --language ja --backend whisperx
  python -m asr_pipeline --input episode.mkv --language ja --output-dir work/
"""
import json, os, subprocess, sys, tempfile

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_DIR, "subbridge"))


def run(m, *args, capture=True):
    """Run a subbridge module."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(SKILL_DIR, "subbridge")
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        ["python", "-m", m] + list(args),
        capture_output=True, text=True, env=env
    )
    if r.returncode != 0 and r.stderr:
        print("  ERR: %s" % r.stderr[:200], file=sys.stderr)
    return r.stdout


def main():
    import argparse
    ap = argparse.ArgumentParser(description="ASR + Translation Pipeline")
    ap.add_argument("--input", "-i", required=True, help="Video file (.mkv/.mp4)")
    ap.add_argument("--output", "-o", default=None,
                    help="Output .zh-hk.srt path (default: video path + .zh-hk.srt)")
    ap.add_argument("--work", default=None,
                    help="Work directory for caches (default: temp dir)")
    ap.add_argument("--backend", choices=["whisperx", "faster-whisper"],
                    default="faster-whisper",
                    help="ASR backend")
    ap.add_argument("--model", default=None,
                    help="Whisper model size or HF path")
    ap.add_argument("--device", default=None,
                    help="cpu / cuda (default: auto-detect)")
    ap.add_argument("--language", default=None,
                    help="Source language code (ja/en/zh/ko/...). "
                         "Required for non-English. Auto-detect if omitted.")
    ap.add_argument("--region", default="hk",
                    help="Target region: hk/tw/cn (default: hk)")
    ap.add_argument("--context", default="casual",
                    help="Translation context: casual/military/medical/auto")
    ap.add_argument("--tm", default=None,
                    help="TM JSON file for cross-episode reuse")
    ap.add_argument("--keep-temp", action="store_true",
                    help="Keep temp work directory")
    args = ap.parse_args()

    video = args.input
    if not os.path.isfile(video):
        print("Error: file not found: %s" % video, file=sys.stderr)
        sys.exit(1)

    # Paths
    base = os.path.splitext(os.path.basename(video))[0]
    output_srt = args.output or video.replace(".mkv", ".srt").replace(".mp4", ".srt")
    output_zh = output_srt.replace(".srt", ".zh-hk.srt")
    work = args.work
    if not work:
        work = tempfile.mkdtemp(prefix="asr_")
    else:
        os.makedirs(work, exist_ok=True)

    # If output dir is different from video dir, ensure it exists
    os.makedirs(os.path.dirname(output_srt) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(output_zh) or ".", exist_ok=True)

    step = 1

    # ── Step 1: ASR ──
    print("\n[%d/5] ASR: %s" % (step, video))
    print("    backend=%s model=%s device=%s language=%s" % (
        args.backend, args.model or "auto", args.device or "auto", args.language or "auto"))

    asr_args = [
        "--input", video,
        "--output", output_srt,
        "--backend", args.backend,
    ]
    if args.model:
        asr_args += ["--model", args.model]
    if args.device:
        asr_args += ["--device", args.device]
    if args.language:
        asr_args += ["--language", args.language]

    result = run("extract", "asr", *asr_args)
    if result:
        try:
            info = json.loads(result.strip().split("\n")[-1])
            src_lang = info.get("language", args.language or "en")
            seg_count = info.get("segments", 0)
            print("    detected: %s, %d segments" % (src_lang, seg_count))
        except (json.JSONDecodeError, IndexError):
            src_lang = args.language or "en"
            print("    language: %s" % src_lang)
    else:
        src_lang = args.language or "en"

    if not os.path.exists(output_srt):
        print("    ASR FAILED: no output file", file=sys.stderr)
        sys.exit(1)

    step += 1

    # ── Step 2: Parse ──
    cache = os.path.join(work, "cache.json")
    print("\n[%d/5] Parse -> %s" % (step, cache))

    run("parse", "--input", output_srt,
        "--source-lang", src_lang,
        "--target-lang", "zh",
        "--region", args.region,
        "--context", args.context,
        "--market", "asia",
        "--out", cache)

    if not os.path.exists(cache):
        print("    PARSE FAILED", file=sys.stderr)
        sys.exit(1)

    step += 1

    # ── Step 3: Apply TM (if provided) ──
    if args.tm and os.path.exists(args.tm):
        print("\n[%d/5] Apply TM: %s" % (step, args.tm))
        run("workflow", "apply-tm", "--work", work, "--tm", args.tm)
    else:
        print("\n[%d/5] Skip TM (none provided)" % step)

    step += 1

    # ── Step 4: Collect unique for agent ──
    unique_file = os.path.join(work, "unique.json")
    print("\n[%d/5] Collect unique -> %s" % (step, unique_file))
    run("workflow", "collect", "--cache", cache, "--out", unique_file)

    if os.path.exists(unique_file):
        with open(unique_file, encoding='utf-8') as f:
            data = json.load(f)
        total_items = len(data.get("items", data if isinstance(data, list) else []))
        print("    %d unique texts need translation" % total_items)
        print("\n    >>> Agent: translate %s then run:" % unique_file)
        print("    python -m workflow apply --cache %s --tm %s" % (cache, unique_file))
        print("    python -m workflow verify --cache %s" % cache)
        print("    python -m workflow export --cache %s --output %s" % (cache, output_zh))
    else:
        print("    COLLECT FAILED")

    step += 1

    # ── Step 5: If nothing to translate, verify + export directly ──
    verify_out = run("workflow", "verify", "--cache", cache)
    if verify_out and "100%" in verify_out:
        print("\n[%d/5] Export -> %s" % (step, output_zh))
        run("workflow", "export", "--cache", cache, "--output", output_zh)
        print("\nDone! Output: %s" % output_zh)
    else:
        print("\n[%d/5] Waiting for agent translation..." % step)
        print("    Cache: %s" % cache)
        print("    Output: %s" % output_zh)

    # Cleanup
    if not args.keep_temp and not args.work:
        import shutil
        if os.path.exists(work) and work != args.work:
            shutil.rmtree(work, ignore_errors=True)

    # Machine-readable output
    result = {
        "asr_srt": output_srt,
        "zh_srt": output_zh,
        "cache": cache,
        "unique": unique_file if os.path.exists(unique_file) else None,
        "source_language": src_lang,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
