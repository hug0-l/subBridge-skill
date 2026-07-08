---
name: subbridge-skill
description: Translate subtitle files (SRT/ASS/VTT/SUB/SMI/LRC) with format preservation, glossary management, and multi-region support. Triggers on "translate subtitle", "翻译字幕", "subtitle translate", "字幕翻譯", "subbridge", and similar.
---

# subBridge 技能指南

## 總覽

端到端字幕翻譯工具。支援三種翻譯模式：

```
Mode A: Manual  — Agent 逐段翻譯（最高品質）
Mode B: Auto    — auto_translate.py 處理 Level 1-2（音效+短語），剩餘 agent 處理
Mode C: Hybrid  — Auto + Subagent 平行翻譯（最快，適合多集/多檔案）
```

```
[字幕檔] → parse --market --context → [cache.json]
                                              │
                    ┌─── batch read ──→ [Agent 手譯] ──┐
                    │   (mode B: --auto)               │
                    │   (mode C: --auto + subagent)    │
                    │   (prompt_builder.py 生成 prompt)│
                    └──────────────────────────────────┘
                                              │
                                  batch write → [cache.json]
                                    (auto-fix list→string)
                                              │
                                  glossary update ←──┐
                                    (scan new names)  │
                                              │
                                  verify completeness → [loop if gaps]
                                              │
                                  export --bilingual? ──┐
                                  export --no-credit?   │
                                              │
                                  [譯文字幕.zh-hk.srt]
                                    (credit footer) ← repo URL
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

## ⚠️ 首要規則：先問，唔好估

**即使同一個 session、同一個 user，每份檔案都可能有唔同要求。必須每次問清楚。**

真實案例：
- SEAL Team S7 → military context，角色名用英文
- The President's Cake → 伊拉克背景，阿拉伯人名要音譯
- 新宿野戦病院 → 日劇，medical context，Okayama 方言要特別處理

**絕對假設：**
- ❌ 唔好假設源語言（日文字幕唔一定係日文，可能混英文）
- ❌ 唔好假設目標語言（用戶上次要粵語，今次要台繁）
- ❌ 唔好假設 context（上套係軍事，今套係醫療）
- ❌ 唔好假設檔案路徑嘅含義（`.en.srt` 唔一定係英文）
- ❌ 唔好假設用戶要翻譯（可能只係 check 下）

### 標準問詢清單

```
? 你想我做咩？（翻譯/校驗/轉格式/提取字幕/其他）

? 字幕檔案路徑：
  > （用戶輸入）

? 源語言係咩？
  > [auto-detect] / en / ja / ko / zh / es / ...

? 目標語言？
  > zh / en / ja / ...

? 目標區域？（如需）
  > tw / cn / hk / br / ...

? Context（多義詞消歧）：
  > military / medical / casual / auto（默認）

? Market（CPS 速度）：
  > nordic(14cps) / western(12cps) / asia(10cps)

? 有冇特別要求？
  > 例如：方言處理、角色名保留原文、歌曲唔譯...
```

### 確認摘要

收集完所有資訊後，agent 應展示摘要請用戶確認：

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

用戶確認後才開始執行工作流程。唔好 skip 呢步。

> **實戰教訓：** 新宿野戦病院 E01 嘅字幕包含 Okayama 方言（岡山弁）、英日 code-switching、醫療術語、同歌舞伎町文化用語（パパ活、ホスト等）。如果唔先問清楚，直接預設「日文→粵語」係唔夠嘅——要知角色名點譯、方言點處理、文化詞彙點 localize。

---

## ⚠️ 嚴重警告：auto-translate ≠ full translation

**auto_translate.py 只處理 Level 1（音效）+ Level 2（常用短句），覆蓋率通常 ~10-18%。**

其餘 82-90% 係對話（Level 3-4），**必須經過 subagent 翻譯先係完成品**。

### 錯誤案例

batch 翻譯 438 部電影時，直接 auto → export，結果：
```
Total segments: 1109
Auto-translated: 202 (18%)
Untranslated: 907 (82%) → 全部係英文
```
檔案名 `movie.zh-hk.srt` 令人以為係完整粵語字幕，但實際 82% 內容係原文。

### 正確流程

```bash
# ❌ 錯：auto → export（~18% translated）
<PFX> -m batch read cache.json --size 1000 --auto ... --output auto_batch.json
<PFX> -m batch write cache.json auto_batch.json
<PFX> -m export --cache cache.json -o out.zh-hk.srt  # ← 82% 英文！

