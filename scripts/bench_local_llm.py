"""Measure local LLM throughput on a v4-shaped news-analysis prompt.

Hits Ollama's /api/generate and reads the native timing fields
(prompt_eval_count, prompt_eval_duration, eval_count, eval_duration).
Runs one warmup + N measured calls per model.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request
from typing import Any

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELS = ["qwen3.6:35b", "gemma4:31b", "qwen3:30b", "qwen3:8b"]
WARMUP = 1
MEASURED = 3
NUM_PREDICT = 600  # high cap — let models stop naturally


PROMPT = """You are a financial analyst. A stock is currently oversold: RSI=28, declined 11% over past 10 days.
Task: decide if this is a bounce opportunity or a falling knife, using ONLY the article and company context below.

COMPANY PROFILE: Meta Platforms (META)
  Sector: Communication Services
  Market Cap: $1.4T
  Current P/E: 24.3

QUARTERLY FUNDAMENTAL TRAJECTORY (last 6 quarters, oldest first):
  2024-Q1: Revenue $36.5B (+27% YoY), Operating Margin 38%, FCF $12.5B
  2024-Q2: Revenue $39.1B (+22% YoY), Operating Margin 38%, FCF $10.9B
  2024-Q3: Revenue $40.6B (+19% YoY), Operating Margin 43%, FCF $15.5B
  2024-Q4: Revenue $48.4B (+21% YoY), Operating Margin 48%, FCF $13.2B
  2025-Q1: Revenue $42.3B (+16% YoY), Operating Margin 41%, FCF $10.1B
  2025-Q2: Revenue $47.5B (+22% YoY), Operating Margin 43%, FCF $8.5B

RECENT EARNINGS GUIDANCE:
  2025-Q2 call: Management reaffirmed full-year capex of $72B (previously $60-65B),
  citing AI infrastructure investments. Reality Labs losses expected to grow materially
  in H2. Ad revenue growth decelerating but still double-digit.

ARTICLE:
  Headline: Meta Shares Slide as Analysts Question AI Spending Payoff Timeline
  Content: Meta Platforms shares dropped 4.2% Wednesday after two major sell-side firms
  downgraded the stock, citing concerns that the company's ballooning AI infrastructure
  capex may not yield commensurate revenue returns until 2027 or later. Morgan Stanley
  cut its price target from $780 to $650, maintaining an Equal-Weight rating. The
  downgrade cited a "fundamental mismatch" between Meta's $72B capex plan and the
  revenue visibility from its Llama 4 and Reality Labs initiatives. JPMorgan followed
  with a similar cut, flagging that Family of Apps ad revenue growth has decelerated
  for three consecutive quarters from 27% to 22%. "We believe the market has been
  giving Meta credit for an AI-driven re-acceleration that the Q2 numbers do not yet
  support," JPMorgan analyst Doug Anmuth wrote. The stock has now declined 11% over
  the past two weeks, pushing RSI into oversold territory. Defenders point to Meta's
  operating margin of 43% (up from 38% a year ago) as evidence of continued operational
  excellence, and note that free cash flow remains healthy at $8.5B for the quarter
  despite the capex ramp. Some contrarian investors see the pullback as a buying
  opportunity, drawing parallels to the 2022 selloff that preceded a 180% rally.

RULES:
1. Use ONLY the article and company profile above. No outside knowledge about specific
   events, earnings, announcements, or financials.
2. If the article does not specifically discuss META (e.g. generic market recap, mover
   lists, index summary), set relevant=false.
3. Macro/sector news IS valid - Fed policy, inflation, geopolitics, sector rotation
   are legitimate drivers of oversold conditions.
4. The trajectory above tells you structural direction: consistently declining revenue
   + margins = falling knife risk; stable or improving = more likely a bounce setup.
5. Internal consistency: if deterioration=true, bounce_probability should be <= 0.4.

Step 1 - Write ONE sentence of reasoning analyzing the setup.
Step 2 - Output ONLY the JSON below, nothing else:

{
  "reasoning": "one sentence explaining bounce vs falling knife",
  "relevant": true or false,
  "bounce_probability": 0.0 to 1.0,
  "catalyst_type": "earnings|guidance|analyst|macro|sector|legal|regulatory|product|management|partnership|other|none",
  "deterioration": true or false,
  "sentiment_score": -1.0 to 1.0
}"""


def call_ollama(model: str, prompt: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"num_predict": NUM_PREDICT, "temperature": 0.1},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=data, headers={"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.loads(resp.read())
    body["_wall_sec"] = time.perf_counter() - t0
    return body


def json_parseable(text: str) -> bool:
    """Check if response contains a parseable JSON block with expected fields."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return False
    try:
        obj = json.loads(text[start : end + 1])
        return all(
            k in obj
            for k in ("relevant", "bounce_probability", "catalyst_type", "deterioration")
        )
    except json.JSONDecodeError:
        return False


def summarize(model: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    prefill_rates = []
    gen_rates = []
    totals = []
    parses = []
    for r in runs:
        # Ollama reports durations in nanoseconds
        pe_count = r.get("prompt_eval_count", 0)
        pe_dur_ns = r.get("prompt_eval_duration", 1)
        ev_count = r.get("eval_count", 0)
        ev_dur_ns = r.get("eval_duration", 1)
        prefill_rates.append(pe_count / (pe_dur_ns / 1e9) if pe_dur_ns else 0)
        gen_rates.append(ev_count / (ev_dur_ns / 1e9) if ev_dur_ns else 0)
        totals.append(r["_wall_sec"])
        parses.append(json_parseable(r.get("response", "")))
    return {
        "model": model,
        "prefill_tok_s_p50": statistics.median(prefill_rates),
        "gen_tok_s_p50": statistics.median(gen_rates),
        "wall_sec_p50": statistics.median(totals),
        "wall_sec_p90": max(totals) if len(totals) < 10 else sorted(totals)[int(len(totals) * 0.9)],
        "json_ok_rate": sum(parses) / len(parses),
        "input_tokens": runs[0].get("prompt_eval_count", 0),
        "output_tokens_p50": statistics.median(r.get("eval_count", 0) for r in runs),
    }


def main() -> int:
    print(f"Models: {MODELS}")
    print(f"Warmup: {WARMUP}, measured: {MEASURED}, num_predict: {NUM_PREDICT}")
    print(f"Prompt size: {len(PROMPT)} chars\n")

    results = []
    for model in MODELS:
        print(f"→ {model}")
        try:
            for _ in range(WARMUP):
                print("  warmup...", end=" ", flush=True)
                call_ollama(model, PROMPT)
                print("done")
            runs = []
            for i in range(MEASURED):
                print(f"  run {i+1}/{MEASURED}...", end=" ", flush=True)
                r = call_ollama(model, PROMPT)
                runs.append(r)
                print(f"{r['_wall_sec']:.1f}s")
            results.append(summarize(model, runs))
        except Exception as exc:
            print(f"  FAILED: {exc}")
            results.append({"model": model, "error": str(exc)})
        print()

    print("\n" + "=" * 110)
    print(f"{'model':<18} {'in_tok':>7} {'out_tok':>8} {'prefill/s':>10} {'gen/s':>8} "
          f"{'wall_p50':>10} {'wall_p90':>10} {'json_ok':>8}")
    print("=" * 110)
    for r in results:
        if "error" in r:
            print(f"{r['model']:<18} ERROR: {r['error']}")
            continue
        print(
            f"{r['model']:<18} {r['input_tokens']:>7} {r['output_tokens_p50']:>8.0f} "
            f"{r['prefill_tok_s_p50']:>10.1f} {r['gen_tok_s_p50']:>8.1f} "
            f"{r['wall_sec_p50']:>9.2f}s {r['wall_sec_p90']:>9.2f}s "
            f"{r['json_ok_rate']:>7.0%}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
