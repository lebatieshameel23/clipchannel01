"""Find the best 20-50 second moments in a long video.

Signals: (1) loudness spikes in the audio, (2) a free LLM reading the transcript for self-contained moments
with a setup and a payoff. Clip edges are snapped to sentence boundaries so nothing starts or ends mid-word.
Without any LLM key (or if every provider is down) a simpler energy-and-density heuristic is used.
"""
import json
import os
import re
import sys
import wave

import numpy as np

import llm

SYSTEM = """You pick the best short-form clips from the transcript of a long video or livestream.

RULES
- Each clip is 20 to 50 seconds and is ONE self-contained moment with a setup and a payoff: funny, surprising,
  emotional, a strong opinion, a story beat, a clear useful insight. It must make sense with no earlier context.
- Start on the first sentence of the moment and end at the end of a sentence. Use the times shown in brackets.
- Prefer moments whose first 3 seconds grab attention.
- Skip: dead air, "like and subscribe", chat/donation/sub reading, sponsor or ad reads, anything hateful, sexual,
  graphically violent, medical or financial advice stated as fact, private information about real people
  (addresses, phone numbers, workplaces), and anything involving minors.
- hook_title: at most 48 characters, shown big at the top. It must be accurate to what is actually said in the
  clip. No false claims, no invented numbers, no misleading teaser.
- score: 1 to 10 for how likely a stranger is to watch to the end and share it. Be strict: most moments are 4 to 6.

Reply with ONE JSON object and nothing else:
{"clips":[{"start":<seconds>,"end":<seconds>,"score":<1-10>,"hook_title":"...","reason":"one short sentence"}]}
Return an empty list if nothing in the transcript deserves a clip."""


# ---------- audio energy ----------
def read_energy(wav_path):
    """Robust z-score of loudness per second (loud = high). Expects 16-bit mono PCM wav."""
    w = wave.open(str(wav_path))
    sr, vals = w.getframerate(), []
    while True:
        buf = w.readframes(sr)
        if not buf:
            break
        a = np.frombuffer(buf, dtype=np.int16).astype(np.float32)
        vals.append(20 * np.log10(np.sqrt(np.mean(a * a)) + 1.0))
    w.close()
    db = np.array(vals)
    med = np.median(db)
    mad = np.median(np.abs(db - med)) + 1e-3
    return np.clip((db - med) / (1.4826 * mad), -3, 6)


