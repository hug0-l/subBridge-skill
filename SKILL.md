---
name: subbridge-skill
description: Translate subtitle files (SRT/ASS/VTT/SUB/SMI/LRC) with format preservation, glossary management, and multi-region support. Triggers on "translate subtitle", "翻译字幕", "subtitle translate", "字幕翻譯", "subbridge", and similar.
---

# subBridge 技能指南

## 總覽

端到端字幕翻譯工具。**Agent 本身是翻譯引擎**，Python 腳本負責解析/匯入/匯出/術語獲取。

```
[字幕檔] → parse → [cache.json] → batch read → [Agent 翻譯] → batch write → [循環] → export → [譯文字幕]
                                        ↑                                    ↓
                                   [glossary.locked.json] ← discover / fetch / agent webfetch / user lock
```

### 支援格式

| 格式 | 讀取 | 寫出 | 特殊保護 |
|------|------|------|---------|
| **SRT** (SubRip) | ✅ | ✅ | HTML 標籤 `<i><b><font>` |
| **ASS/SSA** | ✅ | ✅ | 繪圖指令、K 值、Override tags、Comment 行 |
| **VTT** (WebVTT) | ✅ | ✅ | CSS 區塊、Cue 參數、`<c><v><ruby>` 標籤 |
| **SUB** (MicroDVD) | ✅ | ✅ | 幀號、`{y:}{fc:}{sf:}` 標籤 |
| **SMI** (SAMI) | ✅ | ✅ | `<STYLE>` 區塊、CLASS 屬性、`<SPAN>` 結構 |
| **LRC** (Lyrics) | ✅ | ✅ | 元資料頭、逐字時間標記 |
| **TTML/DFXP** | ❌ | ❌ | 可透過 lxml 擴充 |

### 依賴

```bash
pip install pysubs2 httpx chardet
```

### 命令前綴（後文用 `<PFX>` 代替）

```bash
PYTHONPATH="$SKILL_DIR/subbridge" python
# 或直接在 subbridge/ 目錄下執行：
cd "$SKILL_DIR/subbridge"
python parse.py ...
```

---

## 翻譯前問詢

啟動翻譯前，agent **必須逐項向用戶獲取以下資訊**。括號內為預設值或可選項。

### 必要資訊

```
? 字幕檔案路徑：
  > （用戶輸入路徑，或拖入檔案）

? 源語言（來源語言）：
  > [auto-detect] / en / ja / ko / fr / de / es / pt / it / ru / ar / th / vi / zh / ...

? 目標語言（翻譯目標）：
  > zh / en / ja / ko / pt / es / ...
```

### 目標區域（僅部分語言需要）

若目標語言有多個區域變體，需確認：

```
? 目標區域：
  （所選語言僅一個區域則跳過）

  - 中文（zh）:  tw（臺灣正體） / cn（中國大陸簡體） / hk（香港繁體）
  - 葡萄牙語（pt）:  pt（歐洲） / br（巴西）
  - 英語（en）:  us（美式） / uk（英式）
  - 法語（fr）:  fr（法國） / ca（加拿大）
  - 西班牙語（es）:  es（歐洲） / mx（墨西哥） / ar（阿根廷）
```

區域影響：引號風格、標點全/半形、術語表區域變體取值、翻譯提示詞風格指引。

### 介入模式

```
? 介入模式：
  > A. 抽樣確認（預設）— 先譯約 1 批（30-50 段）給用戶確認風格，再自動繼續
  > B. 每批過目 — 每批譯完皆展示，點頭後才寫回
  > C. 全自動 — 整本一次性譯完，新實體不停止，記入 needs_review
```

### 術語表策略

```
? 術語表：
  > A. 自動發掘（預設）— 從字幕掃描候選 → Wikipedia API 查詢 → agent webfetch 補缺
  > B. 使用現有檔案 — 提供 glossary.locked.json 路徑
  > C. 跳過（不建立術語表，所有名字保留原文）
```

### 可選：自訂規則

```
? 特殊處理規則（可選，直接輸入或跳過）：
  > 角色名全部保留英文
  > 稱謂（先生/女士）不翻譯
  > 歌曲/旁白用斜體標註
  > ...（用戶自訂）
  > 跳過
```

### 確認摘要

收集完所有資訊後，agent 應展示摘要請用戶確認：

