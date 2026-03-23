# Subtitle Engineer — Claude Code Agent

A specialized [Claude Code](https://claude.com/claude-code) agent for producing broadcast-quality, frame-synced subtitles from video content in any language. Genre-agnostic — works for lectures, interviews, documentaries, podcasts, or any spoken content.

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

## Standalone Script

The agent also ships as a standalone Python script (`subtitle_agent.py`) that can run independently of Claude Code:

```bash
# Process a single episode
python3 subtitle_agent.py "Episode Name" --series my-series --config project.json

# Process all episodes in a series (idempotent — skips already done)
python3 subtitle_agent.py --series my-series --all --config project.json

# QA check on existing subtitles
python3 subtitle_agent.py --check "Episode Name" --series my-series
```

## Configuration

All project-specific settings live in `project.json` — the agent itself is genre-agnostic. Copy `project.example.json` and customize:

```json
{
  "project_dir": "/path/to/your/project",
  "source_lang": "en",
  "target_lang": "de",
  "llm": {
    "api_url": "https://your-api-endpoint/v1/chat/completions",
    "api_key": "YOUR_API_KEY",
    "model": "your-model"
  },
  "series": {
    "my-series": {
      "prefix": "EP",
      "srt_dir": "subtitles",
      "srt_pattern": "Episode_{num:02d}.srt"
    }
  },
  "subtitle_rules": [
    "Max 21 CPS (characters per second)",
    "Max 42 characters per line",
    "Translate meaning, not word-for-word"
  ],
  "formality": "informal",
  "formality_checks": { "wrong": "Sie", "except": "siehe" },
  "preserve_terms": ["proper", "nouns", "to", "keep"],
  "known_fixes": { "wrong phrase": "correct phrase" },
  "max_cps": 21,
  "max_line_chars": 42
}
```

### Config Reference

| Key | Description | Default |
|-----|-------------|---------|
| `project_dir` | Root directory of the video project | Script directory |
| `source_lang` | Source language code (e.g., `en`) | `en` |
| `target_lang` | Target language code (e.g., `de`, `fr`, `es`) | `de` |
| `llm` | API endpoint, key, and model for translation | — |
| `series` | Map of series names to SRT directory/pattern info | `{}` |
| `episode_contexts` | Per-episode context strings for better translation | `{}` |
| `subtitle_rules` | List of rules included in translation prompts | Basic CPS/line rules |
| `formality` | `formal` or `informal` | `informal` |
| `formality_checks` | Pattern to detect wrong formality register | `{}` |
| `preserve_terms` | Words/phrases to keep untranslated | `[]` |
| `known_fixes` | Source text corrections (wrong → right) | `{}` |
| `difficulty_keywords` | Word lists for difficulty scoring | Metaphors + complex terms |
| `max_cps` | Characters per second limit | `21` |
| `max_line_chars` | Max characters per subtitle line | `42` |

### Graceful Fallbacks

The pipeline handles missing inputs without crashing:

| Available | Behavior |
|-----------|----------|
| SRT + Video | Full pipeline (audio analysis + translation) |
| SRT only | Skip audio analysis, proceed with translation |
| Source VTT only (no SRT) | Skip to translation using existing VTT |
| Transcription JSON only | Generate VTT from transcription, then translate |
| Nothing | Error with clear message |

## Pipeline Overview

| Phase | Steps | What Happens |
|-------|-------|-------------|
| **Audio Analysis** | 0-2 | Extract audio, detect speech regions (VAD), cross-validate with Whisper |
| **Source Subtitles** | 3-3.5 | Create VTT from SRT, apply sentence framing with speech-aligned timing |
| **Translation** | 4-10 | Discourse analysis, glossary, contextual translation, back-translation QA |
| **Quality Assurance** | 11-15 | Consistency audit, technical QA, readability optimization, review package |
| **Export** | 16-17 | Apply framing to translation, deploy |

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

- Educational content (university lectures, tutorials, courses)
- Documentary narration
- Interviews and panel discussions
- Podcasts and talk shows
- Conference talks and keynotes
- Any long-form spoken content needing multilingual subtitles

## Requirements

- [Claude Code](https://claude.com/claude-code) CLI (for agent mode)
- Python 3.8+ (for standalone script)
- FFmpeg (for audio extraction)
- Whisper (for cross-validation, optional)
- Translation API access (configurable — Qwen, OpenAI, DeepSeek, etc.)

## License

MIT
