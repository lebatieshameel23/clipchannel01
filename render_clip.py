"""Cut one clip out of a source video and render it as a 1080x1920 Short.

Layouts (set per source in sources.yaml):
  blur_fit  the full 16:9 picture centred over a blurred, zoomed copy of itself (safe default)
  fill      centre-crop to vertical (set focus_x 0..1 to slide the crop left or right)
  split     facecam on top, the centre of the screen below (set facecam: [x, y, w, h] as fractions)

Burned in: word-by-word highlighted captions, a hook title for the first 3 seconds, a handle, a source
credit, and a progress bar. Audio is loudness-normalised. Uses ffmpeg with libass.
"""
import pathlib
import re
import subprocess
import tempfile

from common import ROOT

YELLOW, WHITE = "&H0000E6FF", "&H00FFFFFF"


def _cs(t):
    cs = max(0, int(round(t * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(text):
    text = re.sub(r"[^\u0000-￿]", "", text)
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def caption_chunks(words):
    chunks, cur = [], []
    for w in words:
        txt = _esc(w["w"]).strip().upper()
        if not txt:
            continue
        if cur and (len(cur) >= 3 or len(" ".join(x["w"] for x in cur + [{"w": txt}])) > 18):
            chunks.append(cur)
            cur = []
        cur.append({"w": txt, "s": w["s"], "e": w["e"]})
        if re.search(r"[.!?]$", txt):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(clip, source, cfg, dur):
    cap = cfg.get("captions", {})
    hook_margin = 590 if source.get("layout") == "split" else 240   # split: keep the title off the facecam
    font, size = cap.get("font_name", "DejaVu Sans"), cap.get("font_size", 84)
    t0 = clip["start"]
    credit = source.get("credit_text") or f"Clip from {source['name']}"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{WHITE},{WHITE},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,8,2,2,40,40,430,1
Style: Hook,{font},68,&H00000000,&H00000000,{YELLOW},{YELLOW},-1,0,0,0,100,100,0,0,3,16,0,8,70,70,{hook_margin},1
Style: Handle,{font},36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,7,50,50,70,1
Style: Credit,{font},34,&H00DDDDDD,&H00DDDDDD,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,2,50,50,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    handle = _esc(cfg["channel"].get("handle", ""))
    if handle:
        ev.append(f"Dialogue: 0,{_cs(0)},{_cs(dur)},Handle,,0,0,0,,{handle}")
    ev.append(f"Dialogue: 0,{_cs(0)},{_cs(dur)},Credit,,0,0,0,,{_esc(credit)}")
    ev.append(f"Dialogue: 2,{_cs(0)},{_cs(min(3.2, dur))},Hook,,0,0,0,,{{\\fad(120,250)}}{_esc(clip['hook_title']).upper()}")
    chunks = caption_chunks(clip["words"])
    for ci, ch in enumerate(chunks):
        nxt_chunk = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else None
        for wi, w in enumerate(ch):
            s = w["s"] - t0
            if wi + 1 < len(ch):
                e = min(ch[wi + 1]["s"] - t0, s + 1.2)
            else:
                e = w["e"] - t0 + 0.15
                if nxt_chunk is not None:
                    e = min(e, nxt_chunk - t0)
            if e <= s:
                e = s + 0.1
            text = " ".join((f"{{\\c{YELLOW}&}}{x['w']}{{\\c{WHITE}&}}" if k == wi else x["w"]) for k, x in enumerate(ch))
            ev.append(f"Dialogue: 1,{_cs(max(0, s))},{_cs(min(dur, e))},Cap,,0,0,0,,{text}")
    return head + "\n".join(ev) + "\n"


def build_filter(layout, source, dur, fontsdir):
    if layout == "fill":
        fx = float(source.get("focus_x", 0.5))
        core = f"[0:v]scale=-2:1920,crop=1080:1920:x='(iw-1080)*{fx}':y=0[v0];"
    elif layout == "split":
        fx, fy, fw, fh = source["facecam"]
        core = (f"[0:v]split=2[f][g];"
                f"[f]crop=w=iw*{fw}:h=ih*{fh}:x=iw*{fx}:y=ih*{fy},scale=1080:640:force_original_aspect_ratio=increase,crop=1080:640[top];"
                f"[g]scale=-2:1280,crop=1080:1280[bot];[top][bot]vstack=inputs=2[v0];")
    else:
        core = ("[0:v]split=2[a][b];"
                "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=35,eq=brightness=-0.1[bg];"
                "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v0];")
    fd = f":fontsdir={fontsdir}" if fontsdir else ""
    return (core + f"[v0]setsar=1[v1];[v1][1:v]overlay=x='-w+w*t/{dur:.3f}':y=0[v2];[v2]ass=sub.ass{fd}[v]")


def has_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def render(src_path, src_offset, clip, source, cfg, out_path):
    """src_offset = where src_path begins on the original video's timeline (0 for a whole file)."""
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dur = min(59.0, clip["end"] - clip["start"] + 0.25)
    ss = max(0.0, clip["start"] - src_offset)
    layout = source.get("layout", "blur_fit")
    fonts = ROOT / "assets" / "fonts"
    fontsdir = str(fonts) if fonts.exists() else ""
    with tempfile.TemporaryDirectory() as td:
        (pathlib.Path(td) / "sub.ass").write_text(build_ass(clip, source, cfg, dur), encoding="utf-8")
        audio = ("[0:a]loudnorm=I=-16:TP=-1.5:LRA=11," if has_audio(src_path) else "anullsrc=r=44100:cl=stereo,atrim=0:%.3f," % dur)
        fc = build_filter(layout, source, dur, fontsdir) + f";{audio}afade=t=out:st={max(0, dur - 0.25):.3f}:d=0.25[a]"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ss:.3f}", "-i", str(pathlib.Path(src_path).resolve()),
               "-f", "lavfi", "-i", f"color=c=0x00FF66:s=1080x10:r=30:d={dur:.3f}",
               "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "30",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path.resolve())]
        subprocess.run(cmd, check=True, cwd=td)
    return out_path
