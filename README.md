# media-util

Shared tooling for lecture media across teaching sites (DJ4E, PY4E, CC4E, etc.).

**Day-to-day use:** source the course `media.env`, then run the workflow below.
Course-specific data (playlist dumps, vocabulary, `lessons.json`, `media.yaml`)
stays in each course repository. Shared scripts live here.

## Quick reference

Always start with:

```bash
source /Users/csev/htdocs/dj4e/media.env
cd /Users/csev/htdocs/dj4e
```

| I want to… | Command |
|---|---|
| Dump YouTube playlist | `dump-youtube-playlist.sh` |
| Diff lessons ↔ playlist JSONL | `compare-lessons-youtube.py` |
| Diff lessons ↔ MEDIA_ROOT | `compare-lessons-root.py` |
| Generate vocabulary file | `tar cfv lectures.tar lectures` then use ChatGPT |
| Transcribe lessons only | `whisper-lessons` |
| Generate substitutions | `tar cfv whisper.tar whisper` then use ChatGPT |
| Clean transcript mis-hears | `whisper-cleanup.py` |
| Generate AI title/tags/desc | `whisper-desc` (requires `ollama serve`) |
| Build/Rebuild media.yaml (reads whisper) | `bootstrap-media-yaml.py` |
| Update titles → lessons.json | `update-lessons-from-media-yaml.py` |

At this point `media.yaml` is the course of truth.   We can get the titles,
descriptions, and tags uploaded into the YouTube playlist.

| I want to… | Command |
|---|---|
| Test YouTube OAuth | `test-youtube-oauth.py` |
| YouTube apply | `update-youtube-from-media-yaml.py --apply` |
| YouTube apply one change | `update-youtube-from-media-yaml.py --apply --limit 1` |
| YouTube apply one video | `update-youtube-from-media-yaml.py --apply --only VIDEO_ID` |

Once things are all in sync or things are updated, we can check for drift between
the media folder, transcriptions in `whisper`, `lessons.json`, `media.yaml`,
and YouTube as things get edited independently.

| I want to… | Command |
|---|---|
| Download latest YouTube playlist | `dump-youtube-playlist.sh` |
| Diff lessons ↔ media.yaml | `compare-lessons.py` |
| Diff media.yaml ↔ MEDIA_ROOT | `compare-media-root.py` |
| Diff media.yaml ↔ playlist | `compare-youtube.py` |
| Clean orphan whisper files | `compare-whisper-root.py` / `--remove` |

## Prerequisites

See [INSTALL.md](INSTALL.md) for Mac install steps for:

- `ffmpeg` / `ffprobe`
- `yt-dlp`
- `whisper-cli` (whisper.cpp) + ggml model
- Ollama + a local model (default `qwen3:4b`)
- Python packages (`pip3 install -r requirements.txt`): `ruamel.yaml` plus
  Google API libraries for YouTube updates

## Per-course setup

Each course keeps a `media.env` that puts this repo's `bin/` on `PATH` and
sets course paths. Example (`/Users/csev/htdocs/dj4e/media.env`):

```bash
export MEDIA_UTIL=/Users/csev/htdocs/media-util
case ":$PATH:" in
  *":$MEDIA_UTIL/bin:"*) ;;
  *) export PATH="$MEDIA_UTIL/bin:$PATH" ;;
esac

export COURSE_ROOT=/Users/csev/htdocs/dj4e
export MEDIA_ROOT=/Users/csev/Desktop/teach/dj4e-media
export WHISPER_ROOT=/Users/csev/htdocs/dj4e/whisper
export YOUTUBE_DIR=/Users/csev/htdocs/dj4e/youtube
export YOUTUBE_PLAYLIST='https://www.youtube.com/playlist?list=PLlRFEj9H3Oj5e-EH0t3kXrcdygrL9-u-Z'
export COURSE_HINT='Django for Everybody, DJ4E, Django, Python, web development, Dr. Chuck, Chuck Severance'
export EXTRA_TAGS='dj4e, dj4e-lecture'
export EXTRA_DESCRIPTION='For more materials, auto graders, and more courses, please see www.masterprogrammer.com.'
```

Optional: `./bin/install-symlinks.sh` also links tools into `~/bin`.

## Main workflow

Run from the course www root after sourcing `media.env`.

```bash
source /Users/csev/htdocs/dj4e/media.env
cd /Users/csev/htdocs/dj4e
```

### 1. Download the existing YouTube playlist

```bash
dump-youtube-playlist.sh
```

Writes `$YOUTUBE_DIR/youtube-playlist.jsonl` (ids, titles, descriptions, durations).
Members-only / blocked playlist items may make `yt-dlp` exit non-zero; the dump
script still keeps whatever entries it could fetch.

