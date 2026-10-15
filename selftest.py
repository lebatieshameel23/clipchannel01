"""Offline self-test: builds a synthetic 16:9 video, finds clips with the simple heuristic, and renders
one clip in each layout. No accounts or network needed (only ffmpeg).

    python selftest.py          # writes out/selftest_blur_fit.mp4, _fill.mp4, _split.mp4
"""
import copy
import os
import subprocess

import fetch
import highlights
import render_clip
from common import CACHE, OUT, load_config

SECONDS = 100


def make_video(path):
    # quiet tone, with a loud burst from 40s to 52s so the energy detector has something to find
    expr = "0.03*sin(2*PI*220*t)+if(between(t,40,52),0.5*sin(2*PI*660*t)*sin(2*PI*3*t),0)"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=s=1280x720:r=30:d={SECONDS}", "-f", "lavfi", "-i",
                    f"aevalsrc='{expr}':s=16000:d={SECONDS}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


def make_words():
    base = ("so this is the part where everything changed and honestly nobody saw it coming "
            "because the plan looked perfect until the very last second").split()
    words, t, n = [], 1.0, 0
    while t < SECONDS - 6:
        for i in range(10):
            w = base[(n * 7 + i) % len(base)]
            words.append({"w": w + ("." if i == 9 else ""), "s": round(t, 2), "e": round(t + 0.32, 2)})
            t += 0.36
        t += 0.6
        n += 1
    return words


def main():
    os.environ.pop("ANTHROPIC_API_KEY", None)
    work = CACHE / "selftest"
    work.mkdir(parents=True, exist_ok=True)
    video = work / "source.mp4"
    make_video(video)
    wav = fetch.extract_audio(video, work)
    cfg = copy.deepcopy(load_config())
    cfg["highlights"].update(min_score=0, min_words=10)
    words = make_words()
    clips = highlights.find(words, wav, {"title": "Synthetic test"}, {"name": "Self test"}, cfg)
    print(f"found {len(clips)} clip(s):")
    for c in clips:
        print(f"  {c['start']:.1f}-{c['end']:.1f}s  score {c['score']}  energy {c['energy']}  '{c['hook_title']}'")
    assert clips, "no clips found"
    for layout in ("blur_fit", "fill", "split"):
        src = {"name": "Self test", "layout": layout, "facecam": [0.0, 0.0, 0.35, 0.35]}
        out = render_clip.render(video, 0.0, clips[0], src, cfg, OUT / f"selftest_{layout}.mp4")
        print("rendered", out)


if __name__ == "__main__":
    main()
