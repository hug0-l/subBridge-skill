"""
Auto-translate engine with context-aware disambiguation.
Handles Level 1 (sound effects) and Level 2 (common phrases) automatically,
with context-based disambiguation for polysemous terms (e.g., "fire" = "開火"
in military context vs "火燭" in casual context).

Context modes:
  military  — tactical/combat dialogue (SEAL Team, war movies)
  medical   — hospital/clinical dialogue
  casual    — everyday conversation, comedy, drama
  auto      — infer from speaker tags + neighboring segments
"""

import json
import re
import os
import sys
from difflib import SequenceMatcher
from typing import Optional


try:
    from helpers import normalize_apostrophes, strip_newline_markers
except ImportError:
    def normalize_apostrophes(t):
        for a, b in [("\u2018", "'"), ("\u2019", "'"),
                      ("\u201c", '"'), ("\u201d", '"'),
                      ("\u2013", "-"), ("\u2014", "--"),
                      ("\u00a0", " ")]:
            t = t.replace(a, b)
        return t

    def strip_newline_markers(t):
        import re as _re
        t = _re.sub(r"\\[Nn]", " ", t)
        t = _re.sub(r"\s+", " ", t).strip()
        return t


# ── Context System ────────────────────────────────────────────

class TranslationContext:
    MILITARY = "military"
    MEDICAL = "medical"
    CASUAL = "casual"
    AUTO = "auto"

    _SPEAKER_CONTEXT = {}  # populated from glossary

    # Keywords that hint at context
    _MILITARY_KEYWORDS = {
        "enemy", "target", "weapon", "rifle", "gun", "fire", "breach",
        "cover", "move", "extract", "hostile", "contact", "op",
        "mission", "bird", "lz", "dz", "bird", "bird(s)", "chopper",
        "sir", "medic", "wia", "kia", "alpha", "bravo", "charlie",
        "delta", "roger", "wilco", "out", "over", "command",
        "soldier", "marine", "navy", "seal", "operator", "troop",
    }
    _MEDICAL_KEYWORDS = {
        "doctor", "nurse", "patient", "surgery", "diagnosis",
        "symptom", "treatment", "prescription", "hospital",
        "clinic", "ward", "OR", "vitals", "BP", "HR", "O2",
        "pain", "bleeding", "fracture", "infection", "dose",
        # Japanese medical
        "医者", "患者", "手術", "診断", "治療", "病院",
        "外科", "内科", "救急", "麻酔", "出血", "採血",
        "血圧", "脈拍", "点滴", "注射", "薬", "医師",
        "看護師", "救急車", "医療", "診察", "入院",
    }

    @classmethod
    def infer_from_speaker(cls, speaker: str) -> Optional[str]:
        return cls._SPEAKER_CONTEXT.get(speaker.lower())

    @classmethod
    def infer_from_text(cls, text: str) -> str:
        clean = normalize_apostrophes(text).lower()
        mil_score = sum(1 for kw in cls._MILITARY_KEYWORDS if kw in clean)
        med_score = sum(1 for kw in cls._MEDICAL_KEYWORDS if kw in clean)
        if mil_score > med_score and mil_score >= 2:
            return cls.MILITARY
        if med_score > mil_score and med_score >= 2:
            return cls.MEDICAL
        return cls.CASUAL

    @classmethod
    def resolve(cls, text: str, provided: Optional[str] = None,
                speaker: Optional[str] = None) -> str:
        if provided and provided != cls.AUTO:
            return provided
        if speaker:
            ctx = cls.infer_from_speaker(speaker)
            if ctx:
                return ctx
        return cls.infer_from_text(text)


# ── Sound Effect Patterns (Level 1) ──────────────────────────

# Japanese sound effects — common in JP subtitles for medical dramas
_JA_SOUND_EFFECTS = [
    # (speaker) tags — character name indicators
    (r"^\([\u3040-\u309f\u30a0-\u30ffa-zA-Zー・]+\)\s*$", None),  # speaker-only line
    # Medical/body sounds
    (r"^\(.*?音\)$", None),  # any (X sound)
]

# Direct translations for Japanese sound effect patterns
_JA_SOUND_MAP = {
    "叫び声": "（叫喊聲）",
    "倒れる音": "（跌倒聲）",
    "いびき": "（鼻鼾聲）",
    "救急車のサイレン": "（救護車響號）",
    "ぶつかる音": "（碰撞聲）",
    "シャッター音": "（快門聲）",
    "タップ音": "（敲擊聲）",
    "ドアの閉まる音": "（關門聲）",
    "ドアの開閉音": "（開關門聲）",
    "ドアの開く音": "（開門聲）",
    "バイブレーターの音": "（震動聲）",
    "ファスナーを下ろす音": "（拉鏈聲）",
    "ホチキスを打つ音": "（釘書機聲）",
    "階段落ちる ドスンドスン": "（跌下樓梯聲）",
    "受話器を置く音": "（放低聽筒聲）",
    "車のドアの開く音": "（車門打開聲）",
    "指を鳴らす音": "（彈指聲）",
    "携帯電話": "（手機響）",
    "話し声": "（說話聲）",
    "物音": "（雜聲）",
    "悲鳴": "（慘叫聲）",
    "銃声": "（槍聲）",
    "スピーカー": "（擴音器）",
    "英語": "（英語）",
}

