# media-util

Shared tooling for lecture media across teaching sites (DJ4E, PY4E, CC4E, etc.).

Install once, point `~/bin` at this repo, then run the tools from any course
tree. Course-specific data (media file lists, YouTube playlist dumps,
vocabulary files) stays in each course repository.

## Install

```bash
cd /Users/csev/htdocs/media-util
./bin/install-symlinks.sh
```

That creates symlinks in `~/bin` for:

| Command | Purpose |
|---|---|
| `whisper-one.sh` | Transcribe a single media file next to itself |
| `whisper-folder.sh` | Transcribe top-level media in the current folder |
| `whisper-media.sh` | Recursively transcribe a media tree into a whisper/ tree |
| `whisper-cleanup.py` | Apply vocabulary replacements to txt/vtt/srt |
| `whisper-desc` | Generate YouTube title/tags/description via Ollama |
| `bootstrap-media-yaml.py` | Build/refresh `media.yaml` for a course site |
| `dump-youtube-playlist.sh` | Dump playlist metadata to JSONL for matching |

Python dependency for YAML bootstrap:

```bash
pip3 install -r requirements.txt
```

Also needed on PATH: `ffmpeg`, `ffprobe`, `whisper-cli`, `yt-dlp` (for playlist dumps), and a running [Ollama](https://ollama.com) server for `whisper-desc`.

## Course layout

Typical course repository:

```
course-www/
  lessons.json
  media.yaml                 # generated
  media/
    media-files.txt          # list of relative media paths
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
whisper-one.sh lecture.m4v
```

### Current folder (flat)

```bash
cd /path/to/folder-with-media
export COURSE_HINT='Django for Everybody, DJ4E, Django, Python, web development, Dr. Chuck, Chuck Severance'
whisper-folder.sh
```

### Media tree → whisper tree

```bash
export MEDIA_ROOT=/Users/csev/Desktop/teach/dj4e-media
export OUTPUT_ROOT=/Users/csev/htdocs/dj4e/whisper
export COURSE_HINT='Django for Everybody, DJ4E, Django, Python, web development, Dr. Chuck, Chuck Severance'
whisper-media.sh
```

Use `--force` to re-transcribe existing files. Vocabulary is loaded from any
`*whisper-vocabulary*` file found walking upward from `OUTPUT_ROOT` /
`MEDIA_ROOT` (or `$HOME`). Optional `whisper-replacements.txt` drives cleanup.

Example env files live in `examples/`.

## YouTube descriptions from transcripts

From a whisper folder that already has `txt/...`:

```bash
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

## Build / refresh media.yaml

From the course www root:

```bash
cd /Users/csev/htdocs/dj4e

dump-youtube-playlist.sh \
  'https://www.youtube.com/playlist?list=PLlRFEj9H3Oj5e-EH0t3kXrcdygrL9-u-Z' \
  > media/youtube-playlist.jsonl

export MEDIA_ROOT=/Users/csev/Desktop/teach/dj4e-media
bootstrap-media-yaml.py
```

Defaults (all relative to the current working directory):

- `--lessons` → `./lessons.json`
- `--files` → `./media/media-files.txt`
- `--youtube-playlist` → `./media/youtube-playlist.jsonl`
- `--output` → `./media.yaml`
- `--www-root` → `.`
- `--media-root` → required, or set `MEDIA_ROOT`

On each run the script refreshes `size`, `md5`, `duration`, and lesson
`title`. YouTube `youtube_id` / `description` are filled when empty (use
`--force-youtube` to overwrite). `kaltura_id` is always preserved.

## Environment reference

| Variable | Used by | Meaning |
|---|---|---|
| `MEDIA_ROOT` | `whisper-media.sh`, `bootstrap-media-yaml.py` | Media binary tree |
| `OUTPUT_ROOT` | `whisper-media.sh` | Whisper output tree |
| `COURSE_HINT` | `whisper-media.sh`, `whisper-folder.sh` | Prompt context for Whisper |
| `MODEL` / `WHISPER_MODEL` | whisper scripts | ggml model path |
| `OLLAMA_MODEL` / `OLLAMA_HOST` | `whisper-desc` | Local LLM for descriptions |
| `CLEANUP_PY` | whisper scripts | Override path to cleanup tool |

## Per-course examples

See `examples/dj4e.env` for a ready-to-source DJ4E setup.
