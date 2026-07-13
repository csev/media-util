#!/bin/bash

# Recursively transcribe a media tree into a Git-managed whisper tree.
# macOS Bash 3.2 friendly. Course-agnostic — set MEDIA_ROOT and WHISPER_ROOT.
#
# A media file such as:
#   lesson-02-http/07-Web-04-Mini-Django.m4v
#
# produces:
#   txt/lesson-02-http/07-Web-04-Mini-Django.txt
#   vtt/lesson-02-http/07-Web-04-Mini-Django.vtt
#   srt/lesson-02-http/07-Web-04-Mini-Django.srt
#
# Atomic publish behavior:
#   Each finished transcript set is written under .whisper-tmp and then moved
#   into txt/vtt/srt immediately, so an abort mid-batch keeps completed work.
#   A failure on one file does not discard transcripts already published.

set -u

FORCE=0
QUIET_WHISPER="${QUIET_WHISPER:-1}"
SUCCESS=0
FROM_LESSONS=0
LESSONS_JSON="${LESSONS_JSON:-}"

# Resolve through symlinks so ~/bin wrappers find sibling tools in this repo.
SOURCE="$0"
while [ -L "$SOURCE" ]; do
    TARGET="$(readlink "$SOURCE")"
    case "$TARGET" in
        /*) SOURCE="$TARGET" ;;
        *)  SOURCE="$(dirname "$SOURCE")/$TARGET" ;;
    esac
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd -P)"

MEDIA_ROOT="${MEDIA_ROOT:-}"
WHISPER_ROOT="${WHISPER_ROOT:-}"
WHISPER="${WHISPER:-whisper-cli}"
MODEL="${MODEL:-$HOME/models/ggml-medium.bin}"
CLEANUP_PY="${CLEANUP_PY:-$SCRIPT_DIR/whisper-cleanup.py}"
COURSE_HINT="${COURSE_HINT:-Dr. Chuck, Chuck Severance}"

TXT_DIR=""
VTT_DIR=""
SRT_DIR=""
TMP_DIR=""
LOG_FILE=""
RUN_TMP_DIR=""
STAGE_ROOT=""
STAGE_TXT_DIR=""
STAGE_VTT_DIR=""
STAGE_SRT_DIR=""
MEDIA_LIST=""

usage() {
    cat <<EOF_USAGE
Usage: $(basename "$0") [--force|-f] [--verbose|-v] [--from-lessons] [--lessons PATH]

Scans MEDIA_ROOT (default) or media paths from lessons.json (--from-lessons)
and writes transcripts under WHISPER_ROOT.

Required environment:
  MEDIA_ROOT     Directory tree of .mov/.mp4/.m4v files
  WHISPER_ROOT   Whisper folder (receives txt/, vtt/, srt/)

Options:
  --force, -f       Re-run Whisper even if a transcript already exists.
  --verbose, -v     Keep whisper-cli transcript output in the log.
  --from-lessons    Transcribe only media paths listed in lessons.json
  --lessons PATH    lessons.json path (implies --from-lessons)
                    (default: \$LESSONS_JSON or \$COURSE_ROOT/lessons.json)
  --help, -h        Show this help.

Optional environment:
  COURSE_HINT    Course/context hint for the Whisper prompt
                 (default: "Dr. Chuck, Chuck Severance")
  WHISPER, MODEL, LOG_FILE, QUIET_WHISPER, CLEANUP_PY, LESSONS_JSON
EOF_USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --force|-f)
            FORCE=1
            shift
            ;;
        --verbose|-v)
            QUIET_WHISPER=0
            shift
            ;;
        --from-lessons)
            FROM_LESSONS=1
            shift
            ;;
        --lessons)
            if [ -z "${2:-}" ]; then
                echo "ERROR: --lessons requires a path" >&2
                usage >&2
                exit 1
            fi
            LESSONS_JSON="$2"
            FROM_LESSONS=1
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

cleanup_run() {
    if [ -n "$MEDIA_LIST" ] && [ -f "$MEDIA_LIST" ]; then
        rm -f "$MEDIA_LIST"
    fi

    if [ -n "$RUN_TMP_DIR" ] && [ -d "$RUN_TMP_DIR" ]; then
        rm -rf "$RUN_TMP_DIR"
    fi
}

on_exit() {
    status=$?
    cleanup_run
    exit "$status"
}

trap 'on_exit' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

log() {
    echo "$*" | tee -a "$LOG_FILE"
}

format_time() {
    seconds="$1"
    printf "%02d:%02d:%02d" \
        $((seconds/3600)) \
        $(((seconds%3600)/60)) \
        $((seconds%60))
}

find_upward_file_from() {
    target="$1"
    dir="$2"

    while [ "$dir" != "/" ]; do
        if [ -f "$dir/$target" ]; then
            printf "%s\n" "$dir/$target"
            return 0
        fi
        dir="$(dirname "$dir")"
    done

    return 1
}

find_config_file() {
    target="$1"

    if find_upward_file_from "$target" "$WHISPER_ROOT"; then
        return 0
    fi

    if find_upward_file_from "$target" "$MEDIA_ROOT"; then
        return 0
    fi

    if [ -f "$HOME/$target" ]; then
        printf "%s\n" "$HOME/$target"
        return 0
    fi

    return 1
}

find_matching_files_upward_from() {
    pattern="$1"
    dir="$2"

    while [ "$dir" != "/" ]; do
        matches="$(find "$dir" -maxdepth 1 -type f -name "$pattern" -print | LC_ALL=C sort)"
        if [ -n "$matches" ]; then
            printf "%s\n" "$matches"
            return 0
        fi
        dir="$(dirname "$dir")"
    done

    return 1
}

find_vocab_files() {
    # Prefer vocabulary files associated with the Git-managed output tree,
    # then the media tree, then the home directory. At the first directory
    # containing matches, use every file whose name contains
    # "whisper-vocabulary", sorted by filename.
    if find_matching_files_upward_from "*whisper-vocabulary*" "$WHISPER_ROOT"; then
        return 0
    fi

    if find_matching_files_upward_from "*whisper-vocabulary*" "$MEDIA_ROOT"; then
        return 0
    fi

    matches="$(find "$HOME" -maxdepth 1 -type f -name "*whisper-vocabulary*" -print | LC_ALL=C sort)"
    if [ -n "$matches" ]; then
        printf "%s\n" "$matches"
        return 0
    fi

    return 1
}

build_prompt() {
    printf "%s\n" "$VOCAB_FILES" |
    while IFS= read -r vocab_file
    do
        [ -n "$vocab_file" ] || continue
        cat "$vocab_file"
    done |
    awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        {
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
            if (length($0) > 0) {
                if (out != "") out = out ", "
                out = out $0
            }
        }
        END { print out }
    '
}

safe_text_for_prompt() {
    printf "%s" "$1" | sed 's/[^A-Za-z0-9]/ /g; s/[ ][ ]*/ /g; s/^ //; s/ $//'
}