_SOUND_EFFECTS = {
    "en": {
        "re_bracket": [
            (r"\[music\]", "（音樂）"),
            (r"\[music playing\]", "（音樂播放中）"),
            (r"\[soft music\]", "（柔和音樂）"),
            (r"\[dramatic music\]", "（戲劇性音樂）"),
            (r"\[tense music\]", "（緊張音樂）"),
            (r"\[rock music\]", "（搖滾音樂）"),
            (r"\[electronic music\]", "（電子音樂）"),
            (r"\[indistinct chatter\]", "（模糊對話聲）"),
            (r"\[indistinct shouting\]", "（模糊叫喊聲）"),
            (r"\[shouting continues\]", "（叫喊聲繼續）"),
            (r"\[shouting in distance\]", "（遠處叫喊聲）"),
            (r"\[applause\]", "（掌聲）"),
            (r"\[laughter\]", "（笑聲）"),
            (r"\[laughs\]", "（笑）"),
            (r"\[chuckles\]", "（輕笑）"),
            (r"\[chuckling\]", "（輕笑）"),
            (r"\[scoffs\]", "（嗤笑）"),
            (r"\[groans\]", "（呻吟）"),
            (r"\[groaning\]", "（呻吟聲）"),
            (r"\[grunts\]", "（咕噥）"),
            (r"\[grunting\]", "（咕噥聲）"),
            (r"\[sighs\]", "（嘆氣）"),
            (r"\[sighing\]", "（嘆氣聲）"),
            (r"\[coughs\]", "（咳）"),
            (r"\[coughing\]", "（咳嗽聲）"),
            (r"\[sobbing\]", "（哭泣聲）"),
            (r"\[cries\]", "（喊）"),
            (r"\[crying\]", "（喊聲）"),
            (r"\[gasp\]", "（倒抽一口氣）"),
            (r"\[gasps\]", "（倒抽一口氣）"),
            (r"\[screams\]", "（尖叫）"),
            (r"\[screaming\]", "（尖叫聲）"),
            (r"\[yells\]", "（大叫）"),
            (r"\[yelling\]", "（大叫聲）"),
            (r"\[whispering\]", "（竊竊私語）"),
            (r"\[whispers\]", "（低聲）"),
            (r"\[muttering\]", "（低聲嘀咕）"),
            (r"\[clears throat\]", "（清喉嚨）"),
            (r"\[door opens\]", "（門開）"),
            (r"\[door closes\]", "（關門）"),
            (r"\[door slams\]", "（大力關門）"),
            (r"\[knocks\]", "（敲門）"),
            (r"\[knocking\]", "（敲門聲）"),
            (r"\[knock at door\]", "（敲門聲）"),
            (r"\[phone rings\]", "（電話響）"),
            (r"\[phone ringing\]", "（電話鈴聲）"),
            (r"\[phones chime\]", "（電話提示聲）"),
            (r"\[engine starts\]", "（引擎啟動）"),
            (r"\[engine revving\]", "（引擎加速聲）"),
            (r"\[car horn\]", "（汽車喇叭）"),
            (r"\[tires screech\]", "（輪胎摩擦聲）"),
            (r"\[gunfire\]", "（槍聲）"),
            (r"\[gunshot\]", "（槍聲）"),
            (r"\[automatic gunfire\]", "（機槍聲）"),
            (r"\[explosion\]", "（爆炸聲）"),
            (r"\[explosions\]", "（爆炸聲）"),
            (r"\[alarm blaring\]", "（警報聲）"),
            (r"\[dog barking\]", "（狗吠）"),
            (r"\[dog barks\]", "（狗吠）"),
            (r"\[dogs barking\]", "（狗吠聲）"),
            (r"\[seabirds chirping\]", "（海鳥叫）"),
            (r"\[birds chirping\]", "（鳥叫）"),
            (r"\[rain falling\]", "（雨聲）"),
            (r"\[thunder\]", "（雷聲）"),
            (r"\[wind blowing\]", "（風聲）"),
            (r"\[water splashing\]", "（水聲）"),
            (r"\[footsteps\]", "（腳步聲）"),
            (r"\[glass breaking\]", "（玻璃破碎聲）"),
            (r"\[bottles clink\]", "（酒杯碰撞聲）"),
            (r"\[bottles clinking\]", "（酒杯碰撞聲）"),
            (r"\[helicopter blades\]", "（直升機螺旋槳聲）"),
            (r"\[jet flies overhead\]", "（噴射機掠過）"),
            (r"\[radio chatter\]", "（無線電通話聲）"),
            (r"\[static\]", "（靜電聲）"),
            (r"\[beeping\]", "（嗶嗶聲）"),
            (r"\[alarm\]", "（警報）"),
            (r"\[bell rings\]", "（鈴聲）"),
            (r"\[clock ticking\]", "（時鐘滴答聲）"),
            (r"\[sniffles\]", "（抽鼻水）"),
            (r"\[yawns\]", "（打呵欠）"),
            (r"\[sneezes\]", "（打噴嚏）"),
            (r"\[spits\]", "（吐口水）"),
        ],
        "re_paren": [
            (r"\(sigh[s]?\)", "（嘆氣）"),
            (r"\(laugh[s]?\)", "（笑）"),
            (r"\(laughing\)", "（笑）"),
            (r"\(chuckle[s]?\)", "（輕笑）"),
            (r"\(groan[s]?\)", "（呻吟）"),
            (r"\(grunt[s]?\)", "（咕噥）"),
            (r"\(cough[s]?\)", "（咳）"),
            (r"\(crying\)", "（喊）"),
            (r"\(sobbing\)", "（哭泣）"),
            (r"\(gasp[s]?\)", "（倒吸一口氣）"),
            (r"\(scream[s]?\)", "（尖叫）"),
            (r"\(yell[s]?\)", "（大叫）"),
            (r"\(whisper[s]?\)", "（低聲）"),
            (r"\(music\)", "（音樂）"),
            (r"\(music continues\)", "（音樂繼續）"),
            (r"\(applause\)", "（掌聲）"),
            (r"\(indistinct\)", "（模糊不清）"),
        ],
        "re_music": [
            (r"^(♪\s?)+$", "♪ ♪"),
            (r"^(♫\s?)+$", "♫"),
        ],
        "re_speaker": [
            (r"^([A-Z][A-Z]+):\\N(.+)$", r"(\1)\r\\N\2"),
        ],
    }
}


