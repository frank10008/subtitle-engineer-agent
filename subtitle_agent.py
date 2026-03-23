#!/usr/bin/env python3
"""
Subtitle Agent
==============
Automated subtitle translation pipeline for video series.
Translates source-language subtitles to a target language with contextual
awareness, back-translation verification, and quality assurance.

All project-specific configuration (series, terminology, style rules, API
settings) is loaded from project.json in the project root.

Usage:
  python3 subtitle_agent.py "Episode Name"
  python3 subtitle_agent.py --series my-series --all
  python3 subtitle_agent.py --check "Episode Name"
  python3 subtitle_agent.py --config project.json "Episode Name"

Pipeline Steps:
  0. Audio Intelligence (Silero VAD + energy analysis)
  1. Source Subtitle Validation (cross-check SRT)
  2. Source QA (apply known fixes)
  3. Difficulty Scoring (L1-L4 per cue)
  4. Contextual Translation (source → target language)
  5. Back-Translation Verification (target → source)
  6. Fix Flagged Cues (re-translate <70 score)
  7. Polish (formality, CPS, line length)
  8. Final QA Report
"""
import json, re, os, sys, time, subprocess, argparse
from pathlib import Path

# ─── CONFIG (loaded from project.json) ───────────────────────────────────────

def load_config(config_path):
    """Load project configuration. Returns defaults if file not found."""
    defaults = {
        "project_dir": str(Path(__file__).parent),
        "source_lang": "en",
        "target_lang": "de",
        "llm": {
            "api_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
            "api_key": "",
            "model": "qwen-plus",
        },
        "series": {},
        "episode_contexts": {},
        "subtitle_rules": [
            "Max 21 CPS (characters per second)",
            "Max 42 characters per line",
            "Translate meaning, not word-for-word",
        ],
        "formality": "informal",
        "formality_checks": {},
        "preserve_terms": [],
        "known_fixes": {},
        "difficulty_keywords": {
            "metaphors": ["like", "as if", "imagine", "suppose", "just as", "the way"],
            "complex": ["consciousness", "infinite", "transcend", "eternal", "absolute", "essence"],
        },
        "max_cps": 21,
        "max_line_chars": 42,
    }
    if os.path.exists(config_path):
        with open(config_path) as f:
            user_cfg = json.load(f)
        # Deep merge
        for k, v in user_cfg.items():
            if isinstance(v, dict) and isinstance(defaults.get(k), dict):
                defaults[k].update(v)
            else:
                defaults[k] = v
    else:
        print(f"  WARNING: Config not found: {config_path}, using defaults")
    return defaults

CFG = {}  # populated in main()

# ─── UTILITIES ───────────────────────────────────────────────────────────────
def sec_to_vtt(s):
    h = int(s // 3600); m = int((s % 3600) // 60); sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"

def parse_srt(path):
    with open(path) as f: content = f.read()
    blocks = re.split(r'\n\n+', content.strip())
    cues = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3: continue
        m = re.match(r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})', lines[1])
        if not m: continue
        def to_sec(t):
            t = t.replace(',', '.'); h,mi,s = t.split(':')
            return float(h)*3600 + float(mi)*60 + float(s)
        cues.append({"start": to_sec(m.group(1)), "end": to_sec(m.group(2)),
                      "text": ' '.join(lines[2:]).strip()})
    return cues

def parse_vtt(path):
    with open(path) as f: content = f.read()
    blocks = [b for b in re.split(r"\n\n+", content.strip()) if "-->" in b]
    cues = []
    for b in blocks:
        lines = b.strip().split("\n")
        m = re.match(r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})", lines[0])
        if m:
            def to_s(t):
                h,mi,s = t.split(':')
                return int(h)*3600+int(mi)*60+float(s)
            cues.append({"start": to_s(m.group(1)), "end": to_s(m.group(2)),
                         "text": "\n".join(lines[1:])})
    return cues

def write_vtt(path, cues, text_key="text"):
    lines = ["WEBVTT\n"]
    for c in cues:
        lines.append(f"{sec_to_vtt(c['start'])} --> {sec_to_vtt(c['end'])}")
        lines.append(c[text_key])
        lines.append("")
    with open(path, "w") as f: f.write("\n".join(lines))

