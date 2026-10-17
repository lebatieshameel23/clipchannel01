"""Shared helpers: paths, config, source registry (with permission checks), history."""
import hashlib
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
CONTENT = ROOT / "content"
QUEUE = CONTENT / "queue"
USED = CONTENT / "used"
CACHE = ROOT / "cache"
OUT = ROOT / "out"
HISTORY = CONTENT / "history.json"

PERMISSIONS = {"own", "creator_approved", "campaign", "cc_by", "public_domain"}
LAYOUTS = {"blur_fit", "fill", "split"}


def licence_is_cc(licence):
    """True if yt-dlp's per-video 'license' text says Creative Commons. Missing or unknown means False."""
    return "creative commons" in str(licence or "").lower()


def load_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def source_problems(s):
    p = []
    if not s.get("name"):
        p.append("missing name")
    if not (s.get("url") or s.get("path")):
        p.append("needs a url or a path")
    if s.get("permission") not in PERMISSIONS:
        p.append(f"permission must be one of {sorted(PERMISSIONS)}")
    if not str(s.get("proof", "")).strip():
        p.append("proof is empty: write who allowed this and where it is written down")
    lay = s.get("layout", "blur_fit")
    if lay not in LAYOUTS:
        p.append(f"layout must be one of {sorted(LAYOUTS)}")
    if lay == "split":
        fc = s.get("facecam")
        ok = isinstance(fc, list) and len(fc) == 4 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in fc)
        if not ok:
            p.append("split layout needs facecam: [x, y, w, h] as fractions between 0 and 1")
    return p


def load_sources(verbose=True):
    raw = yaml.safe_load((ROOT / "sources.yaml").read_text()) or {}
    good = []
    for s in raw.get("sources") or []:
        probs = source_problems(s)
        if probs:
            if verbose:
                print(f"source '{s.get('name', '?')}' skipped: " + "; ".join(probs), file=sys.stderr)
            continue
        if s.get("enabled", True):
            good.append(s)
    return good


def file_video_id(path):
    p = pathlib.Path(path)
    return "file-" + hashlib.sha1(f"{p.name}:{p.stat().st_size}".encode()).hexdigest()[:10]


def load_history():
    h = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    h.setdefault("analyzed", {})    # source video id -> summary
    h.setdefault("failures", {})    # source video id -> failure count
    h.setdefault("scheduled", {})   # ISO local slot -> uploaded video id
    h.setdefault("videos", {})      # uploaded video id -> info
    return h


def save_history(h):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(h, indent=2, ensure_ascii=False))


def queue_items():
    QUEUE.mkdir(parents=True, exist_ok=True)
    items = []
    for f in QUEUE.glob("*.json"):
        d = json.loads(f.read_text())
        d["_file"] = f
        items.append(d)
    items.sort(key=lambda d: -d["score"])
    return items