# ── Common Phrase Library with Context Awareness ──────────────

def _ctx(d: dict, ctx: str) -> str:
    """Resolve a context dict: {military: 開火, casual: 火燭, default: 開火}"""
    return d.get(ctx, d.get("default", d.get("military", list(d.values())[0])))


_PHRASE_LIBRARY = {
    "en": {
        "zh-hk": {
            # ── Context-dependent polysemous entries ──
            "fire": {
                "military": "開火",
                "casual": "火燭",
                "medical": "火燭",
                "default": "開火",
            },
            "fire!": {
                "military": "開火！",
                "casual": "火燭！",
                "default": "開火！",
            },
            "copy": {
                "military": "收到",
                "casual": "明白",
                "default": "收到",
            },
            "copy that": {
                "military": "收到",
                "casual": "明白",
                "default": "收到",
            },
            "roger": {
                "military": "收到",
                "casual": "好",
                "default": "收到",
            },
            "roger that": {
                "military": "收到",
                "casual": "好",
                "default": "收到",
            },
            "negative": {
                "military": "否定",
                "casual": "唔係",
                "default": "否定",
            },
            "affirmative": {
                "military": "肯定",
                "casual": "係",
                "default": "肯定",
            },
            "stand by": {
                "military": "候命",
                "casual": "等陣",
                "default": "候命",
            },
            "clear": {
                "military": "安全",
                "casual": "清楚",
                "default": "安全",
            },
            "all clear": {
                "military": "全部安全",
                "casual": "冇事",
                "default": "全部安全",
            },
            "hit": {
                "military": "擊中",
                "casual": "打中",
                "medical": "中招",
                "default": "打中",
            },
            "i'm hit": {
                "military": "中槍",
                "casual": "我中咗",
                "default": "中槍",
            },
            "target": {
                "military": "目標",
                "casual": "目標",
                "default": "目標",
            },
            "move": {
                "military": "移動",
                "casual": "行",
                "default": "行",
            },
            "contact": {
                "military": "接敵",
                "casual": "聯絡",
                "default": "聯絡",
            },
            "we got contact": {
                "military": "接敵",
                "casual": "有人聯絡",
                "default": "接敵",
            },

            # ── Single-context entries ──

            # Greetings
            "hello": "你好",
            "hi": "嗨",
            "hey": "喂",
            "good morning": "早晨",
            "good afternoon": "午安",
            "good evening": "晚安",
            "good night": "晚安",
            "goodbye": "再見",
            "bye": "拜",
            "see you": "遲啲見",
            "how are you": "你好嗎",
            "how's it going": "點呀",
            "what's up": "做咩",
            "nice to meet you": "幸會",
            "long time no see": "好耐冇見",

            # Confirmations
            "yes": "係",
            "no": "唔係",
            "yeah": "係",
            "yep": "係",
            "nope": "唔係",
            "ok": "好",
            "okay": "好",
            "alright": "好",
            "sure": "當然",
            "of course": "梗係",
            "certainly": "當然",
            "absolutely": "絕對",
            "exactly": "啱",
            "indeed": "的確",
            "right": "啱",
            "correct": "啱",
            "fine": "好",
            "deal": "成交",

            # Radio / military
            "wilco": "照做",
            "say again": "再講一次",
            "over": "完",
            "out": "完",
            "loud and clear": "清楚",
            "come on": "嚟啦",
            "let's go": "走啦",
            "hurry up": "快啲",
            "go go go": "上上上",
            "watch out": "小心",
            "look out": "小心",
            "careful": "小心",
            "be careful": "小心啲",
            "easy": "慢嚟",
            "easy day": "easy day",
            "take it easy": "放鬆啲",
            "hold on": "等陣",
            "hold up": "等陣",
            "wait": "等陣",
            "stop": "停",
            "freeze": "唔准郁",
            "hands up": "舉高手",
            "put your hands up": "舉高你對手",
            "drop it": "放低",
            "move out": "出發",
            "move it": "快啲",
            "get down": "伏低",
            "get back": "退後",
            "back up": "退後",
            "cover me": "掩護我",
            "man down": "人倒咗",
            "medic": "軍醫",
            "reloading": "換彈",
            "breach": "破門",
            "flashbang": "閃光彈",
            "grenade": "手榴彈",
            "incoming": "嚟緊",
            "suppressing": "壓制中",
            "moving": "移動中",
            "standing by": "候命中",

            # Questions
            "what": "咩話",
            "why": "點解",
            "how": "點樣",
            "who": "邊個",
            "where": "邊度",
            "when": "幾時",
            "really": "真係",
            "seriously": "認真",
            "are you sure": "你肯定",
            "are you okay": "你冇事嗎",
            "you okay": "你冇事嗎",
            "what happened": "發生咩事",
            "what's wrong": "做咩",
            "what is it": "咩事",
            "what's that": "咩嚟㗎",
            "what do you mean": "你咩意思",
            "what are you doing": "你做緊咩",
            "where are you": "你喺邊",
            "where is he": "佢喺邊",
            "where is she": "佢喺邊",
            "how long": "幾耐",
            "how many": "幾多",
            "how much": "幾多錢",
            "how do you know": "你點知㗎",
            "who is it": "邊個",
            "who's there": "邊個喺度",

            # Apologies
            "sorry": "對唔住",
            "i'm sorry": "對唔住",
            "i'm so sorry": "我好對唔住",
            "my bad": "我錯",
            "excuse me": "唔好意思",
            "forgive me": "原諒我",
            "no problem": "冇問題",
            "no worries": "唔緊要",
            "don't worry": "唔使擔心",
            "it's fine": "冇事",
            "it's okay": "冇事",

            # Gratitude
            "thank you": "多謝",
            "thanks": "多謝",
            "thank you so much": "唔該晒",
            "thanks a lot": "唔該晒",
            "you're welcome": "唔使客氣",
            "my pleasure": "我嘅榮幸",

            # Knowledge / opinion
            "i know": "我知",
            "i don't know": "我唔知",
            "i have no idea": "我唔知",
            "no idea": "唔知",
            "i think": "我諗",
            "i think so": "我諗係",
            "i don't think so": "我諗唔係",
            "i guess": "我估",
            "maybe": "或者",
            "perhaps": "或者",
            "probably": "應該",
            "definitely": "肯定",
            "i understand": "我明白",
            "i don't understand": "我唔明",
            "i see": "我明",
            "i get it": "我明",
            "got it": "明",

            # Common verbs
            "look": "睇",
            "listen": "聽",
            "watch": "睇住",
            "help": "幫手",
            "come": "嚟",
            "come here": "過嚟",
            "go": "去",
            "stay": "留低",
            "stay here": "留喺度",
            "sit": "坐",
            "sit down": "坐低",
            "stand up": "企起身",
            "get up": "起身",
            "lie down": "瞓低",
            "turn around": "轉身",
            "follow me": "跟我嚟",
            "come with me": "跟我嚟",
            "leave me alone": "唔好理我",
            "let me go": "放開我",
            "let go": "放開",

            # Emotional
            "i love you": "我愛你",
            "i miss you": "我掛住你",
            "i'm here": "我喺度",
            "i'm fine": "我冇事",
            "i'm okay": "我冇事",
            "i'm tired": "我好攰",
            "i'm hungry": "我好肚餓",
            "i'm scared": "我好驚",
            "i'm worried": "我好擔心",
            "i'm happy": "我好開心",
            "i'm sorry for your loss": "節哀順變",
            "are you hurt": "你有冇受傷",
            "it hurts": "好痛",
            "that hurts": "好痛",
            "oh my god": "天呀",
            "jesus": "天呀",
            "christ": "天呀",
            "god": "天呀",
            # Profanity - flagged as written_acceptable=false in glossary
            # Research: taboo words are more acceptable orally than in writing
            "damn": {"written_acceptable": False, "casual": "死啦", "default": "屌"},
            "damn it": {"written_acceptable": False, "casual": "死啦", "default": "屌"},
            "shit": {"written_acceptable": False, "casual": "大鑊", "default": "屌"},
            "fuck": {"written_acceptable": False, "casual": "頂", "default": "屌"},
            "fucking": {"written_acceptable": False, "casual": "乜鳩", "default": "屌"},
            "what the hell": {"written_acceptable": False, "casual": "搞咩", "default": "搞咩"},
            "holy shit": {"written_acceptable": False, "casual": "嘩", "default": "嘩屌"},
            "oh shit": {"written_acceptable": False, "casual": "弊", "default": "屌"},
            "son of a bitch": {"written_acceptable": False, "casual": "死嘢", "default": "仆街"},
            "bastard": {"written_acceptable": False, "casual": "死嘢", "default": "仆街"},

            # Agreements
            "me too": "我都係",
            "same here": "我都係",
            "i agree": "我同意",
            "you're right": "你啱",
            "you're wrong": "你錯",
            "that's true": "係真",
            "that's right": "啱",
            "you too": "你都係",

            # Common fillers / address
            "well": "嗯",
            "so": "咁",
            "um": "嗯",
            "uh": "嗯",
            "ah": "啊",
            "oh": "噢",
            "hmm": "嗯",
            "uh-huh": "嗯",
            "mm-hmm": "嗯",
            "ahh": "啊",
            "eh": "喂",
            "yo": "喂",
            "man": "老友",
            "dude": "老友",
            "bro": "兄弟",
            "brother": "兄弟",
            "buddy": "夥計",
            "kid": "細路",
            "son": "仔",
            "daddy": "爸爸",
            "dad": "老豆",
            "father": "父親",
            "mom": "媽咪",
            "mother": "母親",
            "sir": "先生",
            "ma'am": "太太",
            "miss": "小姐",
            "boss": "大佬",
            "chief": "大佬",
        }
    },
    # Japanese → HK Cantonese common phrases
    "ja": {
        "zh-hk": {
            # Greetings
                "こんにちは": "你好",
                "こんばんは": "晚安",
                "おはよう": "早晨",
                "おはようございます": "早晨",
                "さようなら": "再見",
                "じゃね": "拜",
                "またね": "遲啲見",
                "ありがとう": "多謝",
                "ありがとうございます": "唔該晒",
                "すいません": "唔好意思",
                "すみません": "唔好意思",
                "ごめん": "對唔住",
                "ごめんなさい": "對唔住",
                "失礼します": "失陪",

                # Confirmations
                "はい": "係",
                "いいえ": "唔係",
                "うん": "嗯",
                "そう": "係",
                "そうですね": "係呀",
                "そうです": "係",
                "違う": "唔係",
                "違います": "唔係",
                "大丈夫": "冇事",
                "大丈夫です": "冇事㗎",
                "オーケー": "好",
                "もちろん": "梗係",

                # Exclamations
                "ヤバッ": "大鑊",
                "やばい": "大鑊",
                "まずい": "大鑊",
                "やった": "太好了",
                "すごい": "好犀利",
                "すげえ": "好勁",
                "やめろ": "停手",
                "止めて": "停",
                "待って": "等陣",
                "行こう": "走啦",
                "来い": "過嚟",
                "助けて": "救命",
                "危ない": "小心",
                "大変": "大件事",

                # Medical — specific to Shinjuku Field Hospital
                "外科医": "外科醫生",
                "内科医": "內科醫生",
                "軍医": "軍醫",
                "救急": "急症",
                "患者": "病人",
                "手術": "手術",
                "麻酔": "麻醉",
                "診察": "診症",
                "止血": "止血",
                "縫合": "縫針",
                "点滴": "吊鹽水",
                "注射": "打針",
                "検査": "檢查",
                "入院": "入院",
                "退院": "出院",
                "先生": "醫生",
                "看護師": "護士",
                "院長": "院長",

                # Kabukicho / cultural
                "歌舞伎町": "歌舞伎町",
                "パパ活": "援交",
                "ホスト": "男公關",
                "風俗": "風俗",
                "ＮＰＯ": "NPO",
                "反社": "反社會組織",
                "暴力団": "黑幫",

                # Questions / common
                "何": "咩",
                "何ですか": "咩話",
                "どうした": "做咩",
                "どうしたの": "做咩",
                "なぜ": "點解",
                "どうして": "點解",
                "どこ": "邊度",
                "だれ": "邊個",
                "いつ": "幾時",
                "いくら": "幾多錢",

                # Fillers
                "あの": "嗯",
                "えっと": "嗯",
                "まあ": "嗯",
                "ねえ": "喂",
                "ちょっと": "喂",
            }
        }
    }


