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


def _extract_audio(video_path: str) -> tuple[str, str]:
    """Extract audio from video file. Returns (audio_path, base_name)."""
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    base = os.path.splitext(os.path.basename(video_path))[0]
    audio_path = str(EXTRACT_DIR / f"{base}_audio.wav")

    print(f"Extracting audio: {audio_path}", file=sys.stderr)
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-v", "quiet",
        "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        audio_path,
    ]
    result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=300)
    if result.returncode != 0:
        print("ffmpeg audio extraction failed.", file=sys.stderr)
        sys.exit(1)
    return audio_path, base


def _write_srt(segments: list, srt_path: str) -> int:
    """Write whisper segments to SRT file. Returns segment count."""
    with open(srt_path, "w", encoding="utf-8") as f:
        seg_idx = 1
        for seg in segments:
            start = seg.get("start", 0)
            end = seg.get("end", 0)
            text = seg.get("text", "").strip()
            if not text:
                continue

            speaker = seg.get("speaker", None)
            if speaker:
                text = f"({speaker}) {text}"

            start_ts = _ms_to_srt_timecode(int(start * 1000))
            end_ts = _ms_to_srt_timecode(int(end * 1000))
            f.write(f"{seg_idx}\n{start_ts} --> {end_ts}\n{text}\n\n")
            seg_idx += 1
    return seg_idx - 1


def cmd_asr_whisperx(args, audio_path: str, base: str,
                      output_dir: str) -> tuple[str, str, int]:
    """ASR via WhisperX (NVIDIA GPU only, with alignment + optional diarization)."""
    try:
        import whisperx
    except ImportError:
        print("Error: whisperx not installed.", file=sys.stderr)
        print("  pip install whisperx", file=sys.stderr)
        print("  Requires: CUDA 12.8 + NVIDIA GPU (≥8GB VRAM)", file=sys.stderr)
        sys.exit(1)

    device = args.device or "cuda"
    model_name = args.model or "large-v2"
    compute_type = args.compute_type or "float16"

    print(f"Loading WhisperX model '{model_name}' on {device}...", file=sys.stderr)
    try:
        asr_model = whisperx.load_model(model_name, device, compute_type=compute_type)
    except Exception as e:
        print(f"Failed to load model: {e}", file=sys.stderr)
        sys.exit(1)

    print("Transcribing...", file=sys.stderr)
    audio = whisperx.load_audio(audio_path)
    result = asr_model.transcribe(audio, batch_size=args.batch_size or 16)
    detected_lang = result.get("language", args.language or "en")
    print(f"Detected language: {detected_lang}", file=sys.stderr)

    # Align
    print("Aligning timestamps...", file=sys.stderr)
    try:
        align_model, align_metadata = whisperx.load_align_model(
            language_code=detected_lang, device=device)
        result = whisperx.align(
            result["segments"], align_model, align_metadata,
            audio, device, return_char_alignments=False)
    except Exception as e:
        print(f"Alignment skipped: {e}", file=sys.stderr)

    # Diarize (optional)
    if args.diarize:
        if not args.hf_token:
            print("Error: --hf-token required for diarization.", file=sys.stderr)
            sys.exit(1)
        print("Running speaker diarization...", file=sys.stderr)
        try:
            from whisperx.diarize import DiarizationPipeline
            diarize_model = DiarizationPipeline(token=args.hf_token, device=device)
            diarize_segments = diarize_model(audio)
            result = whisperx.assign_word_speakers(diarize_segments, result)
        except Exception as e:
            print(f"Diarization failed: {e}", file=sys.stderr)

    segments = result["segments"]
    srt_path = os.path.join(output_dir, f"{base}.asr.srt")
    seg_count = _write_srt(segments, srt_path)
    return srt_path, detected_lang, seg_count


