# Subtitle Engineer — Claude Code Agent

A specialized [Claude Code](https://claude.com/claude-code) agent for producing broadcast-quality, frame-synced subtitles from video content in any language.

## What It Does

Drop in a video with an SRT and get perfectly timed, translated subtitles — automatically.

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐    ┌──────────────┐
│  Video +    │ →  │  Audio       │ →  │  Sentence       │ →  │  Contextual  │
│  Original   │    │  Intelligence│    │  Framing &      │    │  Translation │
│  SRT        │    │  (VAD)       │    │  Speech Sync    │    │  + QA        │
└─────────────┘    └──────────────┘    └─────────────────┘    └──────────────┘
```

### The Problem

Raw subtitle files have timing issues:
- Subtitles appear **before the speaker starts talking**
- Short phrases **flash by too fast** to read
- Translated subtitles lose sync because languages have different lengths
- No sentence-level coherence — cues are fragmented

### The Solution

An 18-step pipeline that produces subtitles which:
- **Start exactly when the speaker talks** (VAD speech-onset snapping)
- **Stay on screen long enough to read** (2s minimum, proportional distribution)
- **Flow as sentences** — no gaps within a sentence, small gap between sentences
- **Merge short cues** into readable two-line subtitles
- **Translate with context** — each cue translated with surrounding cues for coherence
- **Verify quality** — back-translation, CPS checks, line limits, register consistency

## Installation

```bash
# Copy the agent to your Claude Code agents directory
cp subtitle-engineer.md ~/.claude/agents/

# Or for project-local installation
mkdir -p .claude/agents/
cp subtitle-engineer.md .claude/agents/
```

Then in Claude Code:
```
Use the subtitle-engineer agent to process my video subtitles
```

## Pipeline Overview

| Phase | Steps | What Happens |
|-------|-------|-------------|
| **Audio Analysis** | 0–2 | Extract audio, detect speech regions (VAD), cross-validate with Whisper |
| **English Subtitles** | 3–3.5 | Create VTT from SRT, apply sentence framing with speech-aligned timing |
| **Translation** | 4–10 | Discourse analysis, glossary, contextual translation, back-translation QA |
| **Quality Assurance** | 11–15 | Consistency audit, technical QA, readability optimization, review package |
| **Export** | 16–17 | Apply framing to translation, deploy |

## Sentence Framing — The Key Innovation

Most subtitle tools handle timing at the cue level. This agent works at the **sentence level**:

1. **Groups cues into sentences** using punctuation boundaries
2. **Defines a time frame** per sentence anchored to actual speech (VAD)
3. **Distributes cues proportionally** by character count within each frame
4. **Merges short cues** (< 2s each) into two-line subtitles for readability
5. **Extends display time** — last cue stays visible until 200ms before next sentence

### Before vs After

```
BEFORE (raw SRT timing):
  [0] 01:04.000 → 01:05.579  "Is there a love"          ← 3s before speaker talks!
  [1] 01:06.196 → 01:10.484  "in which respect..."
  [2] 01:12.206 → 01:13.644  "intimate love."            ← 1.4s flash

AFTER (sentence framing):
  [0] 01:08.418 → 01:09.926  "Is there a love"           ← starts with speech
  [1] 01:09.926 → 01:11.984  "in which respect..."       ← no gap, flows
  [2] 01:12.206 → 01:13.644  "intimate love."            ← proper display time
```

## Technical Standards

| Parameter | Value |
|-----------|-------|
| Max characters per line | 42 |
| Max lines per cue | 2 |
| Target reading speed | 17 CPS |
| Maximum reading speed | 21 CPS |
| Minimum display time | 2.0 seconds |
| Inter-sentence gap | 200ms |

## Built For

- Spiritual discourse videos (lectures, talks, satsangs)
- Educational content (university lectures, tutorials)
- Documentary narration
- Any long-form spoken content needing multilingual subtitles

## Requirements

- [Claude Code](https://claude.com/claude-code) CLI
- FFmpeg (for audio extraction)
- Whisper (for cross-validation)
- Translation API access (configurable — Qwen, OpenAI, etc.)

## License

MIT