# ✅ 啱：auto → subagent → export（100% translated）
<PFX> -m batch read cache.json --size 1000 --auto ... --uncertain uncertain.json
<PFX> -m batch write cache.json auto_batch.json
# → subagent 翻譯 uncertain.json
<PFX> -m batch write cache.json subagent_translations.json  # ← 補齊 82%
<PFX> -m verify completeness cache.json   # ← 確認 100%
<PFX> -m export --cache cache.json -o out.zh-hk.srt
```

### Pre-flight check（export 前強制檢查）

`verify completeness` 必須回報 100% 先准 export：
```bash
# 低過 100% 就唔好 export
<PFX> -m verify completeness cache.json
# → {"completeness_pct": 18.0}  ← STOP! 仲有 82% 未譯
```

### 對 batch 翻譯嘅影響

- auto_translate 係 **pre-processor**，唔係 translator
- batch translate 必須包含 subagent step，否則出嚟嘅 .zh-hk.srt 係半成品
- 用 TM 可以逐步提升 auto-translate 覆蓋率，但永遠唔會到 100%
- **永遠永遠永遠**要行 `verify completeness` 先當完成

---

## 工作流程

---

## 工作流程

> **啟動前必讀：** 開始以下任何步驟前，請確認已執行翻譯前問詢並獲用戶確認。唔好跳過問詢步驟。

### 步驟 2：解析字幕

```bash
<PFX> -m parse --input episode.srt \
  --source-lang en --target-lang zh --region hk \
  --context military --market asia \
  --out work/cache.json
```

- 自動檢測格式與編碼
- 每條字幕段包含：`text_index`, `start_ms`, `end_ms`, `source_text`, `_preserved`（格式保護資料）
- ASS 的繪圖指令 / Karaoke 標籤 / Comment 行全部存入 `_preserved` 不要翻譯
- `--context`：`military` / `medical` / `casual` / `auto`（多義詞消歧用）
- `--market`：`nordic`(14cps) / `western`(12cps) / `asia`(10cps)（控制 CPS 閾值）
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

支援三種模式。按規模選擇：

#### Mode A：Agent 手譯（單一檔案，最高品質）

```bash
# 4a. 讀取未譯批次
<PFX> -m batch read work/cache.json --size 50 --output work/batch.json

# 4b. Agent 翻譯
```
Agent 逐段閱讀 `work/batch.json`，參考 `references/subagent_prompt_template.md` 翻譯：
- 保留 `[N]` 標籤格式（這是批次索引，不是字幕換行）
- 原文中的 `\N` 是字幕換行，譯文中也保留 `\N`
- 對話 `- A\N- B` → 「A」「B」
- 人物名依 glossary，不在表內的**保留原文 + 記入 uncertain**
- 每一行**逐行對應**，不要合併或拆分

保存為 `work/translations_001.json`：
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

#### Mode B：Auto（快速，多集適用）

```bash
# 4a. Auto 翻譯音效+短語，不確定的標記留俾 agent
<PFX> -m batch read work/cache.json --size 1000 \
  --auto --glossary work/glossary.locked.json \
  --tm work/tm.json --tm-save work/tm.json \
  --uncertain work/uncertain.json \
  --context military --output work/auto_batch.json

# 4b. 寫回自動翻譯
<PFX> -m batch write work/cache.json work/auto_batch.json

# 4c. Agent 翻譯 uncertain 部分（參照 Mode A 流程）
<PFX> -m batch read work/cache.json --size 100 --output work/batch.json
# → agent 翻譯 → batch write → 循環
```

#### Mode C：Subagent 平行（10+ 集，最快）

主 agent 流程：
1. Auto 處理全部集數（--auto）
2. 對每集嘅 uncertain items spawn subagent
3. Subagent 收到 batch + glossary，獨立翻譯
4. 主 agent 收齊後 batch write + export

Subagent 用標準 prompt template：
```
references/subagent_prompt_template.md
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
  --output out/episode.zh-hk.srt \
  --format srt --region hk
```

- 格式保護：原始檔案作為範本，只取代文字欄位
- 可轉換格式（SRT→ASS、VTT→SRT 等）
- **Credit 標頭**：自動加入 header（ASS/VTT 等支援 comment 嘅格式有效）
- **SRT 格式不加入 credit header**（會破壞格式，Plex 等播放器會無法識別）
- `--no-credit` 可強制跳過
- 輸出檔名建議：`影片原名.{lang}.srt`（例如 `Episode.S01E01.1080p.zh-hk.srt`）

### 步驟 6：校驗

```bash
# 翻譯品質檢查（CJK-aware，根據原始時間碼計算 CPS）
<PFX> -m verify quality work/cache.json --market asia