def cmd_asr_faster(args, audio_path: str, base: str,
                    output_dir: str) -> tuple[str, str, int]:
    """Lightweight ASR via faster-whisper (CPU / AMD GPU via OpenVINO)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("Error: faster-whisper not installed.", file=sys.stderr)
        print("  pip install faster-whisper", file=sys.stderr)
        print("  Runs on CPU or NVIDIA GPU (no CUDA toolkit needed)", file=sys.stderr)
        sys.exit(1)

    device = args.device or "cpu"
    model_name = args.model or "tiny"
    compute_type = args.compute_type or "int8"

    # Map compute types
    if device == "cpu":
        compute_type = "int8"
    elif device == "cuda":
        compute_type = args.compute_type or "float16"

    print(f"Loading faster-whisper model '{model_name}' on {device}...", file=sys.stderr)
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as e:
        print(f"Failed to load model: {e}", file=sys.stderr)
        sys.exit(1)

    language = args.language or None

    print("Transcribing...", file=sys.stderr)
    segments_gen, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
    )

    detected_lang = info.language if info else (language or "en")
    print(f"Detected language: {detected_lang} (probability: "
          f"{info.language_probability:.2f})", file=sys.stderr)

    # faster-whisper returns generator; collect all segments
    segments = []
    for seg in segments_gen:
        segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })

    srt_path = os.path.join(output_dir, f"{base}.asr.srt")
    seg_count = _write_srt(segments, srt_path)
    return srt_path, detected_lang, seg_count


def cmd_asr(args):
    """Generate subtitles from audio using ASR."""
    has_ffmpeg = check_tool("ffmpeg")
    if not has_ffmpeg:
        print("Error: ffmpeg is required for audio extraction.", file=sys.stderr)
        sys.exit(1)

    video_path = args.input
    if not os.path.isfile(video_path):
        print(f"Error: file not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    # Auto-detect device
    if args.device is None:
        try:
            import torch
            args.device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            args.device = "cpu"

    # Auto-pick model
    if args.model is None:
        args.model = "base" if args.device == "cpu" else "large-v3-turbo"

    # Auto-pick compute type
    if args.compute_type is None:
        args.compute_type = "int8" if args.device == "cpu" else "float16"

    # Determine output path
    if args.output:
        srt_out = args.output
        output_dir = os.path.dirname(srt_out) or "."
        base = os.path.splitext(os.path.basename(srt_out))[0]
    else:
        output_dir = args.output_dir or os.path.dirname(video_path)
        base = os.path.splitext(os.path.basename(video_path))[0]
        srt_out = os.path.join(output_dir, f"{base}.srt")

    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Extract audio
    audio_path, _ = _extract_audio(video_path)

    # Step 2: Run selected backend
    try:
        if args.backend == "whisperx":
            _, lang, seg_count = cmd_asr_whisperx(args, audio_path, base, output_dir)
        else:
            _, lang, seg_count = cmd_asr_faster(args, audio_path, base, output_dir)
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass

    # Rename the auto-generated srt to our desired path
    generated = os.path.join(output_dir, f"{base}.asr.srt")
    if os.path.exists(generated) and generated != srt_out:
        os.rename(generated, srt_out)

    print(f"\nOutput: {srt_out}", file=sys.stderr)
    print(f"Segments: {seg_count}", file=sys.stderr)
    print(f"Language: {lang}", file=sys.stderr)

    output = {
        "output_path": srt_out,
        "segments": seg_count,
        "language": lang,
        "backend": args.backend,
        "model": args.model,
    }
    print(json.dumps(output, ensure_ascii=False))


def _ms_to_srt_timecode(ms: int) -> str:
    """Convert milliseconds to SRT timecode format HH:MM:SS,mmm."""
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


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

    asr = sub.add_parser("asr", help="Generate subtitles via ASR")
    asr.add_argument("--input", "-i", required=True, help="Video file path")
    asr.add_argument("--output", "-o", default=None,
                     help="Output .srt path (default: video file path + .srt)")
    asr.add_argument("--backend", choices=["whisperx", "faster-whisper"],
                     default="faster-whisper",
                     help="ASR backend: whisperx (NVIDIA GPU, accurate) or "
                          "faster-whisper (CPU/AMD, lightweight)")
    asr.add_argument("--model", default=None,
                     help="Model size (tiny/base/small/medium/large-v2/large-v3) "
                          "or HuggingFace model ID. Default: base (CPU), large-v3-turbo (GPU)")
    asr.add_argument("--device", default=None,
                     help="Device: cpu, cuda, auto (default: auto-detect)")
    asr.add_argument("--compute-type", default=None,
                     help="Compute type: int8 (CPU), float16 (GPU), float32 "
                          "(default: auto-pick)")
    asr.add_argument("--batch-size", type=int, default=16,
                     help="Batch size for inference (whisperx only)")
    asr.add_argument("--language", default=None,
                     help="Language code (auto-detect if omitted)")
    asr.add_argument("--diarize", action="store_true",
                     help="Enable speaker diarization (whisperx only)")
    asr.add_argument("--hf-token", default=None,
                     help="HuggingFace token for diarization (whisperx only)")
    asr.add_argument("--output-dir", default=None,
                     help="Output directory (default: video file directory)")

    args = ap.parse_args(argv)
    if args.command == "list":
        cmd_list(args)
    elif args.command == "extract":
        cmd_extract(args)
    elif args.command == "auto":
        cmd_auto(args)
    elif args.command == "asr":
        cmd_asr(args)


if __name__ == "__main__":
    main()