```
==================== 翻譯設定摘要 ====================
  字幕檔案：  The President's Cake (2025) WEBRip-1080p.srt
  格式：      SRT（971 段）
  源語言：    en
  目標語言：  zh
  目標區域：  tw（臺灣正體）
  介入模式：  A（抽樣確認）
  術語表：    自動發掘
  特殊規則：  角色名全部保留英文
====================================================
? 確認以上設定？(Y/n) >
```

用戶確認後才開始執行工作流程。

---

## 工作流程

### 步驟 2：解析字幕

```bash
<PFX> -m parse --input episode.srt \
  --source-lang ja --target-lang zh --region tw \
  --out work/cache.json
```

- 自動檢測格式與編碼
- 每條字幕段包含：`text_index`, `start_ms`, `end_ms`, `source_text`, `_preserved`（格式保護資料）
- ASS 的繪圖指令 / Karaoke 標籤 / Comment 行全部存入 `_preserved` 不要翻譯
- 輸出統計：`Parsed 342 segments`

### 步驟 3：建立術語表

**方式 A（推薦 — 直接從整份字幕掃描 + Wikipedia 查詢，一步到位）：**
```bash
# 直接從 cache.json 提取所有候選 + 查 Wikipedia
# 自動掃描整個字幕的所有大寫詞組/專有名詞，無頻率過濾
# 真實案例：Michael (2026) 提取出 678 個潛在術語
<PFX> -m glossary fetch \
  --cache work/cache.json \
  --source-lang en --target-lang zh --region tw \
  --out work/glossary.populated.json \
  --limit 100
```

**方式 B（傳統分步 — 先 discover 再 fetch）：**
```bash
# 3a. 從字幕文本發現候選術語
<PFX> -m glossary discover --cache work/cache.json \
  --source-lang ja --output work/candidates.json

# 3b. 自動從 Wikipedia API 獲取翻譯
<PFX> -m glossary fetch \
  --candidates work/candidates.json \
  --source-lang ja --target-lang zh --region tw \
  --out work/glossary.populated.json
```

**後續（不論 A/B）：**
```bash
# 3c. Agent webfetch 填補空缺（_gaps 陣列）
#     讀取 _gaps[] 的 search_urls，用 webfetch 查詢，填入 glossary

# 3d. 用戶審查後鎖定
<PFX> -m glossary lock --input work/glossary.populated.json \
  --out work/glossary.locked.json
```

**⚠️ 實戰教訓 — 候選噪音過濾：**
自動 discover 會產出大量誤報：
- 句子開頭大寫：`You`、`Let`、`Get` — 需要停用詞表過濾
- SRT 換行殘留：`\N` + 後續詞 → `Nto`、`Nwe` — **discover 前必須先 strip `\N`**
- 常見英文詞：`President`(職位)、`Doctor`(稱呼) — 需要 user 確認是否為角色

**過濾建議：** 先用 `—min-freq 3`，再用停用詞表去噪，最後用人眼確認。真實案例：51 候選 → 過濾後 5 個真實角色名。使用 `fetch --cache` 模式可繞過頻率過濾，直接掃描全文。

**術語表資料類型：**
- `characters[]` — 角色，含 region (tw/cn/hk) 變體、aliases、forced_keep 標記
- `terms[].category` — place / organization / title / skill / item / concept / rank_title / species / vehicle / food / exclamation / measure / term（兜底）
- `non_translate_patterns[]` — 不可翻譯的正則模式（ASS 繪圖、K 值、VTT 標籤等）
- `never_translate[]` — 全域不翻譯詞（OK, Lt., Dr.）
- `regions[]` — 引號風格、標點規則、wiki 變體
- `rules[]` — 行長、行數、CPD 限制等

### 翻譯基本原則

**一路直去，除非真係唔確定：**

| 情況 | 做法 |
|------|------|
| 確定無疑的翻譯（日常對話、場景描述、常見用語） | **直接翻譯，唔停，唔使問** |
| 不在術語表內的人名/地名，合乎常理推斷 | 按常理推斷直接翻譯，記入 `_auto_filled`，最後一齊報告 |
| 真正無法判斷（原文嚴重歧義、文化特定概念） | 記入 `_uncertain` 列表，暫且保留原文，全部處理完後一次過問 |

> **核心規則：唔好停低問用戶，除非真係冇辦法決定。** 名人傳記（Michael Jackson 等）的角色名直接用常識翻譯。合理推斷就直接做。所有唔肯定的記錄在 report 裡，全部完成後一次過報告。

> **強制規則：用戶已經表明唔需要逐批確認。直接做晒全部，有問題最後一次過報告。**

---

### 步驟 4：翻譯循環

#### 核心規則：Agent 直接逐段翻譯，不要寫 script 配對