# 術語合規檢查
<PFX> -m verify glossary work/cache.json work/glossary.locked.json

# 翻譯完整性檢查（檢查有冇漏譯，可 loop export 未譯段）
<PFX> -m verify completeness work/cache.json --output work/gaps.json

# 格式完整性檢查（比對原始檔與輸出檔）
<PFX> -m verify integrity --original episode.srt --output out/episode_translated.srt
```

**預設閾值（AVT 學術標準）：**

| 參數 | 預設值 | 說明 |
|------|--------|------|
| CPS | 12（western）/ 10（asia） | 跟 `--market` |
| CPL | 36 visual units | CJK 字符計 2 unit |
| Max lines | 2 | 標準字幕 |
| Min duration | 1.0s | 少於 1s 的 subtitle 太短 |
| Max duration | 6.0s | 多於 6s 觀眾會重讀 |

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

#### 輸出檔名慣例

輸出字幕應跟原始影片檔名，加上語言 tag：

```
原始 MKV:  The.Show.S01E01.1080p.WEB-DL.mkv
輸出 SRT:  The.Show.S01E01.1080p.WEB-DL.zh-hk.srt
```

提取後直接命名：
```bash
ffmpeg -i episode.mkv -map 0:s:0 "episode.srt"
# 之後 export 用返同一個 basename + .zh-hk.srt
```

# 從影片提取內嵌字幕

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

# 解析（含 context + market）
$PFX -m parse --input ep01.srt --source-lang en --target-lang zh \
  --region hk --context military --market asia --out work/cache.json

# 術語表
$PFX -m glossary discover --cache work/cache.json --source-lang en > work/candidates.json
$PFX -m glossary fetch --candidates work/candidates.json \
  --source-lang en --target-lang zh --region hk --out work/glossary.populated.json
# → agent webfetch _gaps
$PFX -m glossary lock --input work/glossary.populated.json --out work/glossary.locked.json

# 術語表更新（scan cache 新名 + Wikipedia merge）
$PFX -m glossary update --glossary work/glossary.locked.json \
  --cache work/cache.json --source-lang en --target-lang zh --region hk

# Mode A: Agent 手譯
$PFX -m batch read work/cache.json --size 50 --output work/batch.json
# → agent 翻譯（或先用 prompt_builder 生成 prompt）
$PFX -m prompt_builder --input work/batch.json --output prompt.txt \
  --context military --bilingual
$PFX -m batch write work/cache.json work/translations_001.json

# Mode B: Auto 翻譯
$PFX -m batch read work/cache.json --size 1000 --auto \
  --glossary work/glossary.locked.json --tm work/tm.json \
  --tm-save work/tm.json --uncertain work/uncertain.json \
  --context military --output work/auto_batch.json
$PFX -m batch write work/cache.json work/auto_batch.json

# 匯出（單語／雙語／credit）
$PFX -m export --cache work/cache.json --output out/ep01.zh-hk.srt \
  --format srt --region hk
$PFX -m export --cache work/cache.json --output out/ep01.bilingual.srt \
  --bilingual --bilingual-order source-first

# 校驗
$PFX -m verify quality work/cache.json --market asia
$PFX -m verify glossary work/cache.json work/glossary.locked.json
$PFX -m verify completeness work/cache.json --output work/gaps.json
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

---

## ⚠️ Batch Mode 實戰教訓（v1.1 大規模批量化經驗）

### 致命錯誤：auto → export 唔係完整翻譯

2026年7月批次處理 438 部電影時，直接 auto_translate → export 就當完成。結果：
```
Total segments: 506,066
Auto-translated: 57,216 (11.3%)
Untranslated: 448,850 (88.7%)
```
檔案名 `movie.zh-hk.srt` 令人誤以為係完整粵語字幕，但實際 88% 內容係英文。

**教訓：** auto_translate 係 pre-processor，唔係 translator。永遠行 `verify completeness` check 100% 先當完成。

### Method A: Mixed Batch（跨電影混合批次）

```
[電影1 seg 1]    ← 唔知情節
[電影5 seg 10]   ← 唔知情節
[電影2 seg 3]    ← 零上下文
```

| 維度 | 結果 |
|------|------|
| 速度 | 快：一次 5000 seg |
| Quality | ❌ 差：角色名混亂、風格唔一致 |
| 語境 | ❌ 零：subagent 唔知係咩戲 |
| 適合 | 不建議 |

### Method B: Per-Movie（逐部電影獨立處理）

```
[電影1] → subagent 知道全套片嘅情節同角色
[電影2] → subagent 知道全套片嘅情節同角色
```

| 維度 | 結果 |
|------|------|
| 速度 | 慢：每部電影獨立 dispatch |
| Quality | ✅ 好：角色名一致、風格統一 |
| 語境 | ✅ 完整：subagent 睇到全套對白 |
| 適合 | 正式翻譯 |

### 速度 vs Quality 取捨

| 方法 | 438 電影需時 | Quality |
|------|-------------|---------|
| Mixed batch 5000 | ~4 小時 | 4/10 |
| Per-movie full | ~40 小時 | 8/10 |
| Auto → subagent per-movie | ~20 小時 | 7/10 |

**結論：** Mixed batch 唔值得慳時間。逐部電影獨立 dispatch，每部電影的 subagent prompt 包含該電影嘅 context（片名、類型）。

### `<i>` 標籤內容翻譯陷阱

SIX E01-E02 大量 radio comms 用 `<i>` 包住，subagent 以為要「保留」而唔譯：

```
Before: <i>Bravo-Zero-One, this is K-Bar.</i>
After:  <i>Bravo-Zero-One, this is K-Bar.</i>  ← ❌ 冇譯！
```

**點解發生：** 早期 prompt 寫「`<i>` tags preserved」，subagent 理解為成個 tag 連內容保留。

**解決方案：** prompt 要寫明「內容 inside `<i>` 都要譯」，唔係保留成個 tag。

```diff
- <i> tags preserved
+ <i> tags: preserve the tags, TRANSLATE the content inside
+ <i>He ran</i> → <i>佢跑咗</i>
```

**驗證方法（完稿後）：**
```bash
python -c "
import json
c = json.load(open('cache.json'))
for s in c['segments']:
    src = s.get('source_text','').strip()
    tgt = s.get('translated_text','').strip()
    if src and tgt and src.lower() == tgt.lower():
        print(f'RESIDUE #{s[\"text_index\"]}: {src[:60]}')