### 2. Align `lessons.json` and `MEDIA_ROOT`

```bash
compare-lessons-root.py
```

Fix mismatches until this is clean: missing media on disk, unreferenced files under
`MEDIA_ROOT`, conflicting titles/youtube ids inside `lessons.json`. Move
superseded media aside (for example into a `*-media-old` tree) and update
`lessons.json` paths as needed. This step does **not** touch `media.yaml`.

### 3. Clean orphan whisper artifacts

```bash
compare-whisper-root.py          # report
compare-whisper-root.py --remove # delete orphans
```

Removes `whisper/{txt,vtt,srt,desc}/...` entries whose matching media file is
no longer under `MEDIA_ROOT`.

### 4. Transcribe new / missing media

All media under `MEDIA_ROOT`:

```bash
whisper-media.sh
```

Or only media referenced by `lessons.json`:

```bash
whisper-lessons
```

Both write transcripts under `$WHISPER_ROOT` (`txt/`, `vtt/`, `srt/`).
Existing transcripts are skipped unless you pass `--force`.

Optional cleanup of Whisper mis-hears (after or during transcription):

```bash
whisper-cleanup.py
```

Rules live in a `*whisper-replacements*` file under `$WHISPER_ROOT` (searched
upward). Format is **only**:

```text
incorrect => correct
```

Tab or space separation is an error. Blank lines and `#` comments are ignored.

### 5. Generate titles, tags, and descriptions with Ollama

Start Ollama if it is not already running, then:

```bash
ollama serve   # if needed
whisper-desc
```

Uses `$WHISPER_ROOT` from `media.env` (no need to `cd whisper`).
Writes `desc/...` with the same relative names as `txt/...`:

```
title

tag1, tag2, tag3

two paragraph description
```

Title / tags / description are sanitized for YouTube (no HTML-like
`<tags>` or raw angle brackets). Defaults: model `qwen3:4b` at
`http://localhost:11434`. Use `--force` to overwrite existing `desc/` files.

### 6. Build `media.yaml`

```bash
bootstrap-media-yaml.py
```

Builds / refreshes `media.yaml` from:

- `MEDIA_ROOT` (inventory: every `.mov` / `.mp4` / `.m4v`)
- `lessons.json` (`DJ nn.mm` prefix and youtube ids; Review wording is
  lessons-only and is **not** stored in `media.yaml`)
- `youtube/youtube-playlist.jsonl` (youtube id / description matching)
- `WHISPER_ROOT/desc/...` (AI title, tags, description from `whisper-desc`)

`media.yaml` is the shared publish surface for each lecture (YouTube now;
Kaltura later). Course extras and AI metadata are baked onto each entry so
upload tools only need to read `media.yaml`.

Titles are composed as:

```text
DJ nn.mm <AI title from desc> (m:ss)
```

falling back to the cleaned lessons.json title body when no AI title exists.
Also records `size`, `md5`, `duration`, and `duration_text` from disk.

Description priority: AI `whisper/desc` if present, else YouTube playlist
(empty fields only, unless `--force-youtube`). Tags come from AI `whisper/desc`
when present (comma-separated string). Course `EXTRA_TAGS` / `EXTRA_DESCRIPTION`
from `media.env` are appended onto each entry (and also stored as top-level
`extra_tags` / `extra_description`). Existing `youtube_id` is filled when empty
(`--force-youtube` to overwrite). `kaltura_id` is preserved.

Top-level globals are copied from `media.env` on each run:

`course_root`, `media_root`, `whisper_root`, `youtube_dir`, `youtube_playlist`,
`course_hint`, `extra_tags`, and `extra_description`.

### 7. Update titles in `lessons.json`

```bash
update-lessons-from-media-yaml.py          # write
update-lessons-from-media-yaml.py --dry-run
```

Copies `media.yaml` titles onto `lessons.json` entries that have a `media`
path. Review stays a **lessons.json-only** concept:

- If the old title was a review (`Review:…` / `(review)`), the new title is
  `Review: <media.yaml title>` and `"review": true` is set.
- Non-review entries get the media.yaml title as-is (no `review` key).

### 8. Push titles / descriptions / tags to YouTube (optional)

Requires a Google Cloud OAuth **Desktop** client with YouTube Data API v3
enabled. Save the client JSON as `~/.ssh/youtube_client_secret.json` (or set
`YOUTUBE_CLIENT_SECRETS`).

```bash
pip3 install -r /Users/csev/htdocs/media-util/requirements.txt
test-youtube-oauth.py                     # smoke-test OAuth + API
update-youtube-from-media-yaml.py              # dry-run diffs
update-youtube-from-media-yaml.py --apply      # write to YouTube
update-youtube-from-media-yaml.py --apply --limit 1
update-youtube-from-media-yaml.py --apply --only VIDEO_ID
```