# ── AutoTranslate Engine ─────────────────────────────────────

class AutoTranslate:
    """Context-aware auto-translate engine."""

    def __init__(self,
                 glossary_path: Optional[str] = None,
                 tm_path: Optional[str] = None,
                 source_lang: str = "en",
                 target_lang: str = "zh",
                 region: str = "hk",
                 context: str = TranslationContext.AUTO):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.region = region
        self.context = context
        self.glossary = self._load_glossary(glossary_path)
        self.tm = self._load_tm(tm_path)
        self.stats = {"tm_hit": 0, "phrase": 0, "sound": 0,
                      "glossary": 0, "uncertain": 0, "total": 0}

    # ── Glossary ────────────────────────────────────────────

    def _load_glossary(self, path: Optional[str]) -> dict:
        if not path or not os.path.exists(path):
            return {"characters": {}, "terms": {}, "never_translate": set()}
        with open(path, "r", encoding="utf-8") as f:
            gloss = json.load(f)
        result = {"characters": {}, "terms": {}, "never_translate": set()}
        for c in gloss.get("characters", []):
            key = c["canonical"].lower()
            render = c.get("region", {}).get(self.region, c["render"])
            result["characters"][key] = render
            for alias in c.get("aliases", []):
                result["characters"][alias.lower()] = render
        for t in gloss.get("terms", []):
            key = t["src"].lower()
            dst = t.get("region", {}).get(self.region, t["dst"])
            result["terms"][key] = dst
        for nt in gloss.get("never_translate", []):
            result["never_translate"].add(nt["src"].lower())
        return result

    def _speaker_context(self, speaker_key: str) -> Optional[str]:
        """Check if a glossary character implies a context."""
        military_chars = {"jason", "ray", "sonny", "omar", "brock",
                          "trent", "drew", "blackburn", "eric", "mandy",
                          "clay", "davis", "rivas"}
        key = speaker_key.lower()
        if key in military_chars:
            return TranslationContext.MILITARY
        return None

    # ── Translation Memory ───────────────────────────────────

    def _load_tm(self, path: Optional[str]) -> dict:
        if not path or not os.path.exists(path):
            return {"exact": {}, "version": "1.0"}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_tm(self, path: Optional[str]):
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.tm, f, ensure_ascii=False, indent=2)

    def _tm_key(self, text: str) -> str:
        t = normalize_apostrophes(text)
        t = strip_newline_markers(t)
        t = re.sub(r"[^\w\s]", "", t.lower())
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _tm_exact_match(self, text: str) -> Optional[tuple]:
        """Returns (translation, source_key) or None."""
        key = self._tm_key(text)
        val = self.tm.get("exact", {}).get(key)
        if val:
            return (val, key)
        return None

    def _tm_fuzzy_match(self, text: str, threshold: float = 0.85
                         ) -> Optional[tuple]:
        key = self._tm_key(text)
        best_ratio = 0
        best = None
        for stored_key, translation in self.tm.get("exact", {}).items():
            ratio = SequenceMatcher(None, key, stored_key).ratio()
            if ratio > best_ratio and ratio >= threshold:
                best_ratio = ratio
                best = (translation, stored_key)
        return best

    def _add_to_tm(self, source: str, translation: str):
        key = self._tm_key(source)
        self.tm.setdefault("exact", {})[key] = translation

    # ── Sound Effects (Level 1) ─────────────────────────────

    def _match_sound_effects(self, text: str) -> Optional[str]:
        lang = self.source_lang
        t = text.strip()

        # Music notes
        if re.match(r"^[\s♪♫♬]*$", t):
            return t

        # Japanese sound effects — parenthetical (XX)
        if lang in ("ja", "jp"):
            # Direct map lookup for known JA sound phrases
            clean = re.sub(r"^[＜＜]?", "", t)
            clean = re.sub(r"[＞＞]?$", "", clean).strip("()（） ")
            if clean in _JA_SOUND_MAP:
                return _JA_SOUND_MAP[clean]

            # Strip speaker prefix: (南の英語) → match "英語"
            m = re.match(r'^\([^）)]+の([^）)]+)）?$', t)
            if m:
                sub = m.group(1)
                if sub in _JA_SOUND_MAP:
                    return _JA_SOUND_MAP[sub]

            # Speaker-only lines with glossary name
            if re.match(r'^\([\u3040-\u309f\u30a0-\u30ffa-zA-Zー・]+\)$', t):
                name = t.strip("()")
                render = self.glossary["characters"].get(name, name)
                return f"({render})"

        # English sound effects
        if lang not in _SOUND_EFFECTS:
            return None
        effects = _SOUND_EFFECTS[lang]

        # Bracket effects
        for pattern, replacement in effects["re_bracket"]:
            if re.fullmatch(pattern, t, re.IGNORECASE):
                return replacement

        # Parenthetical effects
        for pattern, replacement in effects["re_paren"]:
            if re.fullmatch(pattern, t, re.IGNORECASE):
                return replacement

        # Speaker replay: extract embedded speaker, apply glossary
        for pattern, template in effects["re_speaker"]:
            m = re.match(pattern, t, re.IGNORECASE)
            if m:
                speaker_raw = m.group(1)
                rest = m.group(2)
                name = self.glossary["characters"].get(
                    speaker_raw.lower(), speaker_raw.title())
                return f"({name})\r\\N{rest}"

        return None

    # ── Phrase Matching (Level 2) ───────────────────────────

    def _resolve_text(self, text: str) -> tuple:
        """Normalize text and extract speaker if present."""
        speaker = None
        t = text

        # Extract speaker tag like "SONNY: ..."
        m = re.match(r"^([A-Z][A-Z]+):\s*(.+)$", t)
        if m:
            speaker = m.group(1)
            t = m.group(2)

        # Extract speaker tag like "JASON: ..."
        m = re.match(r"^([A-Z][A-Z]+)\(cont\):\s*(.+)$", t, re.IGNORECASE)
        if m:
            speaker = m.group(1)
            t = m.group(2)

        # Dialogue prefix "- NAME: ..."
        m = re.match(r"^-\s*([A-Z][A-Z]+):\s*(.+)$", t)
        if m:
            speaker = m.group(1)
            t = f"- {m.group(2)}"

        return t, speaker

    def _normalize(self, text: str) -> str:
        t = normalize_apostrophes(text)
        t = strip_newline_markers(t)
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip().lower()
        return t

    def _get_library(self) -> dict:
        lc = f"{self.target_lang}-{self.region}" if self.region else self.target_lang
        lib = _PHRASE_LIBRARY.get(self.source_lang, {}).get(lc, {})
        if not lib:
            lib = _PHRASE_LIBRARY.get(self.source_lang, {}).get(
                self.target_lang, {})
        return lib

    def _resolve_context(self, text: str, speaker: Optional[str] = None) -> str:
        if self.context != TranslationContext.AUTO:
            return self.context
        if speaker:
            ctx = self._speaker_context(speaker)
            if ctx:
                return ctx
        return TranslationContext.infer_from_text(text)

    def _resolve_entry(self, entry, context: str) -> str:
        """Resolve a phrase library entry against current context."""
        if isinstance(entry, dict):
            return _ctx(entry, context)
        return entry

    def _build_lookup(self, library: dict, context: str) -> dict:
        lookup = {}
        for phrase, entry in library.items():
            translation = self._resolve_entry(entry, context)
            norm = self._normalize(phrase)
            lookup[norm] = translation
            words = norm.split()
            if len(words) >= 2:
                for i in range(1, min(len(words), 4)):
                    partial = " ".join(words[:i])
                    if partial not in lookup:
                        lookup[partial] = translation
        return lookup

    def _match_phrase(self, text: str, context: str) -> Optional[str]:
        library = self._get_library()
        if not library:
            return None

        lookup = self._build_lookup(library, context)
        norm = self._normalize(text)

        if not norm:
            return None

        words = norm.split()

        # 1. Exact
        if norm in lookup:
            return self._append_punct(lookup[norm], text)

        # 2. Short text word match
        if len(words) <= 5:
            wk = " ".join(words)
            if wk in lookup:
                return self._append_punct(lookup[wk], text)
            for phrase, translation in sorted(lookup.items(),
                                              key=lambda x: -len(x[0])):
                if wk == phrase:
                    return self._append_punct(translation, text)

        # 3. Word-by-word match
        for phrase, translation in sorted(lookup.items(),
                                          key=lambda x: -len(x[0])):
            if words == phrase.split():
                return self._append_punct(translation, text)

        # 4. Short prefix
        if len(words) <= 3:
            wk = " ".join(words)
            for phrase, translation in sorted(lookup.items(),
                                              key=lambda x: -len(x[0])):
                pwords = phrase.split()
                if len(pwords) >= len(words) and pwords[:len(words)] == words:
                    return self._append_punct(translation, text)

        # 5. Dialogue split
        if text.startswith("-") and "\\N" in text:
            lines = re.split(r"\\[Nn]", text)
            translated = []
            for line in lines:
                line = line.strip().lstrip("- ")
                pr = self._match_phrase(line, context)
                if pr:
                    translated.append(pr)
                else:
                    return None
            if translated:
                return "\\N".join(translated)

        return None

    def _append_punct(self, text: str, original: str) -> str:
        orig = original.strip()
        if orig.endswith("?"):
            return text + "？"
        if orig.endswith("！"):
            return text + "！"
        if orig.endswith("!"):
            return text + "！"
        if orig.endswith("。") or orig.endswith("."):
            return text + "。"
        return text

    # ── Glossary Substitution ────────────────────────────────

    def _apply_glossary(self, text: str) -> str:
        result = text
        terms = sorted(self.glossary["terms"].items(),
                       key=lambda x: -len(x[0]))
        for src, dst in terms:
            if src not in self.glossary["never_translate"]:
                result = re.sub(
                    re.escape(src), dst, result, flags=re.IGNORECASE)
        return result

    # ── Main Translate ──────────────────────────────────────

    def translate_segment(self, source_text: str) -> tuple:
        """
        Returns (translated_text, confidence).
        confidence:
          1.0 = exact TM
          0.9 = phrase library
          0.8 = sound effect
          0.7 = fuzzy TM
          0.6 = glossary substitution only
          0.0 = uncertain, needs agent
        """
        self.stats["total"] += 1
        text = source_text

        if not text.strip():
            self.stats["sound"] += 1
            return ("", 1.0)

        # Never translate
        if text.strip().lower() in self.glossary["never_translate"]:
            self.stats["phrase"] += 1
            return (text, 1.0)

        # Level 1: Sound effects
        sound = self._match_sound_effects(text)
        if sound is not None:
            self.stats["sound"] += 1
            return (sound, 1.0)

        # Extract speaker & resolve context
        clean_text, speaker = self._resolve_text(text)
        ctx = self._resolve_context(clean_text, speaker)

        # Level 2a: TM exact
        tm_result = self._tm_exact_match(text)
        if tm_result:
            self.stats["tm_hit"] += 1
            return (self._apply_glossary(tm_result[0]), 1.0)

        # Level 2b: Phrase
        phrase_result = self._match_phrase(clean_text, ctx)
        if phrase_result:
            self.stats["phrase"] += 1
            # Reconstruct speaker prefix if present
            if speaker and not text.startswith("-"):
                speaker_name = self.glossary["characters"].get(
                    speaker.lower(), speaker.title())
                phrase_result = f"({speaker_name}) {phrase_result}"
            self._add_to_tm(text, phrase_result)
            return (self._apply_glossary(phrase_result), 0.9)

        # Level 2c: Fuzzy TM
        fuzzy = self._tm_fuzzy_match(text)
        if fuzzy:
            self.stats["tm_hit"] += 1
            return (self._apply_glossary(fuzzy[0]), 0.7)

        # Level 2d: Glossary-only substitution
        glossed = self._apply_glossary(text)
        if glossed != text:
            self.stats["glossary"] += 1
            return (glossed, 0.6)

        self.stats["uncertain"] += 1
        return ("", 0.0)

    def translate_batch(self, batch_data: list) -> tuple:
        translations = []
        uncertain = []
        for item in batch_data:
            source = item["source_text"]
            translated, confidence = self.translate_segment(source)
            if translated:
                translations.append({
                    "text_index": item["text_index"],
                    "translated_text": translated,
                    "confidence": confidence,
                })
            if confidence == 0.0:
                uncertain.append(item)
        return translations, uncertain, self.stats


