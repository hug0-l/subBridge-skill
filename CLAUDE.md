# subBridge-skill — CLAUDE.md

## Project
Subtitle translation tool for SRT/ASS/VTT. EN → zh-hk (Cantonese). Also supports zh-CN, ja, ko.

## Key Commands
- `python translate_bulk.py <en.srt> <output_unique.json>` — extract unique EN texts
- `python apply_translations.py <en.srt> <translations.json> <output.zh-hk.srt>` — apply translations
- `python fix_translations.py` — normalize agent JSON output (nested/BOM/Python repr)
- `python convert_encoding.py` — UTF-16→UTF-8 SRT conversion

## Output Format Rules (CRITICAL)
Agent translations MUST be flat JSON: `{"idx_000": "翻譯", "idx_001": "..."}`
DO NOT nest under `translations` key. DO NOT use English text as keys. DO NOT use Python repr strings.

## Translation Rules
- Natural Cantonese: 嘅/佢/喺/哋/唔/咗/嘢/啲/係(not 是)/冇(not 沒有)
- NO Mandarin: avoid 把/被/掉/這/那/什麼
- Character names: keep English
- Military radio: Copy→收到, this is→呢度係, Out→收線, How copy?→收唔收到？
- Song lyrics (♪): keep original
- Credit segments: filtered by regex in apply_translations.py

## CJK QC Threshold
> 90% = passing. Below means too many untranslated segments.
Non-CJK segments are usually `[sound effects]`, lyrics, or English names — acceptable if dialogue is covered.

## EN SRT Sourcing (when missing)
1. tvsubtitles.net — download season ZIP with CookieJar
2. subtitlecat.com — extract SRT from HTML embed

## Pipeline Performance
- ~800 unique texts/ep → agent 1-2 min
- 6 parallel agents → 6 eps in ~3 min
- 32 eps: ~4 hours wall time