def llm(prompt, temperature=0.3):
    import requests
    api = CFG["llm"]
    resp = requests.post(
        api["api_url"],
        headers={"Authorization": f"Bearer {api['api_key']}", "Content-Type": "application/json"},
        json={"model": api["model"],
              "messages": [{"role": "user", "content": prompt}],
              "temperature": temperature},
        timeout=90,
    )
    if resp.status_code == 429:
        print("      Rate limited, waiting 10s...")
        time.sleep(10)
        return llm(prompt, temperature)
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip().strip('"\'')

def auto_linebreak(text, max_line=None):
    max_line = max_line or CFG.get("max_line_chars", 42)
    clean = text.replace("\n", " ")
    if len(clean) <= max_line:
        return clean
    words = clean.split()
    l1, l2 = [], []
    cl = 0
    for w in words:
        if cl + len(w) + 1 <= max_line:
            l1.append(w); cl += len(w) + 1
        else:
            l2.append(w)
    if l1 and l2:
        return " ".join(l1) + "\n" + " ".join(l2)
    return clean

def build_rules_prompt():
    """Build subtitle rules string from config."""
    return "\n".join(f"- {r}" for r in CFG.get("subtitle_rules", []))

def build_preserve_prompt():
    """Build preserve-terms instruction if any."""
    terms = CFG.get("preserve_terms", [])
    if not terms:
        return ""
    return f"\nKeep these terms untranslated: {', '.join(terms)}"

# ─── PIPELINE STEPS ──────────────────────────────────────────────────────────

def step0_audio_intel(video_path, episode):
    """Silero VAD + energy envelope analysis"""
    print(f"\n{'='*70}\nSTEP 0: AUDIO INTELLIGENCE\n{'='*70}")

    wav = f"/tmp/{episode.replace(' ', '_')}_audio.wav"

    if not os.path.exists(wav):
        print("  Extracting audio...")
        subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-ar", "16000", "-ac", "1", "-f", "wav", wav],
                       capture_output=True)

    import torch
    import numpy as np
    torch.set_num_threads(1)
    model, utils = torch.hub.load('snakers4/silero-vad', 'silero_vad', force_reload=False, trust_repo=True)
    (get_speech_timestamps, _, read_audio, _, _) = utils
    audio = read_audio(wav, sampling_rate=16000)
    speech_ts = get_speech_timestamps(audio, model, threshold=0.3, min_speech_duration_ms=250,
                                       min_silence_duration_ms=300, sampling_rate=16000, return_seconds=True)

    audio_np = audio.numpy()
    total = len(audio_np) / 16000
    speech = sum(s["end"] - s["start"] for s in speech_ts)

    result = {"total_duration_s": total, "speech_duration_s": speech,
              "speech_pct": round(speech/total*100, 1), "speech_regions": speech_ts}
    print(f"  {total:.0f}s total, {speech:.0f}s speech ({result['speech_pct']}%), {len(speech_ts)} regions")
    return result

def step1_source_validation(srt_path):
    """Validate source subtitles and apply known fixes"""
    print(f"\n{'='*70}\nSTEP 1: SOURCE SUBTITLE VALIDATION\n{'='*70}")

    cues = parse_srt(str(srt_path))
    print(f"  SRT: {len(cues)} cues")

    known_fixes = CFG.get("known_fixes", {})

    applied = []
    for cue in cues:
        text = cue["text"]
        for wrong, right in known_fixes.items():
            if wrong.lower() in text.lower():
                text = re.sub(re.escape(wrong), right, text, flags=re.IGNORECASE)
                applied.append(f"'{wrong}' -> '{right}'")
        cue["text_fixed"] = text

    if applied:
        print(f"  Applied {len(applied)} fixes: {', '.join(applied[:5])}")
    else:
        print("  No known fixes needed")

    return cues

def step2_source_qa(cues, audio_intel):
    """QA pass on source subtitles"""
    print(f"\n{'='*70}\nSTEP 2: SOURCE QA\n{'='*70}")
    no_speech = sum(1 for c in cues
                    if not any(s["end"] > c["start"] and s["start"] < c["end"]
                              for s in audio_intel.get("speech_regions", [])))
    print(f"  {len(cues)} cues, {no_speech} over silence")
    return cues

