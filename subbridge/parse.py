"""Parse subtitle files into cache.json with format-specific protection.

Each format has its own protector that knows exactly which bytes
are safe to modify and which must be preserved verbatim.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from helpers import (
    detect_subtitle_format, make_cache, save_cache,
    timecode_to_ms, ms_to_timecode,
    TranslationStatus,
)

# ── Abstract base ──────────────────────────────────────────────


class SubtitleProtector:
    format_name = ""

    def parse(self, raw: str, encoding: str = "utf-8") -> list[dict]:
        raise NotImplementedError

    def export(self, segments: list[dict], raw: str = None,
               encoding: str = "utf-8") -> str:
        raise NotImplementedError


# ── SRT Protector ──────────────────────────────────────────────

SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n"
    r"(\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3})"
    r"\s*\n((?:(?!\n\n|\n\d+\s*\n).)*)",
    re.MULTILINE | re.DOTALL,
)

SRT_TIME_LINE_RE = re.compile(
    r"^(\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3})",
    re.MULTILINE,
)


class SrtProtector(SubtitleProtector):
    format_name = "srt"

    def parse(self, raw: str, encoding: str = "utf-8") -> list[dict]:
        segments = []
        for i, m in enumerate(SRT_BLOCK_RE.finditer(raw)):
            idx = int(m.group(1))
            start_ms = timecode_to_ms(m.group(2))
            end_ms = timecode_to_ms(m.group(3))
            text = m.group(4).strip().replace("\n", "\\N")
            segments.append({
                "text_index": idx,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_text": text,
                "translated_text": "",
                "translation_status": TranslationStatus.UNTRANSLATED,
                "style": "Default",
                "layer": "0",
                "format": "srt",
                "_preserved": {
                    "raw_index": str(idx),
                    "raw_timing": f"{m.group(2)} --> {m.group(3)}",
                    "raw_text": m.group(4),
                },
            })
        return segments

    def export(self, segments: list[dict], raw: str = None,
               encoding: str = "utf-8") -> str:
        out_lines = []
        for seg in segments:
            p = seg.get("_preserved", {})
            text = seg.get("translated_text") or seg["source_text"]
            raw_text = p.get("raw_text", "")
            if seg.get("translated_text"):
                text_export = text.replace("\\N", "\n")
            else:
                text_export = raw_text
            raw_timing = p.get("raw_timing",
                               f"{ms_to_timecode(seg['start_ms'], 'srt')} --> "
                               f"{ms_to_timecode(seg['end_ms'], 'srt')}")
            out_lines.append(f"{p.get('raw_index', str(seg['text_index']))}")
            out_lines.append(raw_timing)
            out_lines.append(text_export)
            out_lines.append("")
        return "\n".join(out_lines)


# ── ASS/SSA Protector ──────────────────────────────────────────

ASS_DRAWING_RE = re.compile(r"\{[^}]*\\p[0-9][^}]*\}.*?\{[^}]*\\p0\}", re.DOTALL)
ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")
ASS_DIALOGUE_RE = re.compile(
    r"^(Dialogue|Comment):\s*(\d+),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),(.*)",
    re.MULTILINE,
)
ASS_FORMAT_LINE = re.compile(r"^Format:\s*.*", re.MULTILINE)
ASS_STYLE_LINE = re.compile(r"^Style:\s*.*", re.MULTILINE)


class AssProtector(SubtitleProtector):
    format_name = "ass"

    def parse(self, raw: str, encoding: str = "utf-8") -> list[dict]:
        segments = []
        idx = 0
        for m in ASS_DIALOGUE_RE.finditer(raw):
            idx += 1
            dtype = m.group(1)
            if dtype == "Comment":
                continue
            layer = m.group(2)
            start_ms = timecode_to_ms(m.group(3))
            end_ms = timecode_to_ms(m.group(4))
            style = m.group(5)
            name = m.group(6)
            margin_l = m.group(7)
            margin_r = m.group(8)
            margin_v = m.group(9)
            effect = m.group(10)
            text = m.group(11)

            has_drawing = bool(ASS_DRAWING_RE.search(text))
            overrides = ASS_OVERRIDE_RE.findall(text)

            segments.append({
                "text_index": idx,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_text": text,
                "translated_text": "",
                "translation_status": TranslationStatus.UNTRANSLATED,
                "style": style,
                "layer": layer,
                "format": "ass",
                "_preserved": {
                    "raw_dtype": dtype,
                    "raw_layer": layer,
                    "raw_timing": f"{m.group(3)},{m.group(4)}",
                    "raw_style": style,
                    "raw_name": name,
                    "raw_margin_l": margin_l,
                    "raw_margin_r": margin_r,
                    "raw_margin_v": margin_v,
                    "raw_effect": effect,
                    "raw_full_line": m.group(0),
                    "has_drawing": has_drawing,
                    "overrides": overrides,
                },
            })
        return segments

    def _p(self, seg: dict, key: str, fallback: str = "") -> str:
        p = seg.get("_preserved", {})
        return p.get(key, fallback)

    def export(self, segments: list[dict], raw: str = None,
               encoding: str = "utf-8") -> str:
        if raw:
            lines = raw.split("\n")
            new_lines = []
            preserved_idx = {}
            for seg in segments:
                preserved_idx[seg["text_index"]] = seg
            dia_idx = 0
            for line in lines:
                m = ASS_DIALOGUE_RE.match(line)
                if m and m.group(1) == "Dialogue":
                    dia_idx += 1
                    seg = preserved_idx.get(dia_idx)
                    if seg and seg.get("translated_text"):
                        text = seg["translated_text"]
                        t = ms_to_timecode(seg["start_ms"], "ass")
                        e = ms_to_timecode(seg["end_ms"], "ass")
                        new_lines.append(
                            f"Dialogue: {self._p(seg, 'raw_layer', seg.get('layer', '0'))},"
                            f"{self._p(seg, 'raw_timing', t + ',' + e)},"
                            f"{self._p(seg, 'raw_style', seg.get('style', 'Default'))},"
                            f"{self._p(seg, 'raw_name', '')},"
                            f"{self._p(seg, 'raw_margin_l', '0')},"
                            f"{self._p(seg, 'raw_margin_r', '0')},"
                            f"{self._p(seg, 'raw_margin_v', '0')},"
                            f"{self._p(seg, 'raw_effect', '')},{text}"
                        )
                    else:
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            return "\n".join(new_lines)

        lines = []
        for seg in segments:
            text = seg.get("translated_text") or seg["source_text"]
            t = ms_to_timecode(seg["start_ms"], "ass")
            e = ms_to_timecode(seg["end_ms"], "ass")
            lines.append(
                f"Dialogue: {self._p(seg, 'raw_layer', seg.get('layer', '0'))},"
                f"{self._p(seg, 'raw_timing', t + ',' + e)},"
                f"{self._p(seg, 'raw_style', seg.get('style', 'Default'))},"
                f"{self._p(seg, 'raw_name', '')},"
                f"{self._p(seg, 'raw_margin_l', '0')},"
                f"{self._p(seg, 'raw_margin_r', '0')},"
                f"{self._p(seg, 'raw_margin_v', '0')},"
                f"{self._p(seg, 'raw_effect', '')},{text}"
            )
        return "\n".join(lines)


# ── VTT Protector ──────────────────────────────────────────────

VTT_CUE_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}\.\d{3})"
    r"((?:\s+\w+(?::[\w.%]+)?)*)\s*\n((?:(?!\n\n).)*)",
    re.MULTILINE | re.DOTALL,
)

VTT_HEADER_BLOCK_RE = re.compile(
    r"^(WEBVTT.*?)(?=\n\d{1,2}:\d{2}|\n\n|\Z)",
    re.MULTILINE | re.DOTALL,
)


class VttProtector(SubtitleProtector):
    format_name = "vtt"

    def parse(self, raw: str, encoding: str = "utf-8") -> list[dict]:
        segments = []
        idx = 0
        for m in VTT_CUE_RE.finditer(raw):
            idx += 1
            start_ms = timecode_to_ms(m.group(1))
            end_ms = timecode_to_ms(m.group(2))
            settings = m.group(3).strip()
            text = m.group(4).strip().replace("\n", "\\N")
            segments.append({
                "text_index": idx,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_text": text,
                "translated_text": "",
                "translation_status": TranslationStatus.UNTRANSLATED,
                "style": "Default",
                "layer": "0",
                "format": "vtt",
                "_preserved": {
                    "raw_timing": f"{m.group(1)} --> {m.group(2)}{' ' + settings if settings else ''}",
                    "settings": settings,
                },
            })
        return segments

    def export(self, segments: list[dict], raw: str = None,
               encoding: str = "utf-8") -> str:
        if raw:
            header_m = VTT_HEADER_BLOCK_RE.match(raw)
            header = header_m.group(1) if header_m else "WEBVTT"
            lines = [header, ""]
            for seg in segments:
                p = seg.get("_preserved", {})
                text = seg.get("translated_text") or seg["source_text"]
                lines.append(p.get("raw_timing",
                                   f"{ms_to_timecode(seg['start_ms'], 'vtt')} --> "
                                   f"{ms_to_timecode(seg['end_ms'], 'vtt')}"))
                lines.append(text.replace("\\N", "\n"))
                lines.append("")
            return "\n".join(lines)
        return ""


# ── SUB (MicroDVD) Protector ───────────────────────────────────

SUB_LINE_RE = re.compile(r"^\{(\d+)\}\{(\d+)\}(.*)", re.MULTILINE)
SUB_TAGS_RE = re.compile(r"\{[^}]*\}")


class SubProtector(SubtitleProtector):
    format_name = "sub"

    def parse(self, raw: str, encoding: str = "utf-8") -> list[dict]:
        segments = []
        idx = 0
        for m in SUB_LINE_RE.finditer(raw):
            idx += 1
            start_frame = int(m.group(1))
            end_frame = int(m.group(2))
            text = m.group(3)
            format_tags = SUB_TAGS_RE.findall(text)
            segments.append({
                "text_index": idx,
                "start_ms": start_frame,
                "end_ms": end_frame,
                "source_text": text,
                "translated_text": "",
                "translation_status": TranslationStatus.UNTRANSLATED,
                "style": "Default",
                "layer": "0",
                "format": "sub",
                "_preserved": {
                    "raw_frames": f"{{{m.group(1)}}}{{{m.group(2)}}}",
                    "format_tags": format_tags,
                },
            })
        return segments

    def export(self, segments: list[dict], raw: str = None,
               encoding: str = "utf-8") -> str:
        lines = []
        for seg in segments:
            p = seg.get("_preserved", {})
            text = seg.get("translated_text") or seg["source_text"]
            lines.append(f"{p['raw_frames']}{text}")
        return "\n".join(lines)


# ── SMI (SAMI) Protector ───────────────────────────────────────

SMI_SYNC_RE = re.compile(r"(<SYNC\s+[^>]*>)\s*(.*?)(?=(<SYNC|\Z))", re.MULTILINE | re.DOTALL)
SMI_START_RE = re.compile(r"Start\s*=\s*(\d+)", re.IGNORECASE)


class SmiProtector(SubtitleProtector):
    format_name = "smi"

    def parse(self, raw: str, encoding: str = "utf-8") -> list[dict]:
        segments = []
        idx = 0
        for m in SMI_SYNC_RE.finditer(raw):
            idx += 1
            sync_tag = m.group(1)
            content = m.group(2).strip()
            start_m = SMI_START_RE.search(sync_tag)
            start_ms = int(start_m.group(1)) if start_m else idx * 1000
            segments.append({
                "text_index": idx,
                "start_ms": start_ms,
                "end_ms": 0,
                "source_text": content,
                "translated_text": "",
                "translation_status": TranslationStatus.UNTRANSLATED,
                "style": "Default",
                "layer": "0",
                "format": "smi",
                "_preserved": {"raw_sync_tag": sync_tag},
            })
        for i in range(len(segments) - 1):
            segments[i]["end_ms"] = segments[i + 1]["start_ms"]
        if segments:
            segments[-1]["end_ms"] = segments[-1]["start_ms"] + 5000
        return segments

    def export(self, segments: list[dict], raw: str = None,
               encoding: str = "utf-8") -> str:
        if raw:
            header_end = raw.find("<BODY>")
            footer_start = raw.rfind("</BODY>")
            header = raw[:header_end + 6] if header_end > 0 else "<SAMI>\n<BODY>"
            footer = raw[footer_start:] if footer_start > 0 else "</BODY>\n</SAMI>"
            body_parts = [header]
            for seg in segments:
                p = seg.get("_preserved", {})
                text = seg.get("translated_text") or seg["source_text"]
                body_parts.append(f'  {p.get("raw_sync_tag", "<SYNC Start=" + str(seg["start_ms"]) + ">")}')
                body_parts.append(f'    <P>{text}</P>')
            body_parts.append(footer)
            return "\n".join(body_parts)
        return ""


# ── LRC Protector ──────────────────────────────────────────────

LRC_META_RE = re.compile(r"^\[(ti|ar|al|by|offset|re|ve):.*\]", re.MULTILINE)
LRC_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{2})[\.:](\d{2,3})\](.*)")
LRC_WORD_RE = re.compile(r"<\d{1,3}:\d{2}[\.:]\d{2,3}>")


class LrcProtector(SubtitleProtector):
    format_name = "lrc"

    def parse(self, raw: str, encoding: str = "utf-8") -> list[dict]:
        segments = []
        idx = 0
        for m in LRC_TIME_RE.finditer(raw):
            idx += 1
            mins = int(m.group(1))
            secs = int(m.group(2))
            frac = m.group(3).ljust(3, "0")[:3]
            start_ms = mins * 60000 + secs * 1000 + int(frac)
            text = m.group(4)
            word_timing = LRC_WORD_RE.findall(text)
            segments.append({
                "text_index": idx,
                "start_ms": start_ms,
                "end_ms": start_ms + 5000,
                "source_text": text,
                "translated_text": "",
                "translation_status": TranslationStatus.UNTRANSLATED,
                "style": "Default",
                "layer": "0",
                "format": "lrc",
                "_preserved": {
                    "raw_timestamp": m.group(0).split("]")[0] + "]",
                    "word_timing_tags": word_timing,
                },
            })
        for i in range(len(segments) - 1):
            segments[i]["end_ms"] = segments[i + 1]["start_ms"]
        return segments

    def export(self, segments: list[dict], raw: str = None,
               encoding: str = "utf-8") -> str:
        if raw:
            meta_lines = [l for l in raw.split("\n") if LRC_META_RE.match(l)]
            lines = list(meta_lines)
            for seg in segments:
                p = seg.get("_preserved", {})
                text = seg.get("translated_text") or seg["source_text"]
                lines.append(f"{p['raw_timestamp']}{text}")
            return "\n".join(lines)
        return ""


# ── Format Registry ────────────────────────────────────────────

PROTECTORS = {
    "srt": SrtProtector(),
    "ass": AssProtector(),
    "vtt": VttProtector(),
    "sub": SubProtector(),
    "smi": SmiProtector(),
    "lrc": LrcProtector(),
}


def get_protector(fmt: str) -> SubtitleProtector:
    prot = PROTECTORS.get(fmt)
    if not prot:
        prot = SrtProtector()
    return prot


# ── CLI ────────────────────────────────────────────────────────


def main(argv=None):
    ap = argparse.ArgumentParser(description="Parse subtitle file into cache.json")
    ap.add_argument("--input", "-i", required=True, help="Input subtitle file")
    ap.add_argument("--out", "-o", required=True, help="Output cache.json path")
    ap.add_argument("--format", "-f", default="auto",
                    choices=["auto", "srt", "ass", "vtt", "sub", "smi", "lrc", "ttml"],
                    help="Subtitle format (auto-detect by default)")
    ap.add_argument("--source-lang", default="", help="Source language code")
    ap.add_argument("--target-lang", default="", help="Target language code")
    ap.add_argument("--region", default="", help="Target region variant")
    ap.add_argument("--context", default="auto",
                    choices=["military", "medical", "casual", "auto"],
                    help="Context for disambiguation (default: auto-infer)")
    ap.add_argument("--market", default="asia",
                    choices=["nordic", "western", "asia"],
                    help="Target market for CPS: nordic(14), western(12), asia(10)")
    ap.add_argument("--encoding", default="", help="File encoding (auto-detect if empty)")
    args = ap.parse_args(argv)

    raw_bytes = open(args.input, "rb").read()

    enc = args.encoding
    if not enc:
        try:
            import chardet
            result = chardet.detect(raw_bytes)
            enc = result["encoding"] or "utf-8"
            if enc.lower() in ("ascii",):
                enc = "utf-8"
        except ImportError:
            enc = "utf-8"

    raw = raw_bytes.decode(enc, errors="replace")

    fmt = args.format
    if fmt == "auto":
        fmt = detect_subtitle_format(raw)

    protector = get_protector(fmt)
    segments = protector.parse(raw, enc)

    print(f"Format detected: {fmt}")
    print(f"Encoding detected: {enc}")
    print(f"Parsed {len(segments)} segments", file=sys.stderr)

    folder = os.path.dirname(args.out)
    if folder:
        os.makedirs(folder, exist_ok=True)

    cache = make_cache(
        segments=segments,
        source_path=os.path.abspath(args.input),
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        target_region=args.region,
        original_format=fmt,
        original_encoding=enc,
        raw_header="",
    )
    cache["context"] = args.context
    cache["market"] = args.market
    save_cache(cache, args.out)
    print(f"Written: {args.out} ({len(segments)} segments)")


if __name__ == "__main__":
    main()
