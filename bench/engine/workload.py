import json
import random
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "workloads"
PRESETS = ("chat", "long_context", "generation")


def load_prompts(preset: str) -> list[dict]:
    if preset not in PRESETS:
        raise ValueError(f"unknown workload preset: {preset}")
    return json.loads((DATA_DIR / f"{preset}.json").read_text())["prompts"]


class PromptCycler:
    def __init__(self, prompts: list[dict], seed: int):
        self._prompts = list(prompts)
        self._seed = seed
        self._cycle = 0
        self._order: list[dict] = []
        self._i = 0

    def next(self) -> dict:
        if self._i >= len(self._order):
            rng = random.Random(self._seed + self._cycle)
            self._order = self._prompts[:]
            rng.shuffle(self._order)
            self._cycle += 1
            self._i = 0
        prompt = dict(self._order[self._i])
        self._i += 1
        prompt["text"] = f"[req {uuid.uuid4()}] " + prompt["text"]
        return prompt