# ── CLI ──────────────────────────────────────────────────────

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Auto-translate subtitle batch")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--uncertain", "-u")
    ap.add_argument("--glossary")
    ap.add_argument("--tm")
    ap.add_argument("--tm-save")
    ap.add_argument("--source-lang", default="en")
    ap.add_argument("--target-lang", default="zh")
    ap.add_argument("--region", default="hk")
    ap.add_argument("--context", default=TranslationContext.AUTO,
                    choices=[TranslationContext.MILITARY,
                             TranslationContext.MEDICAL,
                             TranslationContext.CASUAL,
                             TranslationContext.AUTO])

    args = ap.parse_args(argv)

    with open(args.input, "r", encoding="utf-8") as f:
        batch = json.load(f)

    engine = AutoTranslate(
        glossary_path=args.glossary,
        tm_path=args.tm,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        region=args.region,
        context=args.context,
    )

    translations, uncertain, stats = engine.translate_batch(batch)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(translations, f, ensure_ascii=False, indent=2)

    if args.uncertain and uncertain:
        os.makedirs(os.path.dirname(args.uncertain) or ".", exist_ok=True)
        with open(args.uncertain, "w", encoding="utf-8") as f:
            json.dump(uncertain, f, ensure_ascii=False, indent=2)

    if args.tm_save:
        engine._save_tm(args.tm_save)

    total = stats["total"]
    auto = total - stats["uncertain"]
    pct = (auto / total * 100) if total else 0
    print(f"Auto-translated: {auto}/{total} ({pct:.0f}%)", file=sys.stderr)
    print(f"  TM hits: {stats['tm_hit']}", file=sys.stderr)
    print(f"  Phrase matches: {stats['phrase']}", file=sys.stderr)
    print(f"  Sound effects: {stats['sound']}", file=sys.stderr)
    print(f"  Glossary only: {stats['glossary']}", file=sys.stderr)
    print(f"  Uncertain: {stats['uncertain']}", file=sys.stderr)
    if args.context == TranslationContext.AUTO:
        print(f"  Context mode: auto-inferred", file=sys.stderr)
    else:
        print(f"  Context mode: {args.context}", file=sys.stderr)


if __name__ == "__main__":
    main()
