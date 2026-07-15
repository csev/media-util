# Installing media-util dependencies (Mac)

These tools are required for the media-util workflow on macOS.
Homebrew is the recommended install path.

```bash
# Install Homebrew if needed: https://brew.sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## ffmpeg and ffprobe

`ffprobe` ships with the `ffmpeg` formula.

```bash
brew install ffmpeg
```

Verify:

```bash
ffmpeg -version
ffprobe -version
```

## yt-dlp

Used by `dump-youtube-playlist.sh` to download playlist metadata.

```bash
brew install yt-dlp
```

Verify:

```bash
yt-dlp --version
```

Keep it updated occasionally:

```bash
brew upgrade yt-dlp
```

## whisper-cli (whisper.cpp)

Transcription uses Homebrew's `whisper-cpp` package, which provides
`whisper-cli`.

```bash
brew install whisper-cpp
```

Verify:

```bash
whisper-cli --help
```

### Download a ggml model

`whisper-cli` needs a model file. media-util defaults to:

```text
$HOME/models/ggml-medium.bin
```

Create the directory and download a model (example: medium):

```bash
mkdir -p "$HOME/models"
curl -L -o "$HOME/models/ggml-medium.bin" \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin"
```

Other models: [ggerganov/whisper.cpp on Hugging Face](https://huggingface.co/ggerganov/whisper.cpp/tree/main)
or [ggml.ggerganov.com](https://ggml.ggerganov.com/).

Override the model path if needed:

```bash
export MODEL="$HOME/models/ggml-medium.bin"
# or
export WHISPER_MODEL="$HOME/models/ggml-medium.bin"
```

## Ollama

Used by `whisper-desc` to generate YouTube titles, tags, and descriptions.

### Install

Homebrew:

```bash
brew install ollama
```

Or install the Mac app from [https://ollama.com/download](https://ollama.com/download).

### Start the server and pull a model

```bash
# Leave this running (or use the Ollama Mac app)
ollama serve

# In another terminal:
ollama pull qwen3:4b
```

`whisper-desc` defaults to `qwen3:4b` at `http://localhost:11434`.

Verify:

```bash
curl -s http://localhost:11434/api/tags
ollama list
```

Override if needed:

```bash
export OLLAMA_MODEL=qwen3:4b
export OLLAMA_HOST=http://localhost:11434
```

## Python packages

`bootstrap-media-yaml.py` needs `ruamel.yaml`.
`update-youtube-from-media-yaml.py` also needs the Google API client libraries:

```bash
cd /Users/csev/htdocs/media-util
pip3 install -r requirements.txt
```

### YouTube OAuth (for update-youtube-from-media-yaml.py)

1. In [Google Cloud Console](https://console.cloud.google.com/), enable
   **YouTube Data API v3**.
2. Create an **OAuth client ID** of type **Desktop app**.
3. Download the JSON and save it as:

```text
~/.ssh/youtube_client_secret.json
```

   (or set `YOUTUBE_CLIENT_SECRETS` to the path).

4. Smoke-test the secret, then dry-run / apply:

```bash
source /Users/csev/htdocs/dj4e/media.env
cd /Users/csev/htdocs/dj4e
test-youtube-oauth.py
update-youtube-from-media-yaml.py
update-youtube-from-media-yaml.py --apply
```

`test-youtube-oauth.py` opens a browser on first run, caches the token at
`$YOUTUBE_DIR/youtube-oauth-token.json`, and verifies API access with the same
scope used by the updater. Use `--reauth` to force a new consent.

`update-youtube-from-media-yaml.py` pushes titles, descriptions, and tags from
`media.yaml`, and stops immediately if the YouTube API quota is exceeded.
## Quick check

```bash
command -v ffmpeg ffprobe yt-dlp whisper-cli ollama
test -f "$HOME/models/ggml-medium.bin" && echo "whisper model: ok"
curl -s http://localhost:11434/api/tags >/dev/null && echo "ollama: ok"
```

Then source a course `media.env` and follow the workflow in [README.md](README.md).