def step3_difficulty(cues):
    """Score each cue L1-L4 for translation difficulty"""
    print(f"\n{'='*70}\nSTEP 3: DIFFICULTY SCORING\n{'='*70}")

    metaphors = CFG.get("difficulty_keywords", {}).get("metaphors", [])
    complex_terms = CFG.get("difficulty_keywords", {}).get("complex", [])
    preserve = [t.lower() for t in CFG.get("preserve_terms", [])]
    max_cps = CFG.get("max_cps", 21)

    results = []
    counts = {1:0, 2:0, 3:0, 4:0}
    for i, cue in enumerate(cues):
        text = cue.get("text_fixed", cue["text"])
        tl = text.lower()
        dur = cue["end"] - cue["start"]
        score = 0

        term_hits = sum(1 for t in preserve if t in tl)
        if term_hits >= 3: score += 3
        elif term_hits >= 1: score += 1

        if any(m in tl for m in metaphors): score += 2

        cps = len(text)/dur if dur > 0 else 0
        if cps > max_cps: score += 2
        elif cps > max_cps * 0.75: score += 1

        if len(text.split()) > 20: score += 1

        complex_hits = sum(1 for p in complex_terms if p in tl)
        if complex_hits >= 2: score += 2
        elif complex_hits: score += 1

        if "?" in text: score += 1

        level = 4 if score >= 6 else 3 if score >= 4 else 2 if score >= 2 else 1
        counts[level] += 1
        results.append({"level": level, "max_target_chars": int(dur * max_cps), "duration": dur})

    print(f"  L1={counts[1]} L2={counts[2]} L3={counts[3]} L4={counts[4]}")
    return results

def step4_translate(cues, difficulty, episode):
    """Context-aware translation to target language"""
    src = CFG.get("source_lang", "en")
    tgt = CFG.get("target_lang", "de")
    print(f"\n{'='*70}\nSTEP 4: CONTEXTUAL TRANSLATION ({src} -> {tgt})\n{'='*70}")

    episode_ctx = CFG.get("episode_contexts", {}).get(episode, "")
    rules = build_rules_prompt()
    preserve = build_preserve_prompt()

    target_cues = []

    for i, cue in enumerate(cues):
        text = cue.get("text_fixed", cue["text"])
        diff = difficulty[i]
        dur = diff["duration"]
        max_ch = diff["max_target_chars"]

        ctx = []
        for j in range(max(0,i-2), i):
            ctx.append(f"[prev] {cues[j].get('text_fixed', cues[j]['text'])}")
        for j in range(i+1, min(len(cues), i+2)):
            ctx.append(f"[next] {cues[j].get('text_fixed', cues[j]['text'])}")

        prompt = f"""Translate this subtitle to {tgt}.
{f"Context: {episode_ctx}" if episode_ctx else ""}
Rules:
{rules}{preserve}
MAX {max_ch} chars ({dur:.1f}s).
Surrounding cues: {'; '.join(ctx) if ctx else '(start/end)'}
Translate ONLY: "{text}"
Reply ONLY with the translation:"""

        try:
            translated = llm(prompt)
            translated = auto_linebreak(re.sub(r'["\u201e\u201c\u201d]', '', translated))
        except Exception as e:
            translated = f"[ERROR: {e}]"

        target_cues.append({"start": cue["start"], "end": cue["end"],
                            "text": translated, "src_text": text})

        if (i+1) % 25 == 0 or i == len(cues)-1:
            print(f"  {i+1}/{len(cues)}")

    return target_cues

