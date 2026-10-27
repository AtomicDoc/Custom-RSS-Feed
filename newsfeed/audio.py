"""Local text-to-speech and MP3 encoding."""
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

PARAGRAPH_GAP = 0.35   # seconds of silence between paragraphs
SEGMENT_GAP = 0.9      # between stories


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
            out.append(np.zeros(int(rate * PARAGRAPH_GAP), dtype=np.float32))
        out.append(np.zeros(int(rate * SEGMENT_GAP), dtype=np.float32))
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
            out += [data, np.zeros(int(rate * SEGMENT_GAP), dtype=np.float32)]
    return np.concatenate(out), rate


ENGINES = {"kokoro": _kokoro, "piper": _piper}


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
