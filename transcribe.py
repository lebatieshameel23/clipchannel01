"""Word-level transcripts: YouTube captions when available (fast), otherwise faster-whisper on CPU.

A transcript is a list of {"w": word, "s": start_seconds, "e": end_seconds}.
"""
import json
import pathlib

import fetch


def parse_json3(path):
    data = json.loads(pathlib.Path(path).read_text())
    words = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        base = ev.get("tStartMs", 0)
        ev_end = (base + ev.get("dDurationMs", 0)) / 1000.0
        for sg in segs:
            tokens = sg.get("utf8", "").replace("\n", " ").split()
            if not tokens:
                continue
            s = (base + sg.get("tOffsetMs", 0)) / 1000.0
            if len(tokens) == 1:
                words.append({"w": tokens[0], "s": s, "e": s})
            else:  # manual captions: a whole phrase in one segment, spread evenly
                span = max(0.3 * len(tokens), ev_end - s)
                for i, t in enumerate(tokens):
                    words.append({"w": t, "s": s + span * i / len(tokens), "e": s + span * (i + 1) / len(tokens)})
    words.sort(key=lambda w: w["s"])
    for i, w in enumerate(words):
        nxt = words[i + 1]["s"] if i + 1 < len(words) else w["s"] + 0.5
        w["e"] = max(w["s"] + 0.05, min(nxt, w["s"] + 0.9)) if w["e"] <= w["s"] else w["e"]
    return words


def whisper_words(wav, model_name, language=None):
    from faster_whisper import WhisperModel  # imported lazily: heavy, and only needed here
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(wav), word_timestamps=True, vad_filter=True,
                                   language=language or None, beam_size=1)
    words = []
    for seg in segments:
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append({"w": t, "s": float(w.start), "e": float(w.end)})
    return words


def get_words(video, wav, workdir, cfg):
    """Captions first (if allowed and dense enough), then whisper."""
    tcfg = cfg["transcript"]
    minutes = max(1.0, (video.get("duration") or 600) / 60.0)
    if tcfg.get("prefer_captions") and video.get("kind") == "url":
        cap = fetch.download_captions(video["url"], workdir)
        if cap:
            words = parse_json3(cap)
            if len(words) / minutes >= 40:      # at least ~40 words a minute, else captions are too sparse
                return words
    return whisper_words(wav, tcfg["whisper_model"], tcfg.get("language"))