def step5_verify(target_cues):
    """Back-translation verification"""
    src = CFG.get("source_lang", "en")
    tgt = CFG.get("target_lang", "de")
    max_cps = CFG.get("max_cps", 21)
    preserve = [t.lower() for t in CFG.get("preserve_terms", [])]
    formality_checks = CFG.get("formality_checks", {})

    print(f"\n{'='*70}\nSTEP 5: BACK-TRANSLATION VERIFICATION\n{'='*70}")

    results = []
    for i, dc in enumerate(target_cues):
        try:
            back = llm(f'Translate to {src}. Reply ONLY {src}:\n"{dc["text"]}"', temperature=0.1)
        except:
            back = "[ERROR]"

        score = 100; issues = []
        src_l = dc["src_text"].lower(); back_l = back.lower()

        skip_words = {"the","is","for","but","and","or","this","that","when","they","are","was",
                      "has","had","will","can","may","its","you","your","how","him","his","her",
                      "not","one","all","yet","get","see","very","like","just","such","here",
                      "more","some","what","with","even","from","only","also"}

        for noun in set(re.findall(r'\b[A-Z][a-z]{2,}\b', dc["src_text"])):
            if noun.lower() not in back_l and noun.lower() not in skip_words:
                score -= 8; issues.append(f"Lost:{noun}")

        src_neg = any(w in src_l for w in ["not","no ","never","don't","doesn't","won't","can't"])
        back_neg = any(w in back_l for w in ["not","no ","never","don't","doesn't","won't","can't"])
        if src_neg != back_neg: score -= 25; issues.append("NEGATION!")

        for t in preserve[:10]:
            if t in src_l and t not in back_l: score -= 12; issues.append(f"Term:{t}")

        dur = dc["end"] - dc["start"]
        cps = len(dc["text"].replace("\n","")) / dur if dur > 0 else 0
        if cps > max_cps: score -= 10; issues.append(f"CPS:{cps:.0f}")

        # Formality check (e.g. {"wrong": "Sie", "except": "siehe"} for German informal)
        if formality_checks:
            wrong = formality_checks.get("wrong", "")
            except_word = formality_checks.get("except", "")
            if wrong and re.search(rf'\b{wrong}\b', dc["text"]):
                if not except_word or except_word not in dc["text"].lower():
                    score -= 15; issues.append(f"Formality:{wrong}")

        results.append({"cue": i, "score": max(0,score), "issues": issues,
                        "src": dc["src_text"], "tgt": dc["text"], "back": back})

        if (i+1) % 25 == 0 or i == len(target_cues)-1:
            print(f"  {i+1}/{len(target_cues)}")

    scores = [r["score"] for r in results]
    flagged = [r for r in results if r["score"] < 70]
    print(f"  Avg: {sum(scores)/len(scores):.1f}/100, Passing: {len(scores)-len(flagged)}, Flagged: {len(flagged)}")
    return results

def step6_fix(target_cues, verify, episode):
    """Re-translate flagged cues"""
    tgt = CFG.get("target_lang", "de")
    max_cps = CFG.get("max_cps", 21)
    max_line = CFG.get("max_line_chars", 42)

    print(f"\n{'='*70}\nSTEP 6: FIX FLAGGED CUES\n{'='*70}")

    flagged = [r for r in verify if r["score"] < 70]
    if not flagged:
        print("  All passing!"); return target_cues

    episode_ctx = CFG.get("episode_contexts", {}).get(episode, "")
    rules = build_rules_prompt()
    preserve = build_preserve_prompt()

    for fr in flagged:
        idx = fr["cue"]
        dur = target_cues[idx]["end"] - target_cues[idx]["start"]
        max_ch = int(dur * max_cps)
        prompt = f"""Fix this {tgt} subtitle. Issues: {', '.join(fr['issues'])}
{f"Context: {episode_ctx}" if episode_ctx else ""}
{rules}{preserve}
Max {max_ch} chars, {max_line}ch/line.
Source: "{fr['src']}"  Bad translation: "{fr['tgt']}"
Reply ONLY corrected {tgt}:"""
        try:
            new = auto_linebreak(re.sub(r'["\u201e\u201c\u201d]', '', llm(prompt)))
            target_cues[idx]["text"] = new
            print(f"  #{idx}: {new[:50]}")
        except:
            pass
    return target_cues

def step7_polish(target_cues):
    """Fix remaining formality, CPS, line length issues"""
    tgt = CFG.get("target_lang", "de")
    max_cps = CFG.get("max_cps", 21)
    max_line = CFG.get("max_line_chars", 42)
    formality_checks = CFG.get("formality_checks", {})

    print(f"\n{'='*70}\nSTEP 7: POLISH\n{'='*70}")

    fixes = 0
    for i, dc in enumerate(target_cues):
        needs_fix = False; issues = []
        dur = dc["end"] - dc["start"]
        chars = len(dc["text"].replace("\n",""))
        cps = chars/dur if dur > 0 else 0
        max_ch = int(dur * max_cps)

        # Formality check
        if formality_checks:
            wrong = formality_checks.get("wrong", "")
            except_word = formality_checks.get("except", "")
            if wrong and re.search(rf'\b{wrong}\b', dc["text"]):
                if not except_word or except_word not in dc["text"].lower():
                    needs_fix = True; issues.append(f"Formality:{wrong}")

        if cps > max_cps + 2:
            needs_fix = True; issues.append(f"CPS:{cps:.0f}")
        for line in dc["text"].split("\n"):
            if len(line) > max_line:
                needs_fix = True; issues.append("long line"); break

        if needs_fix:
            prompt = f"""Fix this {tgt} subtitle. Issues: {', '.join(issues)}
Max {max_ch} chars, {max_line}ch/line.
Current: "{dc['text']}"
Reply ONLY fixed {tgt}:"""
            try:
                new = auto_linebreak(re.sub(r'["\u201e\u201c\u201d]', '', llm(prompt, temperature=0.2)))
                target_cues[i]["text"] = new
                fixes += 1
            except:
                pass

    # Remove any remaining quote artifacts
    for dc in target_cues:
        dc["text"] = re.sub(r'["\u201e\u201c\u201d]', '', dc["text"])

    print(f"  Polished {fixes} cues")
    return target_cues