"
```

搵到 source=target 嘅 segment 就係未譯嘅。

### SRT 廣告/浮水印殘留

OpenSubtitles download 嘅 .srt 成日有廣告尾：
```
Watch any video online with Open-SUBTITLES
Free Browser extension: osdb.link/ex
```

呢啲要 auto-remove 或者 translate 做（廣告）。

### Batch Translate 正確流程

```bash
# 1. Auto-translate（處理音效+短句）
<PFX> -m batch read cache.json --size 3000 --auto --uncertain uncertain.json ...

# 2. Per-movie subagent（逐部翻譯）
# 每部電影獨立 dispatch，唔好 mixed batch

# 3. Write back
<PFX> -m batch write cache.json auto_batch.json
<PFX> -m batch write cache.json subagent_results.json

# 4. Verify 100%
<PFX> -m verify completeness cache.json
# → {"completeness_pct": 100.0}  ✅ 先可以 export

# 5. Export
<PFX> -m export --cache cache.json -o movie.zh-hk.srt
```

### 大規模批量化 Checklist

- [ ] auto_translate 後 coverage < 20%? → 一定要行 subagent
- [ ] Mixed batch 定 per-movie? → **一定 per-movie**
- [ ] subagent prompt 有冇寫明「`<i>` tag 內容都要譯」? → **必須**
- [ ] `verify completeness` 係咪 100%? → **否則唔 export**
- [ ] Source=Target 嘅 residue check 咗未? → **必須做**

---

## 批量實戰總結（2026.07 大規模教訓）

### 數據回顧

| 指標 | 值 |
|------|-----|
| 處理電影 | 438 部 |
| 處理劇集 | ~100+ 集 |
| 總 segments | 506,066 |
| 總翻譯 segments | ~105,000+ |
| 使用 subagent 次數 | 250+ |
| 寫回批次 | 180+ |

### 核心教訓

#### 1. `<i>` tag 內容必須翻譯，唔係保留

**錯誤：** prompt 寫「`<i>` tags preserved」→ subagent 保留整個 tag 連英文內容
**正確：** prompt 要寫「`<i>` tags: preserve the tags, TRANSLATE the content inside」

SIX E01-E02 因為呢個問題有 489 段 `<i>Bravo-Zero-One</i>` 未譯。

#### 2. `\N` 係換行標記，內容都要譯

`\N` 係字幕換行（ASS/SSA 格式內的 line break），subagent 要保留 `\N` 但翻譯前後文字。

#### 3. Auto → Export 係陷阱

auto_translate 只 cover ~11%，export 前一定要行 `verify completeness`。

#### 4. Mixed batch 做得，但 quality 差

跨電影混合批次快但亂。Per-movie subagent 慢但穩。

#### 5. Subagent output 經常有 BOM

部分 subagent 輸出 UTF-8 BOM，batch write 會炒。解決方案：
```python
with open(path, encoding='utf-8-sig') as f:
    data = json.load(f)