cleanup_temp_file() {
    temp_file="$1"
    if [ -n "$temp_file" ] && [ -f "$temp_file" ]; then
        rm -f "$temp_file"
    fi
}

extract_audio() {
    media_file="$1"
    temp_wav="$2"
    err_file="$3"

    rm -f "$err_file"
    ffmpeg -y -v error -i "$media_file" -ar 16000 -ac 1 -c:a pcm_s16le "$temp_wav" 2>"$err_file"
    return $?
}

output_paths() {
    relative_media="$1"
    relative_stem="${relative_media%.*}"

    TXT_FILE="$TXT_DIR/$relative_stem.txt"
    VTT_FILE="$VTT_DIR/$relative_stem.vtt"
    SRT_FILE="$SRT_DIR/$relative_stem.srt"

    STAGE_TXT_FILE="$STAGE_TXT_DIR/$relative_stem.txt"
    STAGE_VTT_FILE="$STAGE_VTT_DIR/$relative_stem.vtt"
    STAGE_SRT_FILE="$STAGE_SRT_DIR/$relative_stem.srt"
}

already_done() {
    relative_media="$1"

    if [ "$FORCE" -eq 1 ]; then
        return 1
    fi

    output_paths "$relative_media"

    if [ -f "$TXT_FILE" ] && [ -f "$VTT_FILE" ] && [ -f "$SRT_FILE" ]; then
        return 0
    fi

    return 1
}

