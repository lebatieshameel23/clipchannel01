# Clip channel: automated Shorts from long videos

Every day, with no manual steps after setup:

```
new videos at your sources -> audio + transcript (YouTube captions, else whisper)
   -> best 20-50 s moments (loudness spikes + a free LLM reading the transcript)
   -> cut and render 1080x1920 with word-highlighted captions, hook title, source credit
   -> scheduled upload, 3 a day  (rendered clips also kept in out/clips/ for TikTok, Reels or campaigns)
```

**Cost: $0.**

| Piece | Free service | Card needed? |
|---|---|---|
| Scheduling and rendering | GitHub Actions (free for public repos) | No |
| Highlight picking | Google Gemini API free tier (key from aistudio.google.com/apikey); optional free backups: Groq, OpenRouter | No |
| Transcripts | YouTube captions via yt-dlp, else faster-whisper on the runner | No |
| Upload | YouTube Data API | No |

Paid Anthropic is still supported but optional (`ANTHROPIC_API_KEY`, used as the last fallback). GitHub Models was
retired on 30 July 2026, so it is not used. Keep the repo **public**: private repos have limited free minutes.

## The one rule

`sources.yaml` refuses any source that does not state **who allowed the clipping** (`own`, `creator_approved`,
`campaign` or `cc_by`) and **where that is written down** (`proof`). I can't check the proof, so it is on you, but
there is a practical reason for the rule. YouTube's reused-content policy covers "borrowing other people's material
without adding significant original value", and enforcement is at channel level: a violation can suspend
monetisation for the whole channel, not just one video. Permission from the creator is not mentioned as a fix, so
treat YouTube ad revenue on a pure clips channel as unlikely. The realistic paths are your own long content, or
authorised clipping campaigns that pay per verified views (see "Money" below).

## Setup (about 30 minutes)

1. **Test locally, no accounts:** install Python and ffmpeg, then `pip install -r requirements.txt` and
   `python selftest.py`. It builds a fake video and renders one clip in each layout to `out/` (takes about 2 minutes).
2. **Check the sources** in `sources.yaml`. It ships with **NASA** (public domain per NASA's media guidelines; read the page and its limits on astronaut likenesses and third-party footage). Add your own, `cc_by` or approved sources the same way (examples inside). Public domain does not exempt a channel from YouTube's reused-content policy, so keep the captions and hook titles that add value, and do not make clips that read as ads. Set your handle, time zone and posting times in `config.yaml`.
3. **Make a new GitHub repo for this project** (separate from your coding channel, so secrets and quota stay separate)
   and upload everything including the hidden `.github` folder. Private repos get a limited number of free Actions
   minutes a month (check GitHub's billing page); long streams transcribed with whisper can use a lot, so keep
   `max_videos_per_run` low or run on your own computer instead.
4. **YouTube login for the clip channel:** on a phone follow `YOUTUBE_LOGIN.md` (no computer needed). Otherwise, in Google Cloud Console create a project, enable **YouTube Data API v3**,
   create an OAuth client of type **Desktop app**, save it as `client_secret.json` here, and run
   `python auth_youtube.py` signed in as the clip channel's account. It prints three values. Uploads from an
   unaudited API project stay private until you pass YouTube's API compliance audit; until then you flip them public
   in YouTube Studio.
5. **Secrets** (repo Settings > Secrets and variables > Actions): `GEMINI_API_KEY` (free), `YT_CLIENT_ID`,
   `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`. Optional free backups: `GROQ_API_KEY` (console.groq.com),
   `OPENROUTER_API_KEY` (openrouter.ai/keys); if Gemini is rate limited the next key is used. Also Settings > Actions > General > Workflow permissions: Read and write.
6. **Go live:** Actions tab > "Daily clips" > Run workflow. After that it runs by itself at 05:00 SAST.

## Layouts (per source)

- `blur_fit` (default): the full picture centred over a blurred copy.
- `fill`: centre crop to vertical; `focus_x: 0..1` slides the crop.
- `split`: facecam on top, screen below; `facecam: [x, y, w, h]` as fractions of the frame.

## Everyday use

- Queue of waiting clips: `content/queue/`. Delete any you don't like. `content/used/` is what has gone out.
- Pickier clips: raise `highlights.min_score`. More or fewer a day: edit `schedule.slots`.
- Add words to `highlights.banned_phrases` to drop clips containing them.
- Rendered files: the `clips` artifact on each workflow run (kept 14 days).
- With no LLM key at all, or when every provider is rate limited, the highlight finder falls back to a simple loudness-and-speech heuristic for that video or chunk. Much weaker.
- Calls used: about one per 7-minute chunk, so roughly 17 for a 2-hour stream, times `max_videos_per_run`. Free limits change without notice and Google no longer publishes per-model numbers (see aistudio.google.com/rate-limit). The pipeline paces itself and fails over, but if you add many sources, lower `max_videos_per_run`.

## Money, honestly

- Clipping campaigns such as Whop Content Rewards pay per 1,000 verified views from a fixed budget. One third-party
  guide ([OpenClip](https://openclip.app/guides/whop-clipping-guide)) puts rates at $0.20 to $6 per 1,000, averaging
  about $1, with budgets that run out first-come-first-served. Each campaign's rules decide which footage and which
  platforms count. Read them, and use `credit_text` / `required_tags` to match them.
- At about $1 per 1,000 views, 1 million views is roughly $1,000. Shorts ad revenue needs far more
  (see GROWTH.md in the coding-channel project for the current thresholds).

## Honest limits

- Tested here: source validation, captions parsing, transcript chunking, the LLM client (retries, rate-limit waits, failover between providers) and prompt and reply handling (against local stand-in servers), highlight snapping, all three layouts, section-offset maths, scheduling, queue and history.
- **Not tested here (no network):** yt-dlp downloads, faster-whisper, real Gemini/Groq and YouTube calls, and whether the default model ids (`gemini-3.8-flash`, `openai/gpt-oss-120b`) are still current; if one stops working the log shows `HTTP 404` and you edit it under `ai:` in `config.yaml`. Run the workflow
  by hand and watch the first run before trusting the schedule.
- YouTube often blocks downloads from cloud servers ("Sign in to confirm you're not a bot"). Fixes: put a cookies.txt
  from a throwaway Google account (never your channel account) in the `YTDLP_COOKIES` secret, or run the project on
  your own computer with `path:` sources and Windows Task Scheduler or cron.
- Music in streams can trigger Content ID claims on your uploads. Captions are English by default.
- The facecam rectangle is fixed; there is no face tracking.
