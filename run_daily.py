"""Daily run: find new videos, pick highlights, render vertical clips, schedule uploads.

    python run_daily.py                    # full run
    python run_daily.py --no-upload        # analyse and render only; clips land in out/clips/
    python run_daily.py --discover-only    # analyse new videos and fill the queue, nothing else
"""
import argparse
import datetime as dt
import glob
import json
import pathlib
import re
import shutil
import sys
import tempfile
from zoneinfo import ZoneInfo

import fetch
import highlights
import render_clip
import transcribe
from common import (CACHE, OUT, QUEUE, ROOT, USED, file_video_id, licence_is_cc, load_config, load_history,
                    load_sources, queue_items, save_history)

NOTES = {
    "own": "This is my own content.",
    "creator_approved": "Used with the creator's permission.",
    "campaign": "Made for an authorised clipping campaign.",
    "cc_by": "Licensed under Creative Commons Attribution.",
    "public_domain": "Public-domain footage. This channel is not affiliated with or endorsed by the source.",
}


def safe(s):
    return re.sub(r"[^\w-]", "_", s)


def pending_slots(cfg, hist, days):
    tz = ZoneInfo(cfg["timezone"])
    now = dt.datetime.now(tz)
    slots = []
    for d in range(1, days + 1):
        day = (now + dt.timedelta(days=d)).date()
        for s in cfg["schedule"]["slots"]:
            hh, mm = map(int, s.split(":"))
            when = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
            if when > now + dt.timedelta(minutes=15) and when.isoformat() not in hist["scheduled"]:
                slots.append(when)
    return slots


# ---------- discovery ----------
def candidate_videos(cfg, sources, hist):
    d = cfg["discovery"]
    lists = []
    for src in sources:
        vids = []
        try:
            if src.get("url"):
                for v in fetch.list_videos(src["url"], d["recent_per_source"]):
                    v.update(kind="url", source=src["name"])
                    vids.append(v)
            if src.get("path"):
                files = sorted(glob.glob(str(ROOT / src["path"])), key=lambda p: -pathlib.Path(p).stat().st_mtime)
                for p in files:
                    vids.append({"id": file_video_id(p), "title": pathlib.Path(p).stem, "url": "", "duration": None,
                                 "live": False, "kind": "file", "path": p, "source": src["name"]})
        except Exception as e:
            print(f"could not list '{src['name']}': {e}", file=sys.stderr)
        keep = []
        for v in vids:
            dur = v.get("duration")
            if (v["id"] in hist["analyzed"] or hist["failures"].get(v["id"], 0) >= 2 or v.get("live")
                    or (dur and not d["min_video_minutes"] * 60 <= dur <= d["max_video_minutes"] * 60)):
                continue
            keep.append(v)
        lists.append(keep)
    out = []
    while any(lists):                      # round-robin across sources, newest first within each
        for lst in lists:
            if lst:
                out.append(lst.pop(0))
    return out


def wav_seconds(wav):
    import wave
    with wave.open(str(wav)) as w:
        return w.getnframes() / w.getframerate()


def analyze_video(video, src, cfg, hist):
    workdir = CACHE / safe(video["id"])
    if src["permission"] == "cc_by" and video["kind"] == "url":
        # A channel can mix licences, so check THIS video's own licence before using it.
        lic = fetch.video_info(video["url"]).get("license")
        if not licence_is_cc(lic):
            print(f"  skipped: this video's licence is {lic!r}, not Creative Commons", file=sys.stderr)
            hist["analyzed"][video["id"]] = {"title": video["title"], "skipped": "licence"}
            return 0
    try:
        wav = (fetch.extract_audio(video["path"], workdir) if video["kind"] == "file"
               else fetch.download_audio(video["url"], workdir))
        video["duration"] = wav_seconds(wav)
        d = cfg["discovery"]
        if not d["min_video_minutes"] * 60 <= video["duration"] <= d["max_video_minutes"] * 60:
            hist["analyzed"][video["id"]] = {"title": video["title"], "skipped": "length"}
            return 0
        words = transcribe.get_words(video, wav, workdir, cfg)
        clips = highlights.find(words, wav, video, src, cfg)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    QUEUE.mkdir(parents=True, exist_ok=True)
    for c in clips:
        item = {"video_id": video["id"], "video_title": video["title"], "video_url": video.get("url", ""),
                "kind": video["kind"], "path": video.get("path", ""), "source": src["name"], **c, "fails": 0}
        (QUEUE / f"{safe(video['id'])}_{int(c['start'] * 10):07d}.json").write_text(
            json.dumps(item, indent=1, ensure_ascii=False))
    hist["analyzed"][video["id"]] = {"title": video["title"], "source": src["name"], "clips": len(clips),
                                     "when": dt.datetime.now(dt.timezone.utc).isoformat()}
    return len(clips)