def window_energy(z, start, end):
    """0..1: how loud the loudest fifth of the window is compared with the whole video."""
    seg = z[max(0, int(start)):int(end) + 1]
    if len(seg) == 0:
        return 0.0
    top = np.sort(seg)[-max(1, len(seg) // 5):]
    return float(np.clip(np.mean(np.clip(top, 0, None)) / 3.0, 0, 1))


def loud_seconds(z, start, end, n=8):
    lo = max(0, int(start))
    seg = z[lo:int(end) + 1]
    idx = np.argsort(seg)[-n:]
    return sorted(int(lo + i) for i in idx if seg[i] > 1.0)


# ---------- transcript structure ----------
def sentences(words, max_words=28, gap=0.7):
    out, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > gap or len(cur) >= max_words):
            out.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?]['\")\]]*$", w["w"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"s": c[0]["s"], "e": c[-1]["e"], "text": " ".join(x["w"] for x in c)} for c in out]


def chunks(sents, size, overlap):
    t, last = sents[0]["s"], sents[-1]["e"]
    while t < last:
        c = [x for x in sents if x["s"] >= t and x["e"] <= t + size]
        if len(c) >= 5:
            yield c
        t += size - overlap


def snap(t, sents, key, tol=4.0):
    best = min(sents, key=lambda x: abs(x[key] - t))
    return best[key] if abs(best[key] - t) <= tol else None


def clean_title(t):
    t = re.sub(r"[^\u0000-￿]", "", str(t)).strip().strip("\"'")
    t = re.sub(r"\s+", " ", t)
    if len(t) > 48:
        t = t[:48].rsplit(" ", 1)[0]
    return t.rstrip(" ,;:-")


# ---------- candidates ----------
def ask_llm(ai_cfg, source, video, chunk, hints, k):
    lines = "\n".join(f"[{c['s']:.1f}-{c['e']:.1f}] {c['text']}" for c in chunk)
    user = (f"Source: {source['name']}\nVideo: {video['title']}\n"
            f"Loudest moments in this part of the audio (seconds): {hints or 'none stand out'}\n"
            f"Return up to {k} clips.\n\nTRANSCRIPT (each line: [start-end seconds] text):\n{lines}")
    return llm.extract_json(llm.complete(SYSTEM, user, max_tokens=2500, ai_cfg=ai_cfg)).get("clips", [])


def heuristic(chunk, z, hc):
    out = []
    for i in range(len(chunk)):
        s, j = chunk[i]["s"], i
        while j + 1 < len(chunk) and chunk[j]["e"] - s < 35:
            j += 1
        e = chunk[j]["e"]
        if e - s < hc["min_seconds"]:
            continue
        out.append({"start": s, "end": e, "score": 4 + 4 * window_energy(z, s, e),
                    "hook_title": chunk[i]["text"], "reason": "heuristic"})
    out.sort(key=lambda c: -c["score"])
    return out[:hc["clips_per_chunk"]]


def finish(c, sents, words, z, hc, banned):
    try:
        s, e, sc = float(c["start"]), float(c["end"]), float(c["score"])
    except (KeyError, TypeError, ValueError):
        return None
    s2, e2 = snap(s, sents, "s"), snap(e, sents, "e")
    if s2 is None or e2 is None or not hc["min_seconds"] <= e2 - s2 <= hc["max_seconds"]:
        return None
    cw = [w for w in words if w["s"] >= s2 - 0.05 and w["e"] <= e2 + 0.4]
    if len(cw) < hc["min_words"]:
        return None
    text = " ".join(w["w"] for w in cw).lower()
    if any(b.lower() in text for b in banned):
        return None
    title = clean_title(c.get("hook_title", ""))
    if not title:
        return None
    en = window_energy(z, s2, e2)
    return {"start": round(s2, 2), "end": round(e2, 2), "energy": round(en, 2),
            "score": round(min(10.0, max(1.0, sc)) + 2 * en, 2),
            "hook_title": title, "reason": str(c.get("reason", ""))[:200], "words": cw}


def overlaps(a, b):
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    return inter > 0.3 * min(a["end"] - a["start"], b["end"] - b["start"])


def find(words, wav_path, video, source, cfg):
    """Return up to clips_per_video clips, best first."""
    hc = cfg["highlights"]
    sents = sentences(words)
    if len(sents) < 5:
        return []
    z = read_energy(wav_path)
    use_llm = llm.available(cfg.get("ai"))
    if not use_llm:
        print("no LLM key (GEMINI_API_KEY / GROQ_API_KEY): using the simple heuristic", file=sys.stderr)
    cands = []
    for chunk in chunks(sents, hc["chunk_seconds"], hc["chunk_overlap"]):
        if not use_llm:
            raw = heuristic(chunk, z, hc)
        else:
            try:
                raw = ask_llm(cfg.get("ai"), source, video, chunk,
                              loud_seconds(z, chunk[0]["s"], chunk[-1]["e"]), hc["clips_per_chunk"])
            except Exception as e:   # providers exhausted or unusable reply: use the heuristic for this chunk
                print(f"LLM failed for one chunk ({e}); using the heuristic for it", file=sys.stderr)
                raw = heuristic(chunk, z, hc)
        for c in raw:
            f = finish(c, sents, words, z, hc, hc.get("banned_phrases", []))
            if f and f["score"] >= hc["min_score"]:
                cands.append(f)
    cands.sort(key=lambda c: -c["score"])
    chosen = []
    for c in cands:
        if all(not overlaps(c, k) for k in chosen):
            chosen.append(c)
        if len(chosen) >= hc["clips_per_video"]:
            break
    return chosen
