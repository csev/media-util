# media-util

Shared tooling for lecture media across teaching sites (DJ4E, PY4E, CC4E, etc.).

**Day-to-day use:** source the course `media.env`, then run any command below.
Course-specific data (media file lists, YouTube playlist dumps, vocabulary)
stays in each course repository.

## Commands

| Command | Purpose |
|---|---|
| `whisper-one.sh` | Transcribe a single media file next to itself |
| `whisper-folder.sh` | Transcribe top-level media in the current folder |
| `whisper-media.sh` | Recursively transcribe a media tree into a whisper/ tree |
| `whisper-cleanup.py` | Apply vocabulary replacements to txt/vtt/srt |
| `whisper-desc` | Generate YouTube title/tags/description via Ollama |
| `bootstrap-media-yaml.py` | Build/refresh `media.yaml` for a course site |
| `dump-youtube-playlist.sh` | Dump playlist metadata to JSONL for matching |
| `compare-lessons-root.py` | Diff `lessons.json` vs `MEDIA_ROOT` (first step; no media.yaml) |
| `compare-media-root.py` | Diff `MEDIA_ROOT` vs `media.yaml` |
| `compare-youtube.py` | Diff `youtube-playlist.jsonl` vs `media.yaml` |
| `compare-lessons.py` | Diff `lessons.json` vs `media.yaml` |

## Per-course setup

Each course keeps a `media.env` that puts this repo's `bin/` on `PATH` and
sets course paths. Example (`/Users/csev/htdocs/dj4e/media.env`):

```bash
export MEDIA_UTIL=/Users/csev/htdocs/media-util
case ":$PATH:" in
  *":$MEDIA_UTIL/bin:"*) ;;
  *) export PATH="$MEDIA_UTIL/bin:$PATH" ;;
esac

export MEDIA_ROOT=/Users/csev/Desktop/teach/dj4e-media
export OUTPUT_ROOT=/Users/csev/htdocs/dj4e/whisper
export YOUTUBE_DIR=/Users/csev/htdocs/dj4e/youtube
export YOUTUBE_PLAYLIST='https://www.youtube.com/playlist?list=PLlRFEj9H3Oj5e-EH0t3kXrcdygrL9-u-Z'
export COURSE_HINT='Django for Everybody, DJ4E, Django, Python, web development, Dr. Chuck, Chuck Severance'
```

Then:

```bash
source /Users/csev/htdocs/dj4e/media.env
cd /Users/csev/htdocs/dj4e

compare-lessons-root.py
dump-youtube-playlist.sh
bootstrap-media-yaml.py
whisper-media.sh
( cd whisper && whisper-desc )
```

Optional: `./bin/install-symlinks.sh` also links the tools into `~/bin` so
they are available even without sourcing `media.env`.

Python dependency for YAML bootstrap:

```bash
pip3 install -r requirements.txt
```

Also needed on PATH: `ffmpeg`, `ffprobe`, `whisper-cli`, `yt-dlp` (for playlist dumps), and a running [Ollama](https://ollama.com) server for `whisper-desc`.

## Course layout

Typical course repository:

```
course-www/
  media.env                  # source this first (sets MEDIA_ROOT)
  lessons.json
  media.yaml                 # generated from MEDIA_ROOT scan
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

Media binaries themselves usually live outside the www tree, for example:

```
~/Desktop/teach/dj4e-media/lesson-02-http/...
```

## Whisper transcription

### One file

```bash
source /path/to/course/media.env
whisper-one.sh lecture.m4v
```

### Current folder (flat)

```bash
source /path/to/course/media.env
cd /path/to/folder-with-media
whisper-folder.sh
```

### Media tree → whisper tree

```bash
source /Users/csev/htdocs/dj4e/media.env
whisper-media.sh
```

Use `--force` to re-transcribe existing files. Vocabulary is loaded from any
`*whisper-vocabulary*` file found walking upward from `OUTPUT_ROOT` /
`MEDIA_ROOT` (or `$HOME`). Optional `whisper-replacements.txt` drives cleanup.

## YouTube descriptions from transcripts

From a whisper folder that already has `txt/...`:

```bash
source /Users/csev/htdocs/dj4e/media.env
cd /Users/csev/htdocs/dj4e/whisper
whisper-desc
```

Writes `desc/...` with the same relative names:

```
title

tag1, tag2, tag3

two paragraph description
```

Defaults: Ollama model `qwen3:4b` at `http://localhost:11434`.
Override with `--model`, `--host`, `OLLAMA_MODEL`, or `OLLAMA_HOST`.
Skip existing files unless you pass `--force`.

## Consistency checks

After sourcing `media.env`:

```bash
# First step before building media.yaml — lessons vs files on disk
compare-lessons-root.py    # lessons.json <-> MEDIA_ROOT

# After media.yaml exists
compare-media-root.py      # MEDIA_ROOT <-> media.yaml
compare-youtube.py         # youtube-playlist.jsonl <-> media.yaml
compare-lessons.py         # lessons.json <-> media.yaml
```

Exit status is `0` when clean, `1` when inconsistencies are reported.
`compare-media-root.py --check-meta` also verifies size/md5 for shared files.

## Build / refresh media.yaml

From the course www root:

```bash
source /Users/csev/htdocs/dj4e/media.env
cd /Users/csev/htdocs/dj4e

dump-youtube-playlist.sh

bootstrap-media-yaml.py
```

Defaults (all relative to the current working directory):

- `--lessons` → `./lessons.json`
- `--media-root` / `MEDIA_ROOT` → scanned for `.mov` / `.mp4` / `.m4v` (source of truth)
- `--files` → optional explicit list (overrides the scan)
- `--youtube-playlist` → `$YOUTUBE_DIR/youtube-playlist.jsonl` (or `./youtube/...`)
- `--output` → `./media.yaml`
- `--www-root` → `.`

On each run the script refreshes `size`, `md5`, `duration`, and lesson
`title`. YouTube `youtube_id` / `description` are filled when empty (use
`--force-youtube` to overwrite). `kaltura_id` is always preserved.

## Environment reference

| Variable | Used by | Meaning |
|---|---|---|
| `MEDIA_UTIL` | `media.env` | Path to this repo; its `bin/` is prepended to `PATH` |
| `MEDIA_ROOT` | `whisper-media.sh`, `bootstrap-media-yaml.py` | Media binary tree |
| `OUTPUT_ROOT` | `whisper-media.sh` | Whisper output tree |
| `YOUTUBE_DIR` | `dump-youtube-playlist.sh`, `bootstrap-media-yaml.py` | Course `youtube/` folder |
| `YOUTUBE_PLAYLIST` | `dump-youtube-playlist.sh` | Course playlist URL |
| `YOUTUBE_PLAYLIST_JSONL` | dump/bootstrap | Optional override for the JSONL dump path |
| `COURSE_HINT` | `whisper-media.sh`, `whisper-folder.sh` | Prompt context for Whisper |
| `MODEL` / `WHISPER_MODEL` | whisper scripts | ggml model path |
| `OLLAMA_MODEL` / `OLLAMA_HOST` | `whisper-desc` | Local LLM for descriptions |
| `CLEANUP_PY` | whisper scripts | Override path to cleanup tool |