# 再以 utf-8 重新儲存
```

#### 6. ASS drawing commands 要跳過

`{\p1}...{\p0}` 係繪圖指令，唔係文字，subagent 唔應該改。Prompt 要寫明：
```
ASS drawing commands (\\p1...\\p0) → keep as-is
```

#### 7. Residue check 必須做

完稿後要搵 source_text == translated_text 嘅 segment：
```python
for s in cache['segments']:
    if s.get('source_text','').strip() == s.get('translated_text','').strip():
        print(f'RESIDUE #{s["text_index"]}: {s["source_text"][:60]}')
```

#### 8. Subagent prompt 一致性至關重要

| Prompt 寫法 | 結果 |
|-------------|------|
| `ALL. HK Cantonese.` | 最快但最易出事 |
| Full rules with `<i>`/`\N`/ASS instructions | 穩定但長 |
| `prompt_builder.py` 生成 | 標準化、可重複 |

**推薦：** 用 `prompt_builder.py` + 手動補 `<i>`/`\N` clarify。

### 大規模批量化建議流程

```
1. 掃描全部電影（確認缺少字幕清單）
2. 建立共享 TM（Translation Memory）
3. Auto-translate 全部（快速處理音效+短句）
4. Per-movie subagent dispatch（逐部獨立翻譯）
   └─ 每次 3-4 部 parallel
   └─ prompt 包含：<i>翻譯、\N保留、ASS跳過
5. Write back → prep next → 循環
6. Completeness check（100% 先行 export）
7. Source=Target residue check（補漏）
8. Export .zh-hk.srt（跟原始影片檔名）
```

**驗證命令：**
```bash
# 完整性
<PFX> -m verify completeness cache.json | grep completeness_pct

# Residue
python -c "
import json
c = json.load(open('cache.json'))
bad = [s for s in c['segments'] if s.get('source_text','').strip() == s.get('translated_text','').strip() and s.get('source_text','').strip()]
print(f'{len(bad)} residue segments')
for s in bad[:3]:
    print(f'  #{s[\"text_index\"]}: {s[\"source_text\"][:60]}')
"

# 最終覆蓋率
python -c "
import json
c = json.load(open('cache.json'))
segs = c['segments']
done = sum(1 for s in segs if s.get('translated_text','').strip() and s.get('translated_text','').strip() != s.get('source_text','').strip())
print(f'{done}/{len(segs)} ({done*100//len(segs)}%)')
"
```


**Q: 翻譯後時間碼變了？**
A: 不應該。時間碼存在 `_preserved.raw_timing`，匯出時原樣寫回。若變了請回報 bug。

**Q: ASS 的繪圖被翻譯了？**
A: `{\p1}...{\p0}` 之間的文字受 `non_translate_patterns` 保護。不會被翻譯。

**Q: 翻譯內容超出字幕顯示時間？**
A: 用 `verify quality --market asia` 檢查。CPS 過高的段需要縮短譯文。成人標準 ~12cps，兒童 ~9cps。

**Q: 如何處理 Netflix 的 TTML/DFXP？**
A: 當前不支援。如有需要可增加 TtmlProtector，用 `lxml` 處理。

**Q: 有些角色名沒在 Wikipedia 查到？**
A: 這些在 `_gaps` 陣列裡，agent 會用 webfetch 查官方站點補齊。

**Q: 匯出後中文字變亂碼？**
A: 原始檔案可能是 Shift-JIS 編碼。用 `--encoding utf-8` 指定匯出編碼，或先用 detect 檢查。

**Q: 雙語字幕點樣 output？**
A: 用 `--bilingual`，source + translation 逐段分行。音效/音樂類單行。

**Q: 翻譯中途發現新角色名點算？**
A: 用 `glossary update` scan cache 自動 detect 新名，Wikipedia fetch 補譯，merge 入現有 glossary。

**Q: Subagent prompt 每次唔一致？**
A: 用 `prompt_builder.py` 生成標準 prompt，支援 context/market/bilingual flag。

**Q: Batch write 話 subagent output 有事？**
A: 自動 fix：如果 `translated_text` 係 list 會 auto-join 做 string，唔會炒 export。

**Q: 字幕 credit 係咩？**
A: SRT 檔尾自動加：
```
# ─────────────────────────────────────
# AI-translated by subbridge
# https://github.com/hug0-l/subBridge-skill
# ─────────────────────────────────────
# Language: en → zh-hk
```
`--no-credit` 可跳過。
