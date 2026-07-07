"""
Build standardized subagent translation prompts.

Generates a complete system prompt for subagent translation tasks,
parameterized by source/target language, context, glossary, etc.
"""

import os
import json
from typing import Optional

# ── Context-specific rules ──────────────────────────────────

_CONTEXT_RULES = {
    "military": """
### Military Context
- Radio comms: "copy" → "收到", "roger" → "收到", "negative" → "否定"
- Tactical: "fire" → "開火", "cover me" → "掩護我", "contact" → "接敵"
- Profanity: keep strong register (屌, 仆街) — soldiers swear
- Chain of command: address as 先生/長官
- Keep ranks: Lieutenant, Sergeant, Chief, etc. untranslated
""",
    "medical": """
### Medical Context
- Diagnoses & procedures: keep accurate; add common name in brackets if needed
  e.g. "急性硬膜下血腫 (acute subdural hematoma)"
- Drugs: keep generic/INN names or translate if well-known
- Medical jargon: can be kept if context makes it clear, otherwise simplify
- Patient-doctor: polite register, use 先生/太太/小姐
- Profanity: soften — less acceptable in hospital setting
- Dialects: if characters speak dialect (e.g., 岡山弁), mark with [方言] or equivalent
""",
    "casual": """
### Casual Context
- Natural everyday speech: use conversational Cantonese
- Slang: use HK-equivalent; avoid obscure mainland slang
- Profanity: match source intensity but prefer softer written form
  e.g., "fuck" → "頂" not "屌" unless strong emotion
- Keep cultural references if known in HK; localize if obscure
""",
}

# ── Language pair rules ────────────────────────────────────

_LANG_PAIRS = {
    ("en", "zh-hk"): """
### English → HK Cantonese
- Contractions: "I'm" → "我", "don't" → "唔好", "can't" → "唔可以"
- Phrasal verbs: translate idiomatically, not literally
- Code-switching (mixed EN/ZH): fully translate to Cantonese
""",
    ("ja", "zh-hk"): """
### Japanese → HK Cantonese
- Honorifics: さん→先生/小姐 based on context, 君→ omit or 仔
- Sentence-final particles: ね→呢/呀, よ→㗎/喎, か→嗎/呀
- Dialects (e.g. 岡山弁): mark with [方言] if distinctive
- Code-switching (mixed JA/EN): fully translate to Cantonese
- パパ活 → "援交" / "爸爸活" depending on context
- On-screen text markers 《》→ [畫面：]
""",
}

# ── Glossary rendering ─────────────────────────────────────

def _render_glossary_block(path: Optional[str]) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            gloss = json.load(f)
    except Exception:
        return ""
    chars = gloss.get("characters", [])
    terms = gloss.get("terms", [])
    lines = []
    if chars:
        lines.append("### Glossary — Characters")
        for c in chars:
            render = c.get("render", c["canonical"])
            region = c.get("region", {})
            for r_val in region.values():
                render = r_val
            alias = c.get("aliases", [])
            note = c.get("note", "")
            alias_str = f" (aliases: {', '.join(alias)})" if alias else ""
            note_str = f" — {note}" if note else ""
            lines.append(f"- {c['canonical']} → {render}{alias_str}{note_str}")
    if terms:
        lines.append("\n### Glossary — Terms")
        for t in terms:
            dst = t.get("dst", t["src"])
            lines.append(f"- {t['src']} → {dst}")
    return "\n".join(lines)


# ── Main prompt builder ────────────────────────────────────