apply_replacements() {
    transcript_file="$1"

    if [ -z "${REPLACEMENTS_FILE:-}" ] || [ ! -f "$REPLACEMENTS_FILE" ]; then
        return 0
    fi

    if [ ! -f "$CLEANUP_PY" ]; then
        log "NOTE: whisper-cleanup.py not found, skipping cleanup"
        return 0
    fi

    if [ ! -s "$transcript_file" ]; then
        log "WARNING: not cleaning missing or empty transcript: $transcript_file"
        return 1
    fi

    log "CLEANUP: $transcript_file"

    if ! python3 "$CLEANUP_PY" \
        --replacements "$REPLACEMENTS_FILE" \
        "$transcript_file" >>"$LOG_FILE" 2>&1; then
        log "WARNING: cleanup failed for $transcript_file"
        return 1
    fi

    return 0
}

process_file() {
    relative_media="$1"
    media_file="$MEDIA_ROOT/$relative_media"
    filename="$(basename "$relative_media")"
    relative_stem="${relative_media%.*}"

    output_paths "$relative_media"

    temp_key="$(printf "%s" "$relative_stem" | sed 's#/#__#g; s/[^A-Za-z0-9._-]/_/g')"
    file_tmp_dir="$RUN_TMP_DIR/work-$temp_key"
    out_base="$file_tmp_dir/output"
    temp_wav="$file_tmp_dir/audio.wav"
    err_file="$file_tmp_dir/ffmpeg-error.log"

    file_hint="$(safe_text_for_prompt "$relative_stem")"
    prompt="${COURSE_HINT}, ${file_hint}. Technical vocabulary: ${VOCAB_PROMPT}"

    if [ ! -f "$media_file" ]; then
        log ""
        log "=================================================="
        log "MEDIA: $media_file"
        log "FAILED: input file does not exist"
        return 1
    fi

    if already_done "$relative_media"; then
        log "SKIP: $relative_media (found existing transcript)"
        return 0
    fi

    mkdir -p "$file_tmp_dir"
    mkdir -p "$(dirname "$STAGE_TXT_FILE")" "$(dirname "$STAGE_VTT_FILE")" "$(dirname "$STAGE_SRT_FILE")"

    log ""
    log "=================================================="
    log "MEDIA: $relative_media"
    log "OUTPUT STEM: $relative_stem"

    total_start=$(date +%s)

    rm -f "$err_file" "$out_base.txt" "$out_base.vtt" "$out_base.srt"

    log "STEP 1: Extracting audio with ffmpeg..."
    ffmpeg_start=$(date +%s)

    if ! extract_audio "$media_file" "$temp_wav" "$err_file"; then
        log "WARNING: ffmpeg failed first try, retrying once..."
        sleep 1
        cleanup_temp_file "$temp_wav"

        if ! extract_audio "$media_file" "$temp_wav" "$err_file"; then
            log "FAILED: ffmpeg could not extract audio from $relative_media"
            if [ -s "$err_file" ]; then
                log "FFMPEG ERROR:"
                sed 's/^/  /' "$err_file" | tee -a "$LOG_FILE"
            fi
            rm -rf "$file_tmp_dir"
            return 1
        fi
    fi

    ffmpeg_end=$(date +%s)
    ffmpeg_time=$((ffmpeg_end - ffmpeg_start))

    if [ ! -f "$temp_wav" ]; then
        log "FAILED: temp wav was not created: $temp_wav"
        rm -rf "$file_tmp_dir"
        return 1
    fi

    log "STEP 2: Running whisper-cli..."
    whisper_start=$(date +%s)

    if [ "$SUPPORTS_PROMPT" -eq 1 ]; then
        if [ "$QUIET_WHISPER" -eq 1 ]; then
            "$WHISPER" </dev/null \
                -m "$MODEL" -f "$temp_wav" -of "$out_base" \
                -otxt -ovtt -osrt --prompt "$prompt" \
                >/dev/null 2>>"$LOG_FILE"
        else
            "$WHISPER" </dev/null \
                -m "$MODEL" -f "$temp_wav" -of "$out_base" \
                -otxt -ovtt -osrt --prompt "$prompt" \
                >>"$LOG_FILE" 2>&1
        fi
        whisper_status=$?
    else
        log "NOTE: whisper-cli does not support --prompt; running without prompt"
        if [ "$QUIET_WHISPER" -eq 1 ]; then
            "$WHISPER" </dev/null \
                -m "$MODEL" -f "$temp_wav" -of "$out_base" \
                -otxt -ovtt -osrt \
                >/dev/null 2>>"$LOG_FILE"
        else
            "$WHISPER" </dev/null \
                -m "$MODEL" -f "$temp_wav" -of "$out_base" \
                -otxt -ovtt -osrt \
                >>"$LOG_FILE" 2>&1
        fi
        whisper_status=$?
    fi

    if [ "$whisper_status" -ne 0 ]; then
        log "FAILED: whisper-cli transcription failed for $relative_media"
        rm -rf "$file_tmp_dir"
        return 1
    fi

    whisper_end=$(date +%s)
    whisper_time=$((whisper_end - whisper_start))

    if [ ! -f "$out_base.txt" ] || [ ! -f "$out_base.vtt" ] || [ ! -f "$out_base.srt" ]; then
        log "FAILED: whisper-cli did not produce the full transcript set for $relative_media"
        rm -rf "$file_tmp_dir"
        return 1
    fi

    apply_replacements "$out_base.txt" || true
    apply_replacements "$out_base.vtt" || true
    apply_replacements "$out_base.srt" || true

    mv -f "$out_base.txt" "$STAGE_TXT_FILE"
    mv -f "$out_base.vtt" "$STAGE_VTT_FILE"
    mv -f "$out_base.srt" "$STAGE_SRT_FILE"

    rm -rf "$file_tmp_dir"

    total_end=$(date +%s)
    total_time=$((total_end - total_start))

    if [ ! -f "$STAGE_TXT_FILE" ] || [ ! -f "$STAGE_VTT_FILE" ] || [ ! -f "$STAGE_SRT_FILE" ]; then
        log "WARNING: staging finished but one or more staged transcript files were not found for $relative_media"
        return 1
    fi

    mkdir -p "$(dirname "$TXT_FILE")" "$(dirname "$VTT_FILE")" "$(dirname "$SRT_FILE")"
    mv -f "$STAGE_TXT_FILE" "$TXT_FILE"
    mv -f "$STAGE_VTT_FILE" "$VTT_FILE"
    mv -f "$STAGE_SRT_FILE" "$SRT_FILE"

    log "DONE: $relative_media"
    log "TIMING:"
    log "  FFMPEG : $(format_time "$ffmpeg_time")"
    log "  WHISPER: $(format_time "$whisper_time")"
    log "  TOTAL  : $(format_time "$total_time")"
    return 0
}

