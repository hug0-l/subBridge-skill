# subBridge — Subtitle Translation Skill

Agent-powered subtitle translation for opencode. Translates **SRT/ASS/VTT/SUB/SMI/LRC** with format preservation, multi-region support, and automatic glossary building.

```
https://github.com/hug0-l/subBridge-skill
```

## Quick Start

```bash
pip install pysubs2 httpx chardet

# 1. Parse
python -m parse --input episode.srt --source-lang en --target-lang zh --region hk --context auto --market asia -o work/cache.json

# 2. Glossary
python -m glossary fetch --cache work/cache.json --source-lang en --target-lang zh --region hk -o work/glossary.populated.json --limit 50
python -m glossary lock -i work/glossary.populated.json -o work/glossary.locked.json

# 3. Auto-translate (handles sound effects + common phrases)
python -m batch read work/cache.json --size 1000 --auto --glossary work/glossary.locked.json --tm work/tm.json --tm-save work/tm.json --uncertain work/uncertain.json --context auto -o work/auto_batch.json
python -m batch write work/cache.json work/auto_batch.json

# 4. Subagent translates remaining uncertain segments
# → use prompt_builder for consistent instructions
python -m prompt_builder --input work/uncertain.json -o prompt.txt --context auto
# → paste prompt into subagent, get translations.json
python -m batch write work/cache.json work/translations.json

# 5. Export (single language or bilingual)
python -m export --cache work/cache.json -o episode.zh-hk.srt
python -m export --cache work/cache.json -o episode.bilingual.srt --bilingual

# 6. Verify
python -m verify quality work/cache.json --market asia
python -m verify glossary work/cache.json work/glossary.locked.json
python -m verify completeness work/cache.json -o work/gaps.json
```

## 3 Translation Modes

| Mode | Speed | Quality | When |
|------|-------|---------|------|
| **A. Manual** | Slow | Highest | Single episode, complex content |
| **B. Auto** (--auto) | Fast | Good | Sound effects + short phrases auto, agent fills rest |
| **C. Hybrid** (auto + subagent) | Fastest | High | Multi-episode series, parallel subagents |

## Features

- **Format-safe**: SRT/ASS/VTT/SUB/SMI/LRC — timing, styles, karaoke, drawings preserved
- **Context-aware**: `--context military/medical/casual/auto` — disambiguates "fire" (開火 vs 火燭)
- **Market-aware CPS**: `--market nordic(14)/western(12)/asia(10)` — reading speed per region
- **Bilingual export**: `--bilingual` — source + translation per segment
- **Glossary update**: `glossary update` — scan cache for new names, Wikipedia merge
- **Prompt builder**: `prompt_builder.py` — standardized subagent instructions
- **Japanese support**: 80+ JP common phrases, medical terms, sound effects
- **CJK-aware**: `cjk_visual_len()` — Chinese chars count as 2 visual units
- **Credit footer**: SRT files get `# AI-translated by subbridge` at end
- **Auto-validation**: batch write auto-fixes list→string subagent bugs
- **Softsub extraction**: ffmpeg-based from MKV/MP4

## Project Structure

```
├── SKILL.md                     # Full documentation
├── subbridge/
│   ├── parse.py                 # Subtitle parser (6 formats)
│   ├── export.py                # Exporter (bilingual, credit footer)
│   ├── batch.py                 # Batch read/write (auto-fix validation)
│   ├── glossary.py              # Discover + fetch + lock + update
│   ├── auto_translate.py        # Context-aware auto-translate engine
│   ├── prompt_builder.py        # Standardized subagent prompt generator
│   ├── verify.py                # Quality + completeness + integrity
│   ├── detect.py                # Language/encoding detection
│   ├── extract.py               # Softsub extraction
│   ├── convert.py               # Format conversion
│   └── helpers.py               # Shared utilities
└── references/
    └── subagent_prompt_template.md
```

## Requirements

- pysubs2 (parsing), httpx (Wikipedia API), chardet (encoding)
- ffmpeg (optional, for softsub extraction)

## License

MIT
