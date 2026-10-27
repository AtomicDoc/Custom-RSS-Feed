"""Thin client for the local Ollama HTTP API."""
import json
import re

import numpy as np
import requests

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class Ollama:
    def __init__(self, cfg: dict):
        self.url = cfg["url"].rstrip("/")
        self.chat_model = cfg["chat_model"]
        self.embed_model = cfg["embed_model"]
        self.options = {"num_ctx": cfg.get("num_ctx", 8192), "temperature": cfg.get("temperature", 0.6)}
        self.timeout = cfg.get("timeout_seconds", 900)

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        resp = requests.post(
            f"{self.url}/api/embed",
            json={"model": self.embed_model, "input": texts},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        vecs = [np.asarray(v, dtype=np.float32) for v in resp.json()["embeddings"]]
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
        resp = requests.post(f"{self.url}/api/chat", json=body, timeout=self.timeout)
        resp.raise_for_status()
        # Reasoning models (qwen3, deepseek-r1) may emit <think> blocks; never read those aloud.
        return THINK_RE.sub("", resp.json()["message"]["content"]).strip()

    def chat_json(self, system: str, user: str) -> dict:
        raw = self.chat(system, user, as_json=True, temperature=0.1)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            return json.loads(match.group(0)) if match else {}
