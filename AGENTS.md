# subBridge-skill AGENTS.md

## Last Session (2026-07-14): 32-Episode zh-hk Fix

### What was done
Fixed 32 episodes of poor machine translation (SEAL S3 E09-E20, SEAL S5 all, SAS S2 all) to natural Cantonese.

### Pipeline
1. `translate_bulk.py` — extract unique EN texts from SRT
2. Spawn general subagent to translate ALL unique texts
3. `apply_translations.py` — apply idx_NNN→zh-hk JSON back to SRT
4. Verify CJK% > 90%

### Critical Lessons

**Agent JSON format is unreliable.** Agents frequently output:
- Nested under `{"translations": {...}}` → extract inner dict
- BOM encoding (utf-8-sig) → re-save as utf-8
- Python repr string `{'idx_000': '...'}` instead of JSON → `ast.literal_eval`
- English text as keys instead of idx_NNN → need `unique_to_indices` mapping

**EN SRT sourcing:**
- tvsubtitles.net: use CookieJar, download season ZIP
- subtitlecat.com: SRT embedded in HTML, extract with regex
- S5 EN files were actually Chinese — had to download proper EN SRTs

**Military radio mapping (prompt requirement):**
- Copy/Copy that → 收到
- Roger → 明白
- This is... → 呢度係...
- Out → 收線
- How copy? → 收唔收到？

**Character names: keep English** with explicit list in prompt.

**UTF-16 SRT files** need conversion before pysubs2.load().

**Parallel agents:** max 7 at a time, else timeout.

### Scores
All 32 eps > 90% CJK except S5E07 (85.7%), S5E12 (87.8%), SAS S2E05 (79.6%) — mostly sound effects and alignment drift.

### Next Session
- File-based TM (translation_memory.json) ready for next show
- docs/ and .github/ directories set up but empty
- SKILL.md has 19 sections now (up to file-based TM)
