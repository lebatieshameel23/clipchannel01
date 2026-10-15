"""yt-dlp wrappers: list videos, fetch audio, captions, and just the section of video a clip needs.

If YTDLP_COOKIES_FILE points to a cookies.txt, it is passed to yt-dlp (helps when a site blocks
cloud servers). Everything here needs network access.
"""
import json
import os
import pathlib
import subprocess
import sys


def _ytdlp(*args, capture=False):
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress", "--retries", "5"]
    if os.environ.get("YTDLP_COOKIES_FILE"):
        cmd += ["--cookies", os.environ["YTDLP_COOKIES_FILE"]]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"yt-dlp failed ({r.returncode}): {(r.stderr or '')[-400:]}")
    return r.stdout


def _flatten(entries):
    for e in entries or []:
        if not e:
            continue
        if e.get("entries"):
            yield from _flatten(e["entries"])
        elif e.get("id"):
            yield e


def list_videos(url, limit):
    """Newest videos at a channel, playlist or single-video URL."""
    data = json.loads(_ytdlp("-J", "--flat-playlist", "--playlist-end", str(limit), url, capture=True))
    entries = list(_flatten(data["entries"])) if data.get("entries") else [data]
    vids = []
    for e in entries:
        vids.append({
            "id": e["id"],
            "title": e.get("title") or e["id"],
            "url": e.get("webpage_url") or e.get("url") or url,
            "duration": e.get("duration"),
            "live": e.get("live_status") in ("is_live", "is_upcoming"),
        })
    return vids


def video_info(url):
    return json.loads(_ytdlp("-J", "--no-playlist", url, capture=True))


def _to_wav(src, wav):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                    str(wav)], check=True)


def extract_audio(path, dest):
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    wav = dest / "audio.wav"
    _to_wav(path, wav)
    return wav


def download_audio(url, dest):
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _ytdlp("-f", "bestaudio/best", "-o", str(dest / "audio_src.%(ext)s"), url)
    src = next(dest.glob("audio_src.*"))
    wav = dest / "audio.wav"
    _to_wav(src, wav)
    src.unlink()
    return wav


def download_captions(url, dest):
    """English captions as json3 (manual if present, else auto-generated). None if unavailable."""
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        _ytdlp("--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en",
               "--sub-format", "json3", "-o", str(dest / "subs.%(ext)s"), url)
    except RuntimeError:
        return None
    files = sorted(dest.glob("subs*.json3"))
    return files[0] if files else None


def download_section(url, start, end, dest):
    """Download only [start, end] seconds of the video (720p max). Returns the file path."""
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _ytdlp("-f", "bv*[height<=720]+ba/b[height<=720]/b", "--download-sections", f"*{start:.2f}-{end:.2f}",
           "--force-keyframes-at-cuts", "--merge-output-format", "mp4",
           "-o", str(dest / "section.%(ext)s"), url)
    return next(dest.glob("section.*"))
