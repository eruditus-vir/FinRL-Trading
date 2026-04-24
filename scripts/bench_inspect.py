"""One call per model, print the raw response so we can see why JSON parse fails."""

import json
import urllib.request

from bench_local_llm import PROMPT, OLLAMA_URL

MODELS = ["qwen3.6:35b", "gemma4:31b", "qwen3:30b", "qwen3:8b"]


def call(model: str, num_predict: int = 600, think: bool | None = None) -> dict:
    options = {"num_predict": num_predict, "temperature": 0.1}
    payload = {"model": model, "prompt": PROMPT, "stream": False, "options": options}
    if think is not None:
        payload["think"] = think
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())


for m in MODELS:
    print(f"\n{'=' * 80}\n{m} (num_predict=600, think=False)\n{'=' * 80}")
    try:
        r = call(m, num_predict=600, think=False)
    except Exception:
        r = call(m, num_predict=600)  # older API or model doesn't support think=
    text = r.get("response", "")
    print(f"eval_count={r.get('eval_count')}, "
          f"done_reason={r.get('done_reason')}")
    print(f"--- response ({len(text)} chars) ---")
    print(text)
