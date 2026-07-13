# media-util

Shared tooling for lecture media across teaching sites (DJ4E, PY4E, CC4E, etc.).

**Day-to-day use:** source the course `media.env`, then run the workflow below.
Course-specific data (playlist dumps, vocabulary, `lessons.json`, `media.yaml`)
stays in each course repository. Shared scripts live here.

## Prerequisites

See [INSTALL.md](INSTALL.md) for Mac install steps for:

- `ffmpeg` / `ffprobe`
- `yt-dlp`
- `whisper-cli` (whisper.cpp) + ggml model
- Ollama + a local model (default `qwen3:4b`)
- Python `ruamel.yaml` (`pip3 install -r requirements.txt`)

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
export OUTPUT_ROOT=/Users/csev/htdocs/dj4e/whisper
export YOUTUBE_DIR=/Users/csev/htdocs/dj4e/youtube
export YOUTUBE_PLAYLIST='https://www.youtube.com/playlist?list=PLlRFEj9H3Oj5e-EH0t3kXrcdygrL9-u-Z'
export COURSE_HINT='Django for Everybody, DJ4E, Django, Python, web development, Dr. Chuck, Chuck Severance'
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

```bash
whisper-media.sh
```

Scans `MEDIA_ROOT` and writes transcripts under `$OUTPUT_ROOT` (`txt/`, `vtt/`,
`srt/`). Existing transcripts are skipped unless you pass `--force`.

### 5. Generate titles, tags, and descriptions with Ollama

Start Ollama if it is not already running, then:

```bash
ollama serve   # if needed
cd whisper
whisper-desc
```

Writes `desc/...` with the same relative names as `txt/...`:

```
title

tag1, tag2, tag3

two paragraph description
```

Defaults: model `qwen3:4b` at `http://localhost:11434`.
Use `--force` to overwrite existing `desc/` files.

### 6. Build `media.yaml`

```bash
cd /Users/csev/htdocs/dj4e
bootstrap-media-yaml.py
```

Builds / refreshes `media.yaml` from:

- `MEDIA_ROOT` (inventory: every `.mov` / `.mp4` / `.m4v`)
- `lessons.json` (titles and youtube ids when present)
- `youtube/youtube-playlist.jsonl` (youtube id / description matching)

Also records `size`, `md5`, and `duration` from disk. Existing
`youtube_id` / `description` are filled when empty (`--force-youtube` to
overwrite). `kaltura_id` is preserved.

## Course layout

```
course-www/
  media.env                  # source this first
  lessons.json
  media.yaml                 # generated last by bootstrap-media-yaml.py
  youtube/
    youtube-playlist.jsonl   # from dump-youtube-playlist.sh
  whisper/
    whisper-vocabulary.txt   # optional, searched upward
    whisper-replacements.txt # optional cleanup rules
    txt/...
    vtt/...
    srt/...
    desc/...                 # from whisper-desc
```

Media binaries usually live outside the www tree, for example:

```
~/Desktop/teach/dj4e-media/lesson-02-http/...
```

## Commands

| Command | Purpose |
|---|---|
| `dump-youtube-playlist.sh` | Dump playlist metadata to JSONL |
| `compare-lessons-root.py` | Diff `lessons.json` vs `MEDIA_ROOT` |
| `compare-whisper-root.py` | Diff whisper artifacts vs `MEDIA_ROOT` (`--remove` orphans) |
| `whisper-media.sh` | Recursively transcribe `MEDIA_ROOT` into whisper/ |
| `whisper-desc` | Generate title/tags/description via Ollama |
| `bootstrap-media-yaml.py` | Build/refresh `media.yaml` |
| `whisper-one.sh` | Transcribe a single media file next to itself |
| `whisper-folder.sh` | Transcribe top-level media in the current folder |
| `whisper-cleanup.py` | Apply vocabulary replacements to txt/vtt/srt |
| `compare-media-root.py` | Diff `MEDIA_ROOT` vs `media.yaml` (after bootstrap) |
| `compare-youtube.py` | Diff playlist JSONL vs `media.yaml` (after bootstrap) |
| `compare-lessons.py` | Diff `lessons.json` vs `media.yaml` (after bootstrap) |

## Environment reference

| Variable | Used by | Meaning |
|---|---|---|
| `MEDIA_UTIL` | `media.env` | Path to this repo; its `bin/` is prepended to `PATH` |
| `COURSE_ROOT` | `media.env` | Course www root (pwd check) |
| `MEDIA_ROOT` | whisper / compare / bootstrap | Media binary tree |
| `OUTPUT_ROOT` | whisper tools | Whisper output tree |
| `YOUTUBE_DIR` | dump / bootstrap | Course `youtube/` folder |
| `YOUTUBE_PLAYLIST` | `dump-youtube-playlist.sh` | Course playlist URL |
| `YOUTUBE_PLAYLIST_JSONL` | dump / bootstrap | Optional override for the JSONL path |
| `COURSE_HINT` | `whisper-media.sh`, `whisper-folder.sh` | Prompt context for Whisper |
| `MODEL` / `WHISPER_MODEL` | whisper scripts | ggml model path |
| `OLLAMA_MODEL` / `OLLAMA_HOST` | `whisper-desc` | Local LLM for descriptions |
| `CLEANUP_PY` | whisper scripts | Override path to cleanup tool |