def step8_report(cues, target_cues):
    """Final QA report"""
    max_cps = CFG.get("max_cps", 21)
    max_line = CFG.get("max_line_chars", 42)
    formality_checks = CFG.get("formality_checks", {})

    print(f"\n{'='*70}\nSTEP 8: FINAL QA REPORT\n{'='*70}")

    formality_violations = 0
    if formality_checks:
        wrong = formality_checks.get("wrong", "")
        except_word = formality_checks.get("except", "")
        for dc in target_cues:
            if wrong and re.search(rf'\b{wrong}\b', dc["text"]):
                if not except_word or except_word not in dc["text"].lower():
                    formality_violations += 1

    cps_v = 0
    for dc in target_cues:
        dur = dc["end"] - dc["start"]
        if dur > 0 and len(dc["text"].replace("\n",""))/dur > max_cps: cps_v += 1
    long = sum(1 for dc in target_cues for line in dc["text"].split("\n") if len(line) > max_line)
    errors = sum(1 for dc in target_cues if "[ERROR" in dc["text"])

    status = "PASS" if formality_violations == 0 and errors == 0 else "NEEDS REVIEW"

    print(f"  Cues:                {len(target_cues)}")
    print(f"  Errors:              {errors}")
    if formality_checks:
        print(f"  Formality violations:{formality_violations}")
    print(f"  CPS >{max_cps}:            {cps_v}")
    print(f"  Lines >{max_line}:          {long}")
    print(f"  Status:              {status}")

    return {"cues": len(target_cues), "errors": errors,
            "formality_violations": formality_violations,
            "cps_violations": cps_v, "long_lines": long, "status": status}

# ─── MAIN ────────────────────────────────────────────────────────────────────

