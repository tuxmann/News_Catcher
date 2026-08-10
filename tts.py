"""Text-to-speech: KittenTTS chunks + ffmpeg concat to MP3."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import config
from article_format import body_starts_with_title, strip_emphasis_markers
from tts_branding import build_intro_text, build_outro_text
from tts_normalize import normalize_for_tts

logger = logging.getLogger(__name__)

# Lazy-loaded KittenTTS instances (model download + ONNX init is expensive).
_tts_models: dict[str, object] = {}

SPEAK_PHRASE_RE = re.compile(
    r"^newscatcher\s*,?\s*speak\s+to\s+me\s*$",
    re.IGNORECASE,
)


def is_speak_phrase(text: str) -> bool:
    return bool(SPEAK_PHRASE_RE.match(text.strip()))


def build_narration_text(title: str | None, body: str) -> str:
    body = body.strip()
    if not title or not title.strip():
        return body
    if body_starts_with_title(body, title):
        return body
    return f"{title.strip()}. {body}"


def chunk_text_for_tts(text: str, max_chars: int | None = None) -> list[str]:
    max_chars = max_chars or config.TTS_CHUNK_CHARS
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for para in paragraphs:
        if len(para) > max_chars:
            flush()
            start = 0
            while start < len(para):
                chunks.append(para[start : start + max_chars])
                start += max_chars
            continue
        trial = f"{current}\n\n{para}".strip() if current else para
        if len(trial) <= max_chars:
            current = trial
        else:
            flush()
            current = para
    flush()
    return chunks


def _load_kittentts(model_name: str):
    """Load KittenTTS 0.8.x (Hugging Face repo id, e.g. KittenML/kitten-tts-mini-0.8)."""
    if model_name in _tts_models:
        return _tts_models[model_name]
    try:
        from kittentts import KittenTTS
    except ImportError as e:
        raise RuntimeError(
            "KittenTTS 0.8.x is not installed. Run: pip install -r requirements.txt"
        ) from e
    logger.info("Loading KittenTTS model %s (first run downloads from Hugging Face)", model_name)
    _tts_models[model_name] = KittenTTS(model_name)
    return _tts_models[model_name]


def _resolve_voice(model: object, voice: str) -> str:
    names = getattr(model, "available_voices", None) or getattr(
        getattr(model, "model", None), "all_voice_names", None
    )
    if names and voice not in names:
        raise ValueError(f"Unknown TTS voice {voice!r}. Choose from: {', '.join(names)}")
    return voice


def _write_silence_wav(wav_path: Path, duration_sec: float, sample_rate: int = 24000) -> None:
    import numpy as np
    import soundfile as sf

    samples = max(1, int(sample_rate * duration_sec))
    sf.write(str(wav_path), np.zeros(samples, dtype=np.float32), sample_rate)


def _synthesize_chunk_wav(
    model: object,
    text: str,
    wav_path: Path,
    voice: str,
    *,
    speed: float = 1.0,
    clean_text: bool = True,
) -> None:
    import soundfile as sf

    voice = _resolve_voice(model, voice)
    # Use inner ONNX model to avoid per-chunk print() in KittenTTS.generate wrapper.
    engine = getattr(model, "model", model)
    audio = engine.generate(text, voice=voice, speed=speed, clean_text=clean_text)
    sf.write(str(wav_path), audio, 24000)


def _mp3_encode_args() -> list[str]:
    return [
        "-codec:a",
        "libmp3lame",
        "-ar",
        str(config.TTS_MP3_SAMPLE_RATE),
        "-ac",
        "1",
        "-b:a",
        config.TTS_MP3_BITRATE,
    ]


def _crossfade_seconds() -> float:
    return max(0.0, config.TTS_CHUNK_CROSSFADE_MS / 1000.0)


def build_acrossfade_filter(num_inputs: int, crossfade_sec: float) -> str | None:
    """Build ffmpeg filter_complex to crossfade N audio inputs."""
    if num_inputs < 2 or crossfade_sec <= 0:
        return None
    d = f"{crossfade_sec:.3f}"
    if num_inputs == 2:
        return f"[0:a][1:a]acrossfade=d={d}:c1=tri:c2=tri[out]"
    parts: list[str] = []
    parts.append(f"[0:a][1:a]acrossfade=d={d}:c1=tri:c2=tri[a01]")
    for i in range(2, num_inputs):
        prev = f"a{i - 1:02d}"
        out = "out" if i == num_inputs - 1 else f"a{i:02d}"
        parts.append(f"[{prev}][{i}:a]acrossfade=d={d}:c1=tri:c2=tri[{out}]")
    return ";".join(parts)


def _concat_wavs_to_mp3(wav_paths: list[Path], mp3_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH; install ffmpeg to build audio files.")
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    encode = _mp3_encode_args()
    crossfade = _crossfade_seconds()
    filter_graph = build_acrossfade_filter(len(wav_paths), crossfade)

    if filter_graph:
        cmd = ["ffmpeg", "-y"]
        for wav in wav_paths:
            cmd.extend(["-i", str(wav)])
        cmd.extend(
            [
                "-filter_complex",
                filter_graph,
                "-map",
                "[out]",
                *encode,
                str(mp3_path),
            ]
        )
        subprocess.run(cmd, check=True, capture_output=True)
        return

    if len(wav_paths) == 1:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_paths[0]), *encode, str(mp3_path)],
            check=True,
            capture_output=True,
        )
        return

    list_file = mp3_path.with_suffix(".concat.txt")
    try:
        lines = [f"file '{p.resolve()}'" for p in wav_paths]
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                *encode,
                str(mp3_path),
            ],
            check=True,
            capture_output=True,
        )
    finally:
        list_file.unlink(missing_ok=True)


def synthesize_to_mp3(
    text: str,
    output_path: Path,
    *,
    title: str | None = None,
    source_domain: str | None = None,
    model_name: str | None = None,
    voice: str | None = None,
    speed: float = 1.0,
    clean_text: bool = True,
    chunk_chars: int | None = None,
    lead_silence_ms: int | None = None,
    skip_enabled_check: bool = False,
) -> Path:
    """
    Synthesize narration to a single MP3 file.
    Raises RuntimeError if TTS is disabled or dependencies are missing.

    When source_domain is set and TTS_SOURCE_BRANDING_ENABLED, prepends an intro
    ("From {site} dot com.") and appends an outro ("That's the end from …").
    """
    if not skip_enabled_check and not config.TTS_ENABLED:
        raise RuntimeError("TTS is disabled (set TTS_ENABLED=1).")

    narration = build_narration_text(title, text)
    narration = strip_emphasis_markers(narration)
    narration = normalize_for_tts(narration)
    chunks = chunk_text_for_tts(narration, max_chars=chunk_chars)
    if not chunks:
        raise ValueError("No text to synthesize.")

    if config.TTS_SOURCE_BRANDING_ENABLED and source_domain:
        intro = build_intro_text(source_domain)
        outro = build_outro_text(source_domain)
        if intro:
            chunks.insert(0, intro)
        if outro:
            chunks.append(outro)

    model_name = (model_name or config.TTS_MODEL).strip()
    if not model_name:
        raise RuntimeError("TTS_MODEL is not set.")
    voice = voice or config.TTS_VOICE

    model = _load_kittentts(model_name)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="newscatcher_tts_") as tmp:
        tmp_path = Path(tmp)
        wav_paths: list[Path] = []
        for i, chunk in enumerate(chunks):
            wav = tmp_path / f"part_{i:04d}.wav"
            logger.info("TTS chunk %s/%s (%s chars)", i + 1, len(chunks), len(chunk))
            _synthesize_chunk_wav(
                model, chunk, wav, voice, speed=speed, clean_text=clean_text
            )
            wav_paths.append(wav)
        silence_ms = config.TTS_LEAD_SILENCE_MS if lead_silence_ms is None else lead_silence_ms
        if silence_ms > 0:
            silence = tmp_path / "lead_silence.wav"
            _write_silence_wav(silence, silence_ms / 1000.0)
            wav_paths.insert(0, silence)
        _concat_wavs_to_mp3(wav_paths, output_path)

    return output_path


def pronunciation_sample_phrase(variant: str) -> str:
    """Short line for auditioning how a spelling sounds."""
    return f"The word is: {variant.strip()}."


def synthesize_pronunciation_sample(
    variant: str,
    output_path: Path,
    *,
    lead_silence_ms: int = 0,
) -> Path:
    """Synthesize a short MP3 for one spelling variant (for /pronounce)."""
    phrase = pronunciation_sample_phrase(variant)
    return synthesize_to_mp3(
        phrase,
        output_path,
        title=None,
        chunk_chars=300,
        lead_silence_ms=lead_silence_ms,
    )


def list_available_voices(model_name: str | None = None) -> list[str]:
    """Return friendly voice names for a KittenTTS model (loads model if needed)."""
    model_name = (model_name or config.TTS_MODEL).strip()
    model = _load_kittentts(model_name)
    names = getattr(model, "available_voices", None)
    return list(names) if names else []


def split_mp3_for_telegram(mp3_path: Path, max_bytes: int) -> list[Path]:
    """Return one or more MP3 paths, each at most max_bytes when possible."""
    mp3_path = Path(mp3_path)
    size = mp3_path.stat().st_size
    if size <= max_bytes:
        return [mp3_path]

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return [mp3_path]

    # Approximate segment duration from average bitrate.
    duration_sec = _probe_duration_seconds(ffmpeg, mp3_path)
    if duration_sec <= 0:
        return [mp3_path]
    bytes_per_sec = size / duration_sec
    segment_sec = max(30, int(max_bytes / bytes_per_sec) - 5)

    out_dir = mp3_path.parent
    stem = mp3_path.stem
    pattern = out_dir / f"{stem}_part%03d.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(mp3_path),
            "-f",
            "segment",
            "-segment_time",
            str(segment_sec),
            "-codec:a",
            "copy",
            str(pattern),
        ],
        check=True,
        capture_output=True,
    )
    parts = sorted(out_dir.glob(f"{stem}_part*.mp3"))
    return parts if parts else [mp3_path]


def _probe_duration_seconds(ffmpeg: str, path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return 0.0