Updates title, description, and tags from each `media.yaml` entry (including
any `EXTRA_*` content already baked in by bootstrap). If an entry has no tags,
existing YouTube tags are left alone. Stops immediately if the YouTube API
quota is exceeded. First OAuth run opens a browser; the token is cached at
`$YOUTUBE_DIR/youtube-oauth-token.json`.

## Course layout

```
course-www/
  media.env                  # source this first
  lessons.json
  media.yaml                 # generated by bootstrap-media-yaml.py
  youtube/
    youtube-playlist.jsonl   # from dump-youtube-playlist.sh
    youtube-oauth-token.json # after first OAuth consent (gitignored)
  whisper/
    *whisper-vocabulary*     # optional, searched upward
    *whisper-replacements*   # optional cleanup rules (`incorrect => correct`)
    txt/...
    vtt/...
    srt/...
    desc/...                 # from whisper-desc (title / tags / description)
```

OAuth client secret (not in the course tree):

```text
~/.ssh/youtube_client_secret.json
```

Media binaries usually live outside the www tree, for example:

```
~/Desktop/teach/dj4e-media/lesson-02-http/...
```

## Commands

| Command | Purpose |
|---|---|
| `dump-youtube-playlist.sh` | Dump playlist metadata to JSONL |
| `test-youtube-oauth.py` | Smoke-test OAuth client + YouTube API access |
| `update-youtube-from-media-yaml.py` | Push `media.yaml` titles, descriptions, and tags to YouTube |
| `update-lessons-from-media-yaml.py` | Copy `media.yaml` titles into `lessons.json` (Review stays in lessons) |
| `compare-lessons-root.py` | Diff `lessons.json` vs `MEDIA_ROOT` |
| `compare-lessons-youtube.py` | Diff `lessons.json` vs YouTube playlist JSONL |
| `compare-whisper-root.py` | Diff whisper artifacts vs `MEDIA_ROOT` (`--remove` orphans) |
| `whisper-media.sh` | Recursively transcribe all media under `MEDIA_ROOT` |
| `whisper-lessons` | Transcribe only media paths listed in `lessons.json` |
| `whisper-desc` | Generate title/tags/description via Ollama |
| `whisper-cleanup.py` | Apply `*whisper-replacements*` (`incorrect => correct`) to txt/vtt/srt |
| `bootstrap-media-yaml.py` | Build/refresh `media.yaml` (AI titles/tags/descriptions) |
| `whisper-one.sh` | Transcribe a single media file next to itself |
| `whisper-folder.sh` | Transcribe top-level media in the current folder |
| `compare-media-root.py` | Diff `MEDIA_ROOT` vs `media.yaml` (after bootstrap) |
| `compare-youtube.py` | Diff playlist JSONL vs `media.yaml` (after bootstrap) |
| `compare-lessons.py` | Diff `lessons.json` vs `media.yaml` (after bootstrap) |

## Environment reference

| Variable | Used by | Meaning |
|---|---|---|
| `MEDIA_UTIL` | `media.env` | Path to this repo; its `bin/` is prepended to `PATH` |
| `COURSE_ROOT` | `media.env` | Course www root (pwd check) |
| `MEDIA_ROOT` | whisper / compare / bootstrap | Media binary tree |
| `WHISPER_ROOT` | whisper tools / `whisper-desc` / cleanup | Whisper output tree |
| `YOUTUBE_DIR` | dump / bootstrap / YouTube update | Course `youtube/` folder |
| `YOUTUBE_PLAYLIST` | `dump-youtube-playlist.sh` | Course playlist URL |
| `YOUTUBE_PLAYLIST_JSONL` | dump / bootstrap | Optional override for the JSONL path |
| `YOUTUBE_CLIENT_SECRETS` | OAuth tools | OAuth client JSON (default `~/.ssh/youtube_client_secret.json`) |
| `YOUTUBE_TOKEN` | OAuth tools | OAuth token cache path |
| `COURSE_HINT` | `whisper-media.sh`, `whisper-folder.sh` | Prompt context for Whisper |
| `EXTRA_TAGS` | bootstrap | Appended to each entry's tags; also stored as `extra_tags` |
| `EXTRA_DESCRIPTION` | bootstrap | Appended to each entry's description; also `extra_description` |
| `MODEL` / `WHISPER_MODEL` | whisper scripts | ggml model path |
| `OLLAMA_MODEL` / `OLLAMA_HOST` | `whisper-desc` | Local LLM for descriptions |
| `CLEANUP_PY` | whisper scripts | Override path to cleanup tool |
| `MEDIA_YAML` | bootstrap / update / sync | Optional override for `media.yaml` |
| `LESSONS_JSON` | compare / sync | Optional override for `lessons.json` |
