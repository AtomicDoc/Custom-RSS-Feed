"""Thin client for the local Ollama HTTP API."""
import json
import logging
import re
import time

import numpy as np
import requests

log = logging.getLogger(__name__)

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
RETRY_DELAYS = (10, 30, 60)   # seconds; rides out an Ollama restart (tray-app update, crashed runner)


class OllamaError(RuntimeError):
    """Ollama is unreachable or rejected a request; the message says how to fix it."""


class Ollama:
    def __init__(self, cfg: dict):
        self.url = cfg["url"].rstrip("/")
        self.chat_model = cfg["chat_model"]
        self.embed_model = cfg["embed_model"]
        self.options = {"num_ctx": cfg.get("num_ctx", 8192), "temperature": cfg.get("temperature", 0.6)}
        self.timeout = cfg.get("timeout_seconds", 900)
        self.think = cfg.get("think")   # set false for reasoning models (qwen3) to skip slow hidden thinking

    def _post(self, path: str, body: dict) -> dict:
        for attempt, delay in enumerate((*RETRY_DELAYS, None), 1):
            try:
                resp = requests.post(f"{self.url}{path}", json=body, timeout=self.timeout)
            except requests.ConnectionError:
                if delay is None:
                    raise OllamaError(
                        f"Ollama stopped responding at {self.url} (model {body.get('model')}). It was likely "
                        "running earlier in this run, so it probably crashed loading the model: check "
                        r"%LOCALAPPDATA%\Ollama\server.log, and try `ollama run <model> hi` by hand."
                    ) from None
                log.warning("Ollama unreachable (attempt %d), retrying in %ds", attempt, delay)
                time.sleep(delay)
                continue
            if not resp.ok:
                raise OllamaError(f"Ollama {path} failed ({resp.status_code}): {resp.text[:300]}")
            return resp.json()

    def check(self) -> None:
        """Fails fast with a readable message if Ollama isn't running or a model isn't pulled."""
        try:
            tags = requests.get(f"{self.url}/api/tags", timeout=10).json()
        except requests.RequestException:
            raise OllamaError(
                f"Can't reach Ollama at {self.url}. Start it (Ollama app from the Start menu, "
                "or `ollama serve` in a terminal you leave open) and try again."
            ) from None
        installed = {m["name"] for m in tags.get("models", [])}
        for model in (self.chat_model, self.embed_model):
            name = model if ":" in model else f"{model}:latest"
            if name not in installed:
                raise OllamaError(f"Model {model} isn't pulled yet. Run: ollama pull {model}")

    def unload(self) -> None:
        """Frees the chat model's VRAM now instead of after Ollama's 5-minute keep-alive, so TTS fits on the GPU."""
        try:
            requests.post(f"{self.url}/api/generate", json={"model": self.chat_model, "keep_alive": 0}, timeout=60)
        except requests.RequestException as exc:
            log.warning("Couldn't unload %s: %s", self.chat_model, exc)

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        data = self._post("/api/embed", {"model": self.embed_model, "input": texts})
        vecs = [np.asarray(v, dtype=np.float32) for v in data["embeddings"]]
        return [v / (np.linalg.norm(v) or 1.0) for v in vecs]

    def chat(self, system: str, user: str, as_json: bool = False, temperature: float | None = None) -> str:
        options = dict(self.options)
        if temperature is not None:
            options["temperature"] = temperature
        body = {
            "model": self.chat_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": options,
        }
        if as_json:
            body["format"] = "json"
        if self.think is not None:
            body["think"] = self.think
        # Reasoning models (qwen3, deepseek-r1) may emit <think> blocks; never read those aloud.
        return THINK_RE.sub("", self._post("/api/chat", body)["message"]["content"]).strip()

    def chat_json(self, system: str, user: str) -> dict:
        raw = self.chat(system, user, as_json=True, temperature=0.1)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            return json.loads(match.group(0)) if match else {}