**⚠️ 實戰教訓：** 不要在 Python script 裡寫字典/規則來翻譯。971 段的真實影片有 1271 種不同台詞，字典配對只覆蓋 ~25%，其餘 75% 還是英文。**翻譯是 agent 的責任，不是 script 的責任。**

正確做法 — 每次批次 30-50 段：

```bash
# 4a. 讀取未譯批次
<PFX> -m batch read work/cache.json --size 50 --output work/batch.json
```

輸出 `work/batch.json` 格式：
```json
[
  {"text_index": 1, "source_text": "Hello world!", "start_ms": 1500, "end_ms": 4000},
  {"text_index": 2, "source_text": "- Come on.\n- Wait!", "start_ms": 5000, ...}
]
```

```bash
# 4b. Agent 翻譯
```
Agent 逐段閱讀 `work/batch.json`，對每段 **用自己的語言能力翻譯**，
遵循 `references/translation_rules.md` + `work/glossary.locked.json`：

- 保留 `[N]` 標籤格式（這是批次索引，不是字幕換行）
- 原文中的 `\N` 是字幕換行，譯文中也保留 `\N`
- 對話 `- A\N- B` → 「A」「B」
- 人物名依 glossary，不在表內的**保留原文 + 記入 uncertain**
- 每一行**逐行對應**，不要合併或拆分

保存為 `work/translations_001.json` 格式：
```json
[
  {"text_index": 1, "translated_text": "哈囉世界！"},
  {"text_index": 2, "translated_text": "「快啲啦。」「等陣！」"}
]
```

```bash
# 4c. 寫回快取
<PFX> -m batch write work/cache.json work/translations_001.json

# 重複 4a→4c 直到 batch read 返回 []
```

#### 片段處理指引

字幕常將一句話拆成多段，或一段含多句對話。處理方式：

| 情況 | 原文 | 譯文 |
|------|------|------|
| 連續句子 | `Line one\NLine two` | `第一行\N第二行` |
| 對話 | `- A.\N- B?` | `「A。」「B？」` |
| 截斷 | `because...` | 保留截斷：`因為...` |
| HTML 標籤 | `<i>song</i>` | 保留：`<i>歌詞</i>` |
| 重複行 | `Hello.` 出現 20 次 | 同一翻譯保持一致 |

### 步驟 5：匯出

```bash
<PFX> -m export \
  --cache work/cache.json \
  --output out/episode_translated.ass \
  --format ass --region tw
```

- 格式保護：原始檔案作為範本，只取代文字欄位
- 可轉換格式（SRT→ASS、VTT→SRT 等）

### 步驟 6：校驗

```bash
# 翻譯品質檢查（行長、CPD、空翻譯）
<PFX> -m verify quality work/cache.json --cps 15 --max-chars 42 --max-lines 2

# 術語合規檢查
<PFX> -m verify glossary work/cache.json work/glossary.locked.json

# 格式完整性檢查（比對原始檔與輸出檔）
<PFX> -m verify integrity --original episode.srt --output out/episode_translated.srt
```

---

## 格式保護機制

每種字幕格式有專屬的 `SubtitleProtector`，清楚知道哪些位元組可修改、哪些必須保留：

| 格式 | 不可修改的內容 | 只修改 |
|------|--------------|--------|
| SRT | 序號、`-->` 時間碼、HTML 標籤 | 時間碼後的文字內容 |
| ASS | `[Script Info]`, `Style:` 行, `Comment:` 行, 繪圖 `{\p1}...{\p0}`, 所有 `{\...}` tags | `Dialogue:` 行最後一個 `,` 後的文字 |
| VTT | `WEBVTT` 頭, `STYLE` 區塊, `REGION` 區塊, Cue 參數, `<c><v><ruby>` | 時間碼與參數後的文字 |
| SUB | `{start}{end}` 幀號, `{y:}`, `{fc:}`, `{sf:}` 標籤 | 標籤後的純文字 |
| SMI | `<HEAD>`, `<STYLE>`, CLASS, `<BR>`, `<SPAN>` 屬性 | `<P>` 標籤內文字節點 |
| LRC | `[ti:][ar:]` 頭部, `[MM:SS.xx]` 時間戳, `<>` 逐字標記 | 時間戳後的文字 |

---

## 從影片提取內嵌字幕

若字幕嵌在 MKV/MP4 等影片容器中（softsub），先提取再翻譯：

### 依賴