def run_pipeline(episode, series, base_dir):
    t0 = time.time()
    base = Path(base_dir)

    vid_dir = base / "videos" / series
    dub_dir = base / "dubbed" / series
    src_lang = CFG.get("source_lang", "en")
    tgt_lang = CFG.get("target_lang", "de")

    video = vid_dir / f"{episode}.mp4"
    src_vtt = dub_dir / f"{episode}_{src_lang}.vtt"
    tgt_vtt = dub_dir / f"{episode}_{tgt_lang}.vtt"

    # Find SRT
    series_info = CFG.get("series", {}).get(series, {})
    srt_dir = vid_dir / series_info.get("srt_dir", "subtitles")
    srt = None
    ep_num = re.search(r'(\d+)', episode)
    if ep_num and series_info.get("srt_pattern"):
        srt_name = series_info["srt_pattern"].format(num=int(ep_num.group(1)))
        srt = srt_dir / srt_name
    if (not srt or not srt.exists()) and srt_dir.exists():
        for f in srt_dir.iterdir():
            if f.suffix == '.srt' and ep_num and ep_num.group(1).zfill(2) in f.name:
                srt = f; break

    print(f"\n{'='*70}")
    print(f"SUBTITLE AGENT: {episode}")
    print(f"  Series: {series}")
    print(f"  Video:  {video}")
    print(f"  SRT:    {srt}")
    print(f"  Source:  {src_vtt}")
    print(f"  Target: {tgt_vtt}")
    print(f"{'='*70}")

    has_video = video.exists()
    has_srt = srt is not None and srt.exists()
    has_src_vtt = src_vtt.exists()

    if not has_video:
        print(f"  WARNING: Video not found: {video}")
    if not has_srt:
        print(f"  WARNING: SRT not found")

    # Determine which steps we can run
    if has_srt:
        audio = step0_audio_intel(video, episode) if has_video else {"speech_regions": []}
        cues = step1_source_validation(srt)
        cues = step2_source_qa(cues, audio)
        write_vtt(src_vtt, cues, text_key="text_fixed")
        print(f"  Written: {src_vtt}")
    elif has_src_vtt:
        print(f"  -> No SRT, using existing source VTT for translation")
        cues = parse_vtt(src_vtt)
        for c in cues:
            c["text_fixed"] = c["text"]
    else:
        trans_json = dub_dir / f"{episode}_transcription.json"
        if trans_json.exists():
            print(f"  -> No SRT/VTT, generating source VTT from transcription.json")
            whisper_to_vtt = base / "whisper_to_vtt.py"
            if whisper_to_vtt.exists():
                subprocess.run(["python3", str(whisper_to_vtt),
                               str(trans_json), str(src_vtt)], check=True)
            else:
                print(f"  ERROR: whisper_to_vtt.py not found")
                return
            cues = parse_vtt(src_vtt)
            for c in cues:
                c["text_fixed"] = c["text"]
        else:
            print(f"  ERROR: No SRT, no source VTT, no transcription.json — cannot proceed")
            return

    difficulty = step3_difficulty(cues)
    target_cues = step4_translate(cues, difficulty, episode)
    verify = step5_verify(target_cues)
    target_cues = step6_fix(target_cues, verify, episode)
    target_cues = step7_polish(target_cues)

    # Backup and write final
    if tgt_vtt.exists():
        bak = str(tgt_vtt) + f".bak.{int(time.time())}"
        os.rename(tgt_vtt, bak)
    write_vtt(tgt_vtt, target_cues)
    print(f"  Written: {tgt_vtt}")

    report = step8_report(cues, target_cues)

    report_path = dub_dir / f"{episode}_qa_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s ({elapsed/60:.1f}m)")
    return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Subtitle Agent — automated subtitle translation pipeline")
    parser.add_argument("episode", nargs="?", help="Episode name")
    parser.add_argument("--series", default=None, help="Series name")
    parser.add_argument("--all", action="store_true", help="Process all episodes in series")
    parser.add_argument("--check", help="Just run QA on existing subtitles")
    parser.add_argument("--config", default="project.json", help="Path to project config (default: project.json)")
    args = parser.parse_args()

    # Load config
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_path)
    CFG.update(load_config(config_path))

    base_dir = CFG.get("project_dir", os.path.dirname(os.path.abspath(__file__)))
    src_lang = CFG.get("source_lang", "en")
    tgt_lang = CFG.get("target_lang", "de")

    # Default series: first one in config, or require --series
    if not args.series:
        series_keys = list(CFG.get("series", {}).keys())
        if len(series_keys) == 1:
            args.series = series_keys[0]
        elif not args.all and not args.check and not args.episode:
            parser.print_help()
            sys.exit(0)
        elif not args.series and series_keys:
            args.series = series_keys[0]

    if args.all:
        all_series = [args.series] if args.series else list(CFG.get("series", {}).keys())
        for series in all_series:
            dub_dir = Path(base_dir) / "dubbed" / series
            episodes = set()
            for meta in sorted(dub_dir.glob("*_meta.json")):
                ep = meta.name.replace("_meta.json", "")
                if "_sync" not in ep:
                    episodes.add(ep)
            vid_dir = Path(base_dir) / "videos" / series
            if vid_dir.exists():
                for f in sorted(vid_dir.glob("*.mp4")):
                    episodes.add(f.stem)

            done = 0; failed = 0; skipped = 0
            for ep in sorted(episodes):
                tgt_vtt = dub_dir / f"{ep}_{tgt_lang}.vtt"
                if tgt_vtt.exists():
                    print(f"  SKIP (already done): {ep}")
                    skipped += 1
                    continue
                print(f"\n{'#'*70}\n# {ep}\n{'#'*70}")
                try:
                    report = run_pipeline(ep, series, base_dir)
                    if report:
                        done += 1
                    else:
                        failed += 1
                except Exception as e:
                    print(f"  FAILED: {ep} — {e}")
                    failed += 1
            print(f"\n{'='*70}")
            print(f"  Series: {series}")
            print(f"  Done: {done}  Failed: {failed}  Skipped: {skipped}  Total: {done+failed+skipped}")
            print(f"{'='*70}")
    elif args.check:
        tgt_vtt = Path(base_dir) / "dubbed" / args.series / f"{args.check}_{tgt_lang}.vtt"
        src_vtt = Path(base_dir) / "dubbed" / args.series / f"{args.check}_{src_lang}.vtt"
        if tgt_vtt.exists() and src_vtt.exists():
            src = parse_vtt(src_vtt)
            tgt = parse_vtt(tgt_vtt)
            step8_report(src, tgt)
        else:
            print("VTT files not found")
    elif args.episode:
        run_pipeline(args.episode, args.series, base_dir)
    else:
        parser.print_help()