[ -n "$MEDIA_ROOT" ] || fail "MEDIA_ROOT is required (directory of .mov/.mp4/.m4v files)"
[ -n "$WHISPER_ROOT" ] || fail "WHISPER_ROOT is required (whisper folder for txt/vtt/srt)"
[ -d "$MEDIA_ROOT" ] || fail "Media root not found: $MEDIA_ROOT"

MEDIA_ROOT="$(cd "$MEDIA_ROOT" && pwd -P)"
mkdir -p "$WHISPER_ROOT"
WHISPER_ROOT="$(cd "$WHISPER_ROOT" && pwd -P)"

TXT_DIR="$WHISPER_ROOT/txt"
VTT_DIR="$WHISPER_ROOT/vtt"
SRT_DIR="$WHISPER_ROOT/srt"
TMP_DIR="$WHISPER_ROOT/.whisper-tmp"
LOG_FILE="${LOG_FILE:-$WHISPER_ROOT/whisper-batch.log}"

mkdir -p "$TXT_DIR" "$VTT_DIR" "$SRT_DIR" "$TMP_DIR"

RUN_TMP_DIR="$TMP_DIR/run-$$"
STAGE_ROOT="$RUN_TMP_DIR/publish"
STAGE_TXT_DIR="$STAGE_ROOT/txt"
STAGE_VTT_DIR="$STAGE_ROOT/vtt"
STAGE_SRT_DIR="$STAGE_ROOT/srt"
mkdir -p "$RUN_TMP_DIR" "$STAGE_TXT_DIR" "$STAGE_VTT_DIR" "$STAGE_SRT_DIR"

if ! command -v "$WHISPER" >/dev/null 2>&1; then
    fail "Cannot find '$WHISPER' in PATH"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    fail "Cannot find 'ffmpeg' in PATH"
fi

if [ ! -f "$MODEL" ]; then
    fail "Model not found: $MODEL"
fi

VOCAB_FILES="$(find_vocab_files)" || \
    fail "No vocabulary files containing 'whisper-vocabulary' were found while walking upward from $WHISPER_ROOT or $MEDIA_ROOT"
REPLACEMENTS_FILE="$(find_config_file "whisper-replacements.txt" || true)"
VOCAB_PROMPT="$(build_prompt)"

