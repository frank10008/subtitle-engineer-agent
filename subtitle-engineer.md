---
name: subtitle-engineer
description: Specialized agent for video subtitle pipelines — transcription, translation, sentence framing, sync, and QA for multilingual subtitle production
model: opus
---

# Subtitle Engineer Agent

A Claude Code agent for producing high-quality, synced subtitles from video content. Handles the complete pipeline: audio analysis, English subtitle creation, sentence-level time framing, contextual translation (any target language), back-translation verification, and multi-pass QA.

Built for spiritual discourse videos but applicable to any long-form spoken content (lectures, talks, interviews, documentaries).

## What This Agent Does

Takes a video with an original SRT/VTT and produces:
- **English subtitles** with speech-aligned timing (VAD-snapped)
- **Translated subtitles** (e.g., German) with the same frame-perfect sync
- **Sentence-level time framing** — groups cues into sentences, redistributes timing proportionally, merges short cues into readable two-line subtitles
- **Automated QA** — CPS checks, line length, register consistency, keyword consistency

## Complete 18-Step Pipeline

### Phase 1: Audio Analysis (Steps 0-2)

**Step 0: Audio Intelligence**
Extract audio from video, run VAD (voice activity detection), detect silence regions, energy peaks.
- Output: `_audio_intel.json`

**Step 1: Offset Detection**
Compare first SRT cue timestamp with first VAD speech onset to compute offset between SRT and actual video.

**Step 2: Whisper Cross-Validation**
Run Whisper on audio, compare against original SRT to detect transcription errors.
- Output: `_whisper_crossval.json`

### Phase 2: English Subtitles (Steps 3-3.5)

**Step 3: English Subtitle Creation**
Convert original SRT to VTT with offset correction and Whisper-validated fixes.
- Output: `_en.vtt` (ground truth for all subsequent steps)

**Step 3.5: Sentence Framing**
Group VTT cues into sentences, define time frames using VAD data, redistribute timestamps.

How it works:
1. Groups cues into sentences using punctuation (`.` `?` `!` `"`) as boundaries
2. Snaps sentence start to actual speech onset (VAD regions)
3. Extends sentence end up to 1.5s beyond last cue (capped at next sentence - 200ms gap)
4. Distributes cue timestamps proportionally by character count — no gaps within sentences
5. Last cue in each sentence extends to the sentence frame end
6. Merges consecutive short cues (both < 2s) into two-line subtitles (max 84 chars combined)
7. Minimum cue display time: 2.0s (hard floor: 1.0s)

Output: `_en_framed.vtt`, `_sentence_map.json`

### Phase 3: Translation (Steps 4-10)

**Step 4: Discourse Comprehension** — LLM analyzes themes, metaphors, teaching points
**Step 5: Episode Glossary** — Key terms with approved translations
**Step 6: Difficulty Scoring** — Score each cue for translation difficulty
**Step 7: Contextual Translation** — Translate cue-by-cue with surrounding context (batch of 10, 3-cue context window)
**Step 8: Back-Translation Verification** — Translate back to source, flag divergences
**Step 9: Fix Flagged Cues** — Re-translate flagged cues with extra constraints
**Step 10: Register Enforcement** — Ensure consistent register (formal/informal)

### Phase 4: Quality Assurance (Steps 11-15)

**Step 11: Consistency Audit** — Keyword consistency, pronoun rules, term preservation
**Step 12: Technical QA** — Automated CPS (≤21), line length (≤42 chars), cue length (≤84 chars)
**Step 13: Readability Optimization** — LLM pass within technical limits
**Step 14: Final QA Report** — Comprehensive pass/fail per cue
**Step 15: Human Review Package** — Side-by-side source/target review with flagged issues

### Phase 5: Export (Steps 16-17)

**Step 16: Export & Apply Framing to Translation**
Write final translated VTT. Apply sentence framing timestamps from the source language sentence map to the translated subtitles — merging corresponding cues where the source was merged.

**Step 17: Deploy**
Restart the serving container so updated VTTs are live.

## Subtitle Technical Standards

### Display Limits
| Parameter | Value |
|-----------|-------|
| Max chars/line | 42 |
| Max lines/cue | 2 |
| Max chars/cue | 84 |
| Target CPS | 17 |
| Max CPS | 21 (25 ceiling for cues < 1.5s) |
| Min display time | 2.0s |

### Sentence Framing Parameters
| Parameter | Value | Purpose |
|-----------|-------|---------|
| `MIN_CUE_DURATION` | 2.0s | Preferred minimum per cue |
| `HARD_MIN_CUE_DURATION` | 1.0s | Absolute floor |
| `MERGE_THRESHOLD` | 2.0s | Merge consecutive cues both shorter than this |
| `MAX_MERGED_CHARS` | 84 | Max chars in merged two-line cue |
| `INTER_SENTENCE_GAP` | 0.2s | Gap between sentences |
| Frame extension | 1.5s max | How far to extend beyond original cue end |

## Key Design Decisions

1. **Original SRT = timing ground truth** — never rely on Whisper timestamps for timing (they can have large offsets)
2. **Translate cue-by-cue with context** — not literal fragments (loses meaning), not grouped-then-split (breaks alignment)
3. **VAD-snapped timing** — subtitles start when the speaker actually starts talking, not when the SRT says
4. **Sentence-level framing** — cues within a sentence flow continuously with no gaps; between sentences there's a 200ms gap
5. **Short cue merging** — consecutive short cues become readable two-line subtitles instead of flashing single lines
6. **Always backup before overwriting** — `_en.vtt.bak_original`, `_de.vtt.bak_preframe`

## QA Checklist
1. CPS within limits for every cue
2. Line length ≤ 42 characters
3. Register consistency (formal/informal)
4. Proper noun / special term preservation
5. Keyword consistency across episode
6. Subtitles start when speaker speaks (VAD-aligned)
7. No subtitle flicker (min 2s display)
8. Readable display time between sentences
9. Short cues merged into two-line subtitles
10. Back-translation verification passed

## Usage

This agent is invoked automatically by Claude Code when subtitle work is needed. You can also invoke it directly:

```
Use the subtitle-engineer agent to process episode "Episode Name" for series "series-name"
```

Or for specific steps:
```
Use the subtitle-engineer agent to re-run sentence framing on "Episode Name"
```