需安裝 **ffmpeg** 或 **MKVToolNix**：
- Windows: `winget install ffmpeg` 或下載 MKVToolNix
- macOS: `brew install ffmpeg mkvtoolnix`
- Linux: `apt install ffmpeg mkvtoolnix`

### 命令

```bash
# 列出影片中的所有字幕軌
<PFX> -m extract list --input episode.mkv

# Output:
#   Index  Codec            Language    Title
#     0    subrip           chi         繁體中文
#     1    ass              jpn         日本語
#     2    hdmv_pgs_subtitle  jpn        (bitmap, skip)

# 提取特定字幕軌
<PFX> -m extract extract --input episode.mkv --tracks 0 --format srt

# 提取全部（自動）
<PFX> -m extract auto --input episode.mkv

# 提取後直接接續翻譯流程
EXTRACTED=$(python extract.py auto --input episode.mkv \
  | python -c "import sys,json; d=json.load(sys.stdin); print(d[0]['output_path'])")
$PFX -m parse --input "$EXTRACTED" --target-lang zh --region tw --out work/cache.json
```

### 支援的內嵌格式

| 容器內碼 | 提取後格式 | 說明 |
|---------|-----------|------|
| SRT (subrip) | `.srt` | 最常見 |
| ASS/SSA | `.ass` | 含完整樣式 |
| WebVTT | `.vtt` | Web 標準 |
| TX3G/mov_text | `.srt` | MP4 常見，ffmpeg 轉 SRT |
| PGS (bluray) | ❌ | 點陣圖，需 OCR |
| VobSub | ❌ | 點陣圖，需 OCR |

---

## 命令速查

```bash
# 路徑變數
SKILL_DIR="$HOME/.config/opencode/skills/subtitle-translate"
PFX="PYTHONPATH=$SKILL_DIR/subbridge python"

# 解析
$PFX -m parse --input ep01.srt --source-lang ja --target-lang zh --region tw --out work/cache.json

# 術語表
$PFX -m glossary discover --cache work/cache.json --source-lang ja > work/candidates.json
$PFX -m glossary fetch --candidates work/candidates.json \
  --source-lang ja --target-lang zh --region tw --out work/glossary.populated.json
# → agent webfetch _gaps
$PFX -m glossary lock --input work/glossary.populated.json --out work/glossary.locked.json

# 翻譯循環
$PFX -m batch read work/cache.json --size 50
# → agent 翻譯
$PFX -m batch write work/cache.json work/translations_001.json

# 匯出
$PFX -m export --cache work/cache.json --output out/ep01_tw.ass --format ass

# 校驗
$PFX -m verify quality work/cache.json --cps 15 --max-chars 42 --max-lines 2
$PFX -m verify glossary work/cache.json work/glossary.locked.json
$PFX -m verify integrity --original ep01.srt --output out/ep01_translated.srt

# 格式檢測
$PFX -m detect --input ep01.srt

# 格式互轉（純轉換不翻譯）
python convert.py --input ep01.srt --output ep01.ass
```

---

## 與 ainiee-translate 的關鍵差異

| 維度 | ainiee-translate | subtitle-translate |
|------|-----------------|-------------------|
| 輸入 | EPUB/TXT 小說 | SRT/ASS/VTT/SUB 字幕 |
| 核心約束 | 保留富文本標籤 | **保留時間碼 + 行長限制 + CPD 可讀性** |
| 樣式 | 行內 HTML 標籤 | ASS 完整樣式（font/color/pos/effect/karaoke/drawing） |
| 術語獲取 | 從 AiNiee config import | **Wikipedia API + agent webfetch，支援任意語言** |
| 區域 | 中文 TW/CN | **任意語言 × 任意區域（zh tw/cn/hk, pt pt/br, en us/uk...）** |
| 格式轉換 | 無 | 字幕格式互轉 |
| 時間完整性 | 無 | **時間碼從不修改，只換文字** |

---

## 實戰經驗（v1.0 實測教訓）

本技能經過兩部完整電影測試：
- **The President's Cake**（971 段，伊拉克背景劇情片）
- **Michael (2026)**（2,596 段，米高積遜傳記片）

以下是從 3,567 段真實字幕翻譯中學到的教訓：

### 翻譯策略

| 策略 | 結果 | 結論 |
|------|------|------|
| 字典 script 配對（手寫 1,000+ 條） | ~25% 覆蓋 | ❌ **不建議。** 真實台詞多樣性太高 |
| 正則模式匹配（I'm X → 我係X） | ~5% 額外覆蓋 | ⚠️ 輔助可用，不能做主力 |
| Agent 逐段翻譯 + Unicode 正規化 | 100% 覆蓋 | ✅ **正確做法。** Agent 直接產 translation JSON |

