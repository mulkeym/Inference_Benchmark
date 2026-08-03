"""Deterministically generate the bundled workload presets. Output is committed."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.engine.tokens import count_tokens  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "bench" / "data" / "workloads"
SEED_TEXT = (ROOT / "tools" / "corpus" / "seed.txt").read_text().split()

QUESTIONS = [
    "Summarize the passage above in three sentences.",
    "List the named characters and what each one wants.",
    "What is the central claim of the passage? Answer briefly.",
    "Extract every proper noun from the passage above.",
]
GEN_TASKS = [
    "Write an exhaustive, thoroughly detailed essay about the economics of {}. "
    "Cover its history, its participants, its costs, its incentives, its failures, "
    "and its future. Continue until you have covered at least twelve distinct "
    "aspects, with several sentences on each; do not summarize and do not stop early.",
    "Write a very long, richly detailed story about {}. Include many scenes, "
    "several characters with distinct voices, extended dialogue, and full "
    "descriptions of every location. Keep writing until the story has at least "
    "ten separate scenes; do not stop early.",
]
TOPICS = ["marriage in the nineteenth century", "country estates", "letter writing",
          "inheritance law", "village society", "carriage travel", "social visits",
          "reputation", "courtship rituals", "family fortunes"]


def passage(target_tokens: int, offset: int) -> str:
    words, i = [], offset
    while count_tokens(" ".join(words)) < target_tokens:
        words.append(SEED_TEXT[i % len(SEED_TEXT)])
        i += 1
    return " ".join(words)


def build(preset: str, n: int, prompt_tokens: int, max_tokens: int, kind: str) -> dict:
    prompts = []
    for i in range(n):
        if kind == "analysis":
            text = passage(prompt_tokens, i * 37) + "\n\n" + QUESTIONS[i % len(QUESTIONS)]
        else:
            text = GEN_TASKS[i % len(GEN_TASKS)].format(TOPICS[i % len(TOPICS)])
        prompts.append({"id": f"{preset}-{i:02d}", "text": text,
                        "max_tokens": max_tokens,
                        "expected_prompt_tokens": count_tokens(text)})
    return {"preset": preset, "version": "2.0.0", "prompts": prompts}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    specs = [("chat", 20, 500, 400, "analysis"),
             ("long_context", 20, 4000, 256, "analysis"),
             ("generation", 20, 80, 1024, "generation")]
    for preset, n, prompt_tokens, max_tokens, kind in specs:
        data = build(preset, n, prompt_tokens, max_tokens, kind)
        (OUT / f"{preset}.json").write_text(json.dumps(data, indent=1))


if __name__ == "__main__":
    main()
