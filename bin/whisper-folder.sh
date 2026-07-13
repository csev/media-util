#!/bin/bash

# Batch transcription script for the current folder
# macOS Bash 3.2 friendly
# Scans top-level .mov, .mp4, and .m4v files only.
# Writes transcripts to txt/, vtt/, and srt/.
# If whisper-cleanup.py and *whisper-replacements* exist, cleans txt/vtt/srt.

set -u

FORCE=0
QUIET_WHISPER="${QUIET_WHISPER:-1}"

usage() {
    cat <<EOF
Usage: $(basename "$0") [--force|-f]

Options:
  --force, -f   Re-run Whisper even if txt/vtt/srt outputs already exist.
  --verbose, -v  Keep whisper-cli transcript output in the log.
  --help, -h    Show this help.

Optional environment:
  COURSE_HINT   Course/context hint for the Whisper prompt
  WHISPER, MODEL, LOG_FILE, QUIET_WHISPER, CLEANUP_PY
EOF
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

SOURCE="$0"
while [ -L "$SOURCE" ]; do
    TARGET="$(readlink "$SOURCE")"
    case "$TARGET" in
        /*) SOURCE="$TARGET" ;;
        *)  SOURCE="$(dirname "$SOURCE")/$TARGET" ;;
    esac
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd -P)"

ROOT="$(pwd -P)"
WHISPER="${WHISPER:-whisper-cli}"
MODEL="${MODEL:-$HOME/models/ggml-medium.bin}"
CLEANUP_PY="${CLEANUP_PY:-$SCRIPT_DIR/whisper-cleanup.py}"
COURSE_HINT="${COURSE_HINT:-Dr. Chuck, Chuck Severance}"
LOG_FILE="${LOG_FILE:-$ROOT/whisper-batch.log}"
TXT_DIR="$ROOT/txt"
VTT_DIR="$ROOT/vtt"
SRT_DIR="$ROOT/srt"
TMP_DIR="$ROOT/.whisper-tmp"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

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

find_upward_file() {
    target="$1"
    dir="$ROOT"

    while [ "$dir" != "/" ]; do
        if [ -f "$dir/$target" ]; then
            printf "%s\n" "$dir/$target"
            return 0
        fi
        dir="$(dirname "$dir")"
    done

    if [ -f "$HOME/$target" ]; then
        printf "%s\n" "$HOME/$target"
        return 0
    fi

    return 1
}

find_vocab_file() {
    find_upward_file "whisper-vocabulary.txt"
}

find_matching_files_upward() {
    pattern="$1"
    dir="$ROOT"

    while [ "$dir" != "/" ]; do
        matches="$(find "$dir" -maxdepth 1 -type f -name "$pattern" -print | LC_ALL=C sort)"
        if [ -n "$matches" ]; then
            printf "%s\n" "$matches"
            return 0
        fi
        dir="$(dirname "$dir")"
    done

    matches="$(find "$HOME" -maxdepth 1 -type f -name "$pattern" -print | LC_ALL=C sort)"
    if [ -n "$matches" ]; then
        printf "%s\n" "$matches"
        return 0
    fi

    return 1
}

find_replacements_files() {
    find_matching_files_upward "*whisper-replacements*"
}

build_prompt() {
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
    ' "$VOCAB_FILE"
}

safe_stem_for_prompt() {
    # macOS-safe replacement for GNU-ish tr usage.
    # Turns separators into spaces for a friendlier Whisper prompt hint.
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

already_done() {
    stem="$1"

    if [ "$FORCE" -eq 1 ]; then
        return 1
    fi

    # Treat any existing transcript for this stem as enough to skip.
    # This preserves restart behavior and lets you delete a transcript set to rerun one file.
    if [ -f "$TXT_DIR/$stem.txt" ] || [ -f "$VTT_DIR/$stem.vtt" ] || [ -f "$SRT_DIR/$stem.srt" ]; then
        return 0
    fi

    return 1
}

apply_replacements() {
    transcript_file="$1"

    if [ -z "${REPLACEMENTS_FILES:-}" ]; then
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

    status=0
    while IFS= read -r replacements_file
    do
        [ -n "$replacements_file" ] || continue
        [ -f "$replacements_file" ] || continue
        if ! python3 "$CLEANUP_PY" \
            --replacements "$replacements_file" \
            "$transcript_file" >>"$LOG_FILE" 2>&1; then
            log "WARNING: cleanup failed for $transcript_file ($replacements_file)"
            status=1
        fi
    done <<EOF
$REPLACEMENTS_FILES
EOF

    return "$status"
}

process_file() {
    media_file="$1"
    filename="$(basename "$media_file")"
    stem="${filename%.*}"

    out_base="$TMP_DIR/$stem"
    temp_wav="$TMP_DIR/$stem.wav"
    err_file="$TMP_DIR/$stem.ffmpeg-error.log"

    file_hint="$(safe_stem_for_prompt "$stem")"
    prompt="${COURSE_HINT}, ${file_hint}. Technical vocabulary: ${VOCAB_PROMPT}"

    if [ ! -f "$media_file" ]; then
        log ""
        log "=================================================="
        log "MEDIA: $media_file"
        log "FAILED: input file does not exist"
        return 1
    fi

    if already_done "$stem"; then
        log "SKIP: $filename (found existing transcript)"
        return 0
    fi

    log ""
    log "=================================================="
    log "MEDIA: $media_file"
    log "TEMP WAV: $temp_wav"
    log "OUTPUT STEM: $stem"

    total_start=$(date +%s)

    cleanup_temp_file "$temp_wav"
    rm -f "$err_file" "$out_base.txt" "$out_base.vtt" "$out_base.srt"

    if [ "$FORCE" -eq 1 ]; then
        rm -f "$TXT_DIR/$stem.txt" "$TXT_DIR/$stem.txt.raw"
        rm -f "$VTT_DIR/$stem.vtt" "$VTT_DIR/$stem.vtt.raw"
        rm -f "$SRT_DIR/$stem.srt" "$SRT_DIR/$stem.srt.raw"
    fi

    log "STEP 1: Extracting audio with ffmpeg..."
    ffmpeg_start=$(date +%s)

    if ! extract_audio "$media_file" "$temp_wav" "$err_file"; then
        log "WARNING: ffmpeg failed first try, retrying once..."
        sleep 1
        cleanup_temp_file "$temp_wav"

        if ! extract_audio "$media_file" "$temp_wav" "$err_file"; then
            log "FAILED: ffmpeg could not extract audio from $media_file"
            if [ -s "$err_file" ]; then
                log "FFMPEG ERROR:"
                sed 's/^/  /' "$err_file" | tee -a "$LOG_FILE"
            fi
            cleanup_temp_file "$temp_wav"
            return 1
        fi
    fi

    ffmpeg_end=$(date +%s)
    ffmpeg_time=$((ffmpeg_end - ffmpeg_start))

    if [ ! -f "$temp_wav" ]; then
        log "FAILED: temp wav was not created: $temp_wav"
        return 1
    fi

    log "STEP 2: Running whisper-cli..."
    whisper_start=$(date +%s)

    if [ "$SUPPORTS_PROMPT" -eq 1 ]; then
        if [ "$QUIET_WHISPER" -eq 1 ]; then
            "$WHISPER" </dev/null \
                -m "$MODEL" \
                -f "$temp_wav" \
                -of "$out_base" \
                -otxt \
                -ovtt \
                -osrt \
                --prompt "$prompt" >/dev/null 2>>"$LOG_FILE"
            whisper_status=$?
        else
            "$WHISPER" </dev/null \
                -m "$MODEL" \
                -f "$temp_wav" \
                -of "$out_base" \
                -otxt \
                -ovtt \
                -osrt \
                --prompt "$prompt" >>"$LOG_FILE" 2>&1
            whisper_status=$?
        fi

        if [ "$whisper_status" -ne 0 ]; then
            log "FAILED: whisper-cli transcription failed for $media_file"
            cleanup_temp_file "$temp_wav"
            return 1
        fi
    else
        log "NOTE: whisper-cli does not support --prompt; running without prompt"
        if [ "$QUIET_WHISPER" -eq 1 ]; then
            "$WHISPER" </dev/null \
                -m "$MODEL" \
                -f "$temp_wav" \
                -of "$out_base" \
                -otxt \
                -ovtt \
                -osrt >/dev/null 2>>"$LOG_FILE"
            whisper_status=$?
        else
            "$WHISPER" </dev/null \
                -m "$MODEL" \
                -f "$temp_wav" \
                -of "$out_base" \
                -otxt \
                -ovtt \
                -osrt >>"$LOG_FILE" 2>&1
            whisper_status=$?
        fi

        if [ "$whisper_status" -ne 0 ]; then
            log "FAILED: whisper-cli transcription failed for $media_file"
            cleanup_temp_file "$temp_wav"
            return 1
        fi
    fi

    whisper_end=$(date +%s)
    whisper_time=$((whisper_end - whisper_start))

    cleanup_temp_file "$temp_wav"
    rm -f "$err_file"

    # Move completed outputs into their final folders, then run optional cleanup.
    if [ -f "$out_base.txt" ]; then
        mv -f "$out_base.txt" "$TXT_DIR/$stem.txt"
        apply_replacements "$TXT_DIR/$stem.txt"
    fi

    if [ -f "$out_base.vtt" ]; then
        mv -f "$out_base.vtt" "$VTT_DIR/$stem.vtt"
        apply_replacements "$VTT_DIR/$stem.vtt"
    fi

    if [ -f "$out_base.srt" ]; then
        mv -f "$out_base.srt" "$SRT_DIR/$stem.srt"
        apply_replacements "$SRT_DIR/$stem.srt"
    fi

    total_end=$(date +%s)
    total_time=$((total_end - total_start))

    if [ -f "$TXT_DIR/$stem.txt" ]; then
        log "DONE: $filename"
        log "TIMING:"
        log "  FFMPEG : $(format_time "$ffmpeg_time")"
        log "  WHISPER: $(format_time "$whisper_time")"
        log "  TOTAL  : $(format_time "$total_time")"
        return 0
    fi

    log "WARNING: whisper-cli finished but $TXT_DIR/$stem.txt was not found"
    return 1
}

if ! command -v "$WHISPER" >/dev/null 2>&1; then
    fail "Cannot find '$WHISPER' in PATH"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    fail "Cannot find 'ffmpeg' in PATH"
fi

if [ ! -f "$MODEL" ]; then
    fail "Model not found: $MODEL"
fi

VOCAB_FILE="$(find_vocab_file)" || fail "Vocabulary file not found while walking upward from $ROOT. Expected whisper-vocabulary.txt"

REPLACEMENTS_FILES="$(find_replacements_files || true)"

mkdir -p "$TXT_DIR" "$VTT_DIR" "$SRT_DIR" "$TMP_DIR"

SUPPORTS_PROMPT=0
if "$WHISPER" --help 2>&1 | grep -q -- "--prompt"; then
    SUPPORTS_PROMPT=1
fi

VOCAB_PROMPT="$(build_prompt)"

: > "$LOG_FILE"

log "Batch started: $(date)"
log "ROOT=$ROOT"
log "COURSE_HINT=$COURSE_HINT"
log "WHISPER=$WHISPER"
log "MODEL=$MODEL"
log "VOCAB_FILE=$VOCAB_FILE"
if [ -n "${REPLACEMENTS_FILES:-}" ]; then
    log "REPLACEMENTS_FILES:"
    printf "%s\n" "$REPLACEMENTS_FILES" | sed 's/^/  /' | tee -a "$LOG_FILE"
else
    log "REPLACEMENTS_FILES=none"
fi
log "LOG_FILE=$LOG_FILE"
log "TXT_DIR=$TXT_DIR"
log "VTT_DIR=$VTT_DIR"
log "SRT_DIR=$SRT_DIR"
log "TMP_DIR=$TMP_DIR"
log "SUPPORTS_PROMPT=$SUPPORTS_PROMPT"
log "FORCE=$FORCE"
log "QUIET_WHISPER=$QUIET_WHISPER"
log "=================================================="

TOTAL=0
DONE_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

# Important macOS fix:
# Use find from "$ROOT" and consume absolute paths directly.
# Do not strip or rebuild paths, because that caused /Users/... to become Users/...
#
# Important stdin fix:
# whisper-cli may read from stdin. If the while loop is also reading filenames
# from stdin, whisper-cli can steal bytes from the next filename, producing
# paths like "sers/..." instead of "/Users/...".
# We therefore put the media list in a file, read it on fd 3, and also run
# whisper-cli with stdin redirected from /dev/null inside process_file().
MEDIA_LIST="$TMP_DIR/media-files.txt"
find "$ROOT" -maxdepth 1 -type f \( -iname "*.mov" -o -iname "*.mp4" -o -iname "*.m4v" \) -print | sort > "$MEDIA_LIST"

while IFS= read -r media_file <&3
do
    TOTAL=$((TOTAL + 1))
    filename="$(basename "$media_file")"
    stem="${filename%.*}"

    if already_done "$stem"; then
        log "SKIP: $filename (found existing transcript)"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        continue
    fi

    if process_file "$media_file"; then
        DONE_COUNT=$((DONE_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done 3< "$MEDIA_LIST"

rm -f "$MEDIA_LIST"

log ""
log "=================================================="
log "Batch finished: $(date)"
log "TOTAL=$TOTAL"
log "DONE=$DONE_COUNT"
log "SKIPPED=$SKIP_COUNT"
log "FAILED=$FAIL_COUNT"
log "LOG=$LOG_FILE"
log "=================================================="
log ""
if [ -n "${REPLACEMENTS_FILES:-}" ]; then
    log "NOTE: New transcripts were cleaned with *whisper-replacements* during this run."
    log "TIP: SKIPPED files were not re-cleaned. To re-apply replacements later:"
else
    log "TIP: Add a *whisper-replacements* file under $ROOT (optional),"
    log "     then re-apply cleanup to transcripts with:"
fi
log "  whisper-cleanup.py"