def discover(cfg, sources, hist):
    d = cfg["discovery"]
    by_name = {s["name"]: s for s in sources}
    done = 0
    for video in candidate_videos(cfg, sources, hist):
        if done >= d["max_videos_per_run"] or len(queue_items()) >= d["queue_target"]:
            break
        print(f"analysing: {video['title']} ({video['source']})")
        try:
            n = analyze_video(video, by_name[video["source"]], cfg, hist)
            print(f"  -> {n} clip(s) queued")
        except Exception as e:
            hist["failures"][video["id"]] = hist["failures"].get(video["id"], 0) + 1
            print(f"  failed: {e}", file=sys.stderr)
        done += 1
        save_history(hist)


# ---------- render and upload ----------
def pick_next(items, taken, cap):
    for it in items:
        if taken.get(it["video_id"], 0) < cap:
            return it
    return items[0] if items else None


def build_description(item, src):
    t = int(item["start"])
    stamp = f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"
    lines = [item["hook_title"], "", f"Source: {src['name']} - {item['video_title']}"]
    if item["kind"] == "url" and item["video_url"]:
        lines.append(f"Full video: {item['video_url']} (this clip starts at {stamp})")
    lines.append(NOTES[src["permission"]])
    if src.get("credit_text"):
        lines.append(src["credit_text"])
    lines += ["", " ".join(list(src.get("required_tags", [])) + ["#shorts"])]
    return "\n".join(lines)


def make_clip(item, src, cfg):
    out = OUT / "clips" / f"{safe(item['video_id'])}_{int(item['start'] * 10):07d}.mp4"
    with tempfile.TemporaryDirectory() as td:
        if item["kind"] == "file":
            path, off = item["path"], 0.0
        else:
            off = max(0.0, item["start"] - 2.0)
            path = fetch.download_section(item["video_url"], off, item["end"] + 2.0, td)
        return render_clip.render(path, off, item, src, cfg, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--discover-only", action="store_true")
    args = ap.parse_args()
    cfg, sources = load_config(), load_sources()
    if not sources:
        sys.exit("No usable sources. Add one to sources.yaml (it needs a permission type and a proof note).")
    hist = load_history()
    upload = cfg["upload"]["enabled"] and not args.no_upload

    slots = pending_slots(cfg, hist, cfg["schedule"]["days_ahead"])
    if not slots and not args.discover_only:
        print("nothing to schedule")
        return
    if len(queue_items()) < max(len(slots), cfg["discovery"]["queue_target"]):
        discover(cfg, sources, hist)
    if args.discover_only:
        print(f"{len(queue_items())} clip(s) in the queue")
        return

    by_name = {s["name"]: s for s in sources}
    items, taken, failures = queue_items(), {}, 0
    USED.mkdir(parents=True, exist_ok=True)
    for when in slots:
        item = pick_next(items, taken, cfg["discovery"]["max_clips_per_video_per_run"])
        if item is None:
            print("queue empty", file=sys.stderr)
            failures += 1
            break
        items.remove(item)
        src = by_name.get(item["source"])
        qf = item.pop("_file")
        if src is None:
            qf.unlink()
            continue
        ok = True
        try:
            mp4 = make_clip(item, src, cfg)
            if upload:
                from youtube_api import upload_scheduled
                publish = when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                yid = upload_scheduled(mp4, item["hook_title"], build_description(item, src),
                                       src.get("tags", []) + ["shorts"], publish, src.get("category", "24"))
                hist["scheduled"][when.isoformat()] = yid
                hist["videos"][yid] = {"title": item["hook_title"], "source": src["name"],
                                       "video_id": item["video_id"], "start": item["start"], "publishAt": publish}
                print(f"scheduled {when.isoformat()} -> https://youtube.com/shorts/{yid}")
            else:
                print(f"rendered {mp4}")
        except (Exception, SystemExit) as e:
            ok = False
            failures += 1
            item["fails"] = item.get("fails", 0) + 1
            print(f"FAILED {qf.name}: {e}", file=sys.stderr)
            if item["fails"] < 2:          # give it one more try on the next run
                qf.write_text(json.dumps(item, indent=1, ensure_ascii=False))
                continue
        taken[item["video_id"]] = taken.get(item["video_id"], 0) + 1
        if ok:
            shutil.move(str(qf), USED / qf.name)
        else:                              # failed twice: park it so it stops blocking the queue
            qf.unlink()
            (USED / ("failed_" + qf.name)).write_text(json.dumps(item, indent=1, ensure_ascii=False))
        save_history(hist)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
