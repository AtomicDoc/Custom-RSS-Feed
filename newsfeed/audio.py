"""Local text-to-speech and MP3 encoding."""
import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

PARAGRAPH_GAP = 0.35   # seconds of silence between paragraphs
SEGMENT_GAP = 0.9      # between stories
CHUNK_GAP = 0.12       # between Chatterbox chunks inside a paragraph
CHUNK_CHARS = 280      # Chatterbox stops at ~40s of speech per call, so feed it a few sentences at a time


def _silence(rate: int, seconds: float) -> np.ndarray:
    return np.zeros(int(rate * seconds), dtype=np.float32)


def _paragraphs(segment: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n+", segment) if p.strip()]


def _chunks(paragraph: str, limit: int = CHUNK_CHARS):
    chunk = ""
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        if chunk and len(chunk) + len(sentence) + 1 > limit:
            yield chunk
            chunk = sentence
        else:
            chunk = f"{chunk} {sentence}".strip()
    if chunk:
        yield chunk


def _chatterbox(segments: list[str], cfg: dict) -> tuple[np.ndarray, int]:
    import torch
    from chatterbox.tts import ChatterboxTTS  # heavy import (torch); only load when used

    model = ChatterboxTTS.from_pretrained(device="cuda" if torch.cuda.is_available() else "cpu")
    exaggeration = cfg.get("chatterbox_exaggeration", 0.5)
    if cfg.get("chatterbox_voice"):
        model.prepare_conditionals(cfg["chatterbox_voice"], exaggeration=exaggeration)
    rate = model.sr
    out = []
    for segment in segments:
        for paragraph in _paragraphs(segment):
            for chunk in _chunks(paragraph):
                wav = model.generate(
                    chunk, exaggeration=exaggeration, cfg_weight=cfg.get("chatterbox_cfg_weight", 0.5)
                )
                out += [wav.squeeze(0).numpy(), _silence(rate, CHUNK_GAP)]
            out.append(_silence(rate, PARAGRAPH_GAP))
        out.append(_silence(rate, SEGMENT_GAP))
    return np.concatenate(out), rate


def _kokoro(segments: list[str], cfg: dict) -> tuple[np.ndarray, int]:
    from kokoro import KPipeline  # heavy import (torch); only load when used

    voice = cfg["kokoro_voice"]
    pipeline = KPipeline(lang_code=voice[0])  # 'a' = American, 'b' = British voices
    rate = 24000
    out = []
    for segment in segments:
        for _, _, audio in pipeline(segment, voice=voice, speed=cfg.get("kokoro_speed", 1.0), split_pattern=r"\n+"):
            if audio is None:
                continue
            out.append(audio.cpu().numpy() if hasattr(audio, "cpu") else np.asarray(audio))
            out.append(_silence(rate, PARAGRAPH_GAP))
        out.append(_silence(rate, SEGMENT_GAP))
    return np.concatenate(out), rate


def _piper(segments: list[str], cfg: dict) -> tuple[np.ndarray, int]:
    out, rate = [], None
    with tempfile.TemporaryDirectory() as tmp:
        for n, segment in enumerate(segments):
            wav = Path(tmp) / f"seg{n}.wav"
            subprocess.run(
                [sys.executable, "-m", "piper", "--model", cfg["piper_model"], "--output_file", str(wav)],
                input=segment.encode("utf-8"), check=True,
            )
            data, rate = sf.read(wav, dtype="float32")
            out += [data, _silence(rate, SEGMENT_GAP)]
    return np.concatenate(out), rate


ENGINES = {"chatterbox": _chatterbox, "kokoro": _kokoro, "piper": _piper}


def synthesize(segments: list[str], cfg: dict, wav_path: Path) -> None:
    engine = cfg["engine"]
    log.info("Synthesizing %d segments with %s", len(segments), engine)
    audio, rate = ENGINES[engine](segments, cfg)
    sf.write(wav_path, audio, rate)


def encode_mp3(wav_path: Path, mp3_path: Path, intro: str = "", outro: str = "") -> int:
    """Joins optional intro/outro around the speech, encodes to MP3 and returns the duration in seconds."""
    inputs = [p for p in (intro, str(wav_path), outro) if p]
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        args += ["-i", path]
    # Normalize every input to the same format so concat accepts mixed sources (e.g. stereo music + mono voice).
    chains = "".join(
        f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}];" for i in range(len(inputs))
    )
    joined = "".join(f"[a{i}]" for i in range(len(inputs)))
    graph = f"{chains}{joined}concat=n={len(inputs)}:v=0:a=1,loudnorm=I=-16:TP=-1.5[out]"
    args += ["-filter_complex", graph, "-map", "[out]", "-ac", "1", "-b:a", "96k", str(mp3_path)]
    subprocess.run(args, check=True)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp3_path)],
        capture_output=True, text=True, check=True,
    )
    return int(float(probe.stdout.strip()))


VOICE_TEST_TEXT = (
    "Good morning! So, the big story today is out of Brussels, where European leaders finally agreed on new "
    "rules for AI chatbots. It took them almost two years, which, honestly, is pretty fast for the EU.\n"
    "Here's what actually changes for you: companies now have to tell you when you're talking to a bot, "
    "and the fines for breaking that rule are steep. We'll see how strictly it's enforced."
)

VOICE_TESTS = {
    "chatterbox-calm": {"engine": "chatterbox", "chatterbox_exaggeration": 0.35, "chatterbox_cfg_weight": 0.5},
    "chatterbox-natural": {"engine": "chatterbox", "chatterbox_exaggeration": 0.5, "chatterbox_cfg_weight": 0.5},
    "chatterbox-lively": {"engine": "chatterbox", "chatterbox_exaggeration": 0.7, "chatterbox_cfg_weight": 0.3},
    "kokoro-af_heart": {"engine": "kokoro", "kokoro_voice": "af_heart"},
    "kokoro-af_bella": {"engine": "kokoro", "kokoro_voice": "af_bella"},
    "kokoro-af_nicole": {"engine": "kokoro", "kokoro_voice": "af_nicole"},
    "kokoro-bf_emma": {"engine": "kokoro", "kokoro_voice": "bf_emma"},
}


def voice_tests(tts_cfg: dict, out_dir: Path) -> list[Path]:
    """Renders the same sample paragraph with each candidate voice so they can be compared by ear."""
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for name, overrides in VOICE_TESTS.items():
        cfg = {**tts_cfg, **overrides}   # Chatterbox tests use your chatterbox_voice clip if one is set
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "test.wav"
            synthesize(_paragraphs(VOICE_TEST_TEXT), cfg, wav)
            mp3 = out_dir / f"{name}.mp3"
            encode_mp3(wav, mp3)
        made.append(mp3)
        log.info("Wrote %s", mp3.name)
    return made