**結論：** Python script 只管解析/匯出/管理。**翻譯是 agent 的責任。** 不要試圖用 script 以程式方式產生翻譯 — 你會花 80% 時間在除錯語法錯誤而不是在翻譯。

### Unicode 正規化（最大教訓）

字幕檔案充斥 Unicode 花括號 / curly quotes，與 Python dict 的 straight quotes 不匹配：

| 字符 | Unicode | 出現位置 | 影響 |
|------|---------|---------|------|
| `'` (U+2019) | RIGHT SINGLE QUOTATION MARK | `you're`、`don't` | **dict key 匹配失敗** |
| `"` (U+201C) | LEFT DOUBLE QUOTATION MARK | quoted dialogue | **dict key 匹配失敗** |
| `"` (U+201D) | RIGHT DOUBLE QUOTATION MARK | quoted dialogue | **dict key 匹配失敗** |
| `–` (U+2013) | EN DASH | ranges | **dict key 匹配失敗** |

**解決方案：** 所有 pattern matching 前先用 `normalize_apostrophes()` 正規化。

### `\N` 換行符破壞 Exact Match

SRT 用 `\N` 表示換行，但 dict key 冇 `\N`。匹配前必須：

```python
text = text.replace("\\N", " ").replace("\\n", " ")
text = re.sub(r' +', ' ', text).strip()
```

**實戰案例：** `"I know you would\\Nnever let me down."` → 要變成 `"I know you would never let me down."` 先 match 到。

### 大小寫不敏感匹配

字幕中大小寫混用（`MICHAEL:` / `Michael:` / `michael`）。所有名稱替換必須用 `re.IGNORECASE`：

```python
# 正確
text = re.sub(re.escape("Michael"), "米高積遜", text, flags=re.I)

# 錯誤（會漏掉 MICHAEL / michael）
text = re.sub(r'(?<![a-zA-Z])Michael(?![a-zA-Z])', "米高積遜", text)
```

### Glossary discover 噪音處理

自動發現 51 個候選，過濾後只剩 5 個真實角色名。幹擾來源：
1. **SRT 換行殘留**：`\Nto`、`\Nwe` — discover 前必須 strip `\N`（已修復）
2. **句子開頭大寫**：`You`、`Let`、`Get`、`Just` — 需要停用詞表
3. **常見職稱**：`President`、`Doctor`、`Sir` — 需人工判斷是否為角色

**推薦：** 使用 `glossary fetch --cache` 直接從全文掃描（678 候選），繞過頻率過濾。

### Windows 路徑陷阱

路徑含特殊字元（`President's Cake` 的 `'`）時，PowerShell inline Python 會報錯。解決方案：
- 統一用 `python script.py` 格式，不要用 `python -c "inline code"`
- 參數路徑用正斜線 `C:/Users/...`
- 必要時寫臨時 .py 檔案然後執行

### 函數定義順序（Rookie Mistake）

多個 1,000+ 行 script 因為 function 定義喺 call 之後而報 `NameError`。永遠用以下 pattern：

```python
def main():
    # main logic here
    pass

def helper_function():
    pass

if __name__ == "__main__":
    main()
```

### 往返測試很重要

正式用於新格式前，先跑一遍 `verify integrity` 確保工具鏈能無損處理該文件。ASS 的 Comment 行 / 繪圖 / K 值最容易在往返中丟失。



**Q: 翻譯後時間碼變了？**
A: 不應該。時間碼存在 `_preserved.raw_timing`，匯出時原樣寫回。若變了請回報 bug。

**Q: ASS 的繪圖被翻譯了？**
A: `{\p1}...{\p0}` 之間的文字受 `non_translate_patterns` 保護。不會被翻譯。

**Q: 翻譯內容超出字幕顯示時間？**
A: 用 `verify quality --cps 15` 檢查。CPD 過高的段需要縮短譯文。

**Q: 如何處理 Netflix 的 TTML/DFXP？**
A: 當前不支援。如有需要可增加 TtmlProtector，用 `lxml` 處理。

**Q: 有些角色名沒在 Wikipedia 查到？**
A: 這些在 `_gaps` 陣列裡，agent 會用 webfetch 查官方站點補齊。

**Q: 匯出後中文字變亂碼？**
A: 原始檔案可能是 Shift-JIS 編碼。用 `--encoding utf-8` 指定匯出編碼，或先用 detect 檢查。
