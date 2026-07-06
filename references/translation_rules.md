# Subtitle Translation Rules

## Core Principles

1. **Line-by-line correspondence**: Translate each [N] segment independently. Do not merge, split, or reorder.

2. **Timing integrity**: Never modify timecodes or frame numbers. Only the text content changes.

3. **Format protection**: All non-text elements are preserved verbatim:
   - ASS override tags (`{\fn...}`, `{\pos...}`, `{\c...}`, etc.)
   - ASS drawing commands (`{\p1}...{\p0}`)
   - Karaoke timing tags (`\k`, `\K`, `\ko`)
   - HTML/XML tags in SRT/VTT/SMI
   - VTT cue settings (position, line, align)
   - SUB format tags (`{y:}`, `{fc:}`, `{sf:}`)

4. **Line length limit**: Each subtitle line should not exceed 42 characters (CJK: ~20 characters). Maximum 2 lines per subtitle.

5. **CPD limit**: Characters per second should not exceed 15. If translation is too long for the time window, shorten it.

## Characters & Terms

6. **Character names**: Follow `glossary.locked.json`:
   - `forced_keep: true` → preserve the source name as-is
   - `render` set → use that translation
   - `region.tw/cn/hk` → use the region-specific variant

7. **Terms**: Follow `glossary.locked.json`:
   - `keep_source: true` → leave untranslated
   - `dst` set → use that translation
   - `region` values → use region-specific variant

8. **Unknown names/terms**: If a capitalized word appears that is not in the glossary:
   - Preserve the source word as-is
   - Note it for later glossary update

## Language & Style

9. **Full-width punctuation**: For CJK translations, use full-width punctuation (，。！？「」).

10. **No CJK spacing**: Do not add spaces between Chinese/Japanese/Korean characters.

11. **Dialogue naturalness**: Write dialogue that sounds natural when read aloud at subtitle speed. Avoid literal translations.

12. **Honorifics**: Omit Japanese honorifics (さん、ちゃん、くん、様) unless the glossary specifies otherwise.

## Forbidden

- Do not add, remove, or reorder segments
- Do not modify timecodes
- Do not strip or corrupt format tags
- Do not translate ASS drawing commands
- Do not translate ASS `Comment:` lines
- Do not add meta-commentary or notes in the output