def build_prompt(
    input_path: str,
    output_path: str,
    glossary_path: Optional[str] = None,
    source_lang: str = "en",
    target_lang: str = "zh",
    region: str = "hk",
    context: str = "auto",
    market: str = "asia",
    bilingual: bool = False,
    include_credit: bool = True,
    extra_rules: str = "",
) -> str:
    target_region = f"{target_lang}-{region}" if region else target_lang
    lang_pair = (source_lang, target_region)
    cps_map = {"nordic": 14, "western": 12, "asia": 10}
    cps = cps_map.get(market, 10)

    idx_suffix = ""

    prompt = f"""You are a professional {target_lang.upper()} subtitle translator.
Translate ALL segments from the input file. Do NOT skip any segment.

## Task
1. Read input: `{input_path}` — JSON array of segments
2. Read glossary (if provided) for character names and terms
3. Translate EVERY segment to {target_region.upper()} ({region.upper()})
4. Write output: `{output_path}` — JSON array of {{"text_index": N, "translated_text": "..."}}

## Core Style Rules

### Cantonese > Written Chinese
- Use 粵語口語: 係/喺/嘅/佢/啲/冇/唔/咗/返/話俾/畀
- Particles: 呀、啦、喇、嘛、啫、㗎、喎
- NOT 普通話書面語: 是/在/的/他/們/沒有/不/了/回/告訴

### Translation Universals (apply in order)
1. **Simplify** — shorter synonyms, drop redundant words
2. **Explicitate** — add connectives where needed for flow
3. **Normalize** — match TL norms

### Timing Awareness
Each segment has `start_ms` and `end_ms`. Max {cps} CPS (chars/sec).
Max 2 lines. Max 36 visual units per line (CJK=2, Latin=1).
If text is too long: condense aggressively.

### Special Cases
| Case | Rule |
|------|------|
| Sound effects (laughs, sighs) | （笑）（嘆氣） |
| Music ♪ ♫ | Preserve as-is |
| Speaker tags | Use glossary names: (X) |
| Dialogue `- A.\\N- B.` | `- A.\\N- B.` |
| On-screen text | [畫面：...] |
| Unclear [inaudible] | [聽唔清] |
| \\N line breaks | Preserve |
| HTML tags `<i>` | Preserve |
"""

    # Context rules
    if context in _CONTEXT_RULES:
        prompt += _CONTEXT_RULES[context]

    # Language pair rules
    if lang_pair in _LANG_PAIRS:
        prompt += _LANG_PAIRS[lang_pair]
    elif source_lang in ("ja", "jp"):
        prompt += _LANG_PAIRS.get(("ja", "zh-hk"), "")

    # Glossary block
    gloss_block = _render_glossary_block(glossary_path)
    if gloss_block:
        prompt += f"\n{gloss_block}\n"

    # Bilingual mode rules
    if bilingual:
        prompt += """
### Bilingual Mode
Each segment will be output as source + translation side by side.
- CRITICAL: Your `translated_text` MUST contain ONLY the translation.
  Do NOT include the source text in your output.
- The system will merge source + translation automatically during export.
- Write natural, readable Cantonese — do NOT mirror English word order.
"""

    # Output validation
    prompt += """
### Output Validation (CRITICAL)
- `translated_text` must be a STRING, never a list/array.
  Good: `"translated_text": "你好嗎？"`
  BAD:  `"translated_text": ["你好嗎？"]`
- `translated_text` must NOT be empty for segments with source text.
- Keep \\N for line breaks within the string.
"""

    # Credit
    if include_credit:
        prompt += """
### Export Credit
The output file will include a credit footer:
  # AI-translated by subbridge
  # https://github.com/hug0-l/subBridge-skill
Do not add your own credit inside the translation text.
"""

    # Extra rules
    if extra_rules:
        prompt += f"\n### Extra Rules (from user)\n{extra_rules}\n"

    prompt += """
### Output Format
```json
[{"text_index": 1, "translated_text": "..."}]
```
Valid JSON. UTF-8. EVERY segment. NO skips.

Now translate ALL segments from the input file."""
    return prompt


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build subagent prompt")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--glossary")
    ap.add_argument("--source-lang", default="en")
    ap.add_argument("--target-lang", default="zh")
    ap.add_argument("--region", default="hk")
    ap.add_argument("--context", default="auto",
                    choices=["military", "medical", "casual", "auto"])
    ap.add_argument("--market", default="asia",
                    choices=["nordic", "western", "asia"])
    ap.add_argument("--bilingual", action="store_true",
                    help="Enable bilingual mode rules")
    ap.add_argument("--no-credit", action="store_true",
                    help="Skip credit mention in prompt")
    ap.add_argument("--extra-rules", default="")
    args = ap.parse_args()
    prompt = build_prompt(
        input_path=args.input,
        output_path=args.output,
        glossary_path=args.glossary,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        region=args.region,
        context=args.context,
        market=args.market,
        bilingual=args.bilingual,
        include_credit=not args.no_credit,
        extra_rules=args.extra_rules,
    )
    print(prompt)