SUPPORTS_PROMPT=0
if "$WHISPER" --help 2>&1 | grep -q -- "--prompt"; then
    SUPPORTS_PROMPT=1
fi

MEDIA_LIST="$RUN_TMP_DIR/media-files.$$"
if [ "$FROM_LESSONS" -eq 1 ]; then
    if [ -z "$LESSONS_JSON" ]; then
        if [ -n "${COURSE_ROOT:-}" ] && [ -f "$COURSE_ROOT/lessons.json" ]; then
            LESSONS_JSON="$COURSE_ROOT/lessons.json"
        else
            LESSONS_JSON="$(pwd -P)/lessons.json"
        fi
    fi
    [ -f "$LESSONS_JSON" ] || fail "lessons.json not found: $LESSONS_JSON"

    python3 - "$LESSONS_JSON" "$MEDIA_LIST" <<'PY' || fail "Could not read media paths from lessons.json"
import json
import sys
from pathlib import Path

lessons_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
data = json.loads(lessons_path.read_text(encoding="utf-8"))
modules = data.get("modules")
if not isinstance(modules, list):
    raise SystemExit("lessons.json: expected top-level modules list")

seen = set()
paths = []
for module in modules:
    if not isinstance(module, dict):
        continue
    items = module.get("items") or []
    if not isinstance(items, list):
        continue
    for item in items:
        if not isinstance(item, dict):
            continue
        media = item.get("media")
        if not isinstance(media, str) or not media.strip():
            continue
        media = media.strip().lstrip("./")
        # Old lessons sometimes stored "dj4e-media/lesson-..."
        if media.startswith("dj4e-media/"):
            media = media[len("dj4e-media/") :]
        if media in seen:
            continue
        seen.add(media)
        paths.append(media)

paths.sort()
out_path.write_text("\n".join(paths) + ("\n" if paths else ""), encoding="utf-8")
print(f"lessons media paths: {len(paths)}", file=sys.stderr)
PY
else
    (
        cd "$MEDIA_ROOT" || exit 1
        find . -type f \( -iname "*.mov" -o -iname "*.mp4" -o -iname "*.m4v" \) -print |
            sed 's#^\./##' |
            LC_ALL=C sort
    ) > "$MEDIA_LIST" || fail "Could not build media list"
fi

: > "$LOG_FILE"

log "Batch started: $(date)"
log "MEDIA_ROOT=$MEDIA_ROOT"
log "WHISPER_ROOT=$WHISPER_ROOT"
log "COURSE_HINT=$COURSE_HINT"
log "FROM_LESSONS=$FROM_LESSONS"
if [ "$FROM_LESSONS" -eq 1 ]; then
    log "LESSONS_JSON=$LESSONS_JSON"
fi
log "WHISPER=$WHISPER"
log "MODEL=$MODEL"
log "VOCAB_FILES:"
printf "%s\n" "$VOCAB_FILES" | sed 's/^/  /' | tee -a "$LOG_FILE"
log "REPLACEMENTS_FILE=${REPLACEMENTS_FILE:-none}"
log "LOG_FILE=$LOG_FILE"
log "TXT_DIR=$TXT_DIR"
log "VTT_DIR=$VTT_DIR"
log "SRT_DIR=$SRT_DIR"
log "TMP_DIR=$TMP_DIR"
log "RUN_TMP_DIR=$RUN_TMP_DIR"
log "SUPPORTS_PROMPT=$SUPPORTS_PROMPT"
log "FORCE=$FORCE"
log "QUIET_WHISPER=$QUIET_WHISPER"
log "=================================================="

TOTAL=0
DONE_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

while IFS= read -r relative_media <&3
do
    [ -n "$relative_media" ] || continue
    TOTAL=$((TOTAL + 1))

    if already_done "$relative_media"; then
        log "SKIP: $relative_media (found existing transcript)"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        continue
    fi

    if process_file "$relative_media"; then
        DONE_COUNT=$((DONE_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done 3< "$MEDIA_LIST"

log ""
log "=================================================="
log "Batch finished: $(date)"
log "TOTAL=$TOTAL"
log "DONE=$DONE_COUNT"
log "SKIPPED=$SKIP_COUNT"
log "FAILED=$FAIL_COUNT"
log "LOG=$LOG_FILE"
log "=================================================="

if [ "$FAIL_COUNT" -ne 0 ]; then
    log "NOTE: completed transcripts were kept; failed/missing ones can be retried."
    exit 1
fi

SUCCESS=1
exit 0
