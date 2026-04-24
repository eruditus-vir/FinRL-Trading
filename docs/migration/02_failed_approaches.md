# Failed Approaches (don't repeat)

## Notebook 17: Quarterly fundamental ML scoring (FinRL-style)

**What was tried:** Train a separate quarterly ML model on fundamental features only (operating margin, debt/equity, revenue growth, P/E, P/B, P/S) to predict 90-day forward returns. FinRL's documented approach.

**Result:** Spearman rank correlation of **-0.028** on holdout. Not just weak — directionally wrong. Worse than coin flip.

**Why it failed:**
1. Fundamentals alone don't predict short-to-medium horizon returns well in this universe
2. Trained in isolation from technical context — model never saw RSI, regime, trend
3. Quarterly granularity is too coarse for trigger-based system that operates daily
4. Sample size: 169 stocks × 16 quarters = ~2700 rows is statistically tight

**Lesson:** Fundamentals are useful as **moderating context** within a unified model, NOT as a standalone scoring layer that gets composed with technicals afterward. This drove the architectural shift in `03_architecture_vision.md`.

## Pure RL allocation on the trigger system (notebook 09)

**What was tried:** RL portfolio allocation agent (PPO) over the 40-stock universe with daily decisions.

**Result:** Failed to converge to stable policy. Performance worse than rank-and-fill heuristic.

**Why it failed:**
1. Action space too large (40-dimensional continuous weights) for the training data available
2. Reward signal too noisy (daily portfolio returns have huge variance)
3. No pre-filtering — RL had to learn both stock selection AND sizing simultaneously

**Lesson:** RL must come AFTER good ML predictions exist. Pre-filter candidates with the trigger system + Stage 1 classifier first. RL only does sizing/timing within a small candidate set, not full universe selection.

## NFLX/INTC trap (system failure mode in 2022)

**What happened:** The trigger system bought NFLX and INTC during 2022 oversold dips. Both kept falling. Contributed materially to the -35.6% MDD.

**Root cause:** The trigger fired correctly (both were technically oversold) but the system had no awareness that:
- Both companies had structural deterioration (revenue trajectory + margin trajectory both declining)
- The macro regime (Fed hiking aggressively) was hostile to growth stocks
- The bounces that "should" happen weren't going to happen

**Lesson:** Two missing layers:
1. **Stock-level:** Multi-quarter fundamental trajectory (driving v4 LLM prompt design)
2. **Universe-level:** Macro regime awareness (driving the macro layer architecture)

Both are in `03_architecture_vision.md` as the planned additions.

## Cloud LLM analysis with knowledge leak (v1 prompts)

**What was tried:** Send news articles + ticker to GPT-4 with simple "is this bullish?" prompt.

**Result:** LLM outputs correlated with ticker, not with article content. The model was using its training knowledge of how that company performed, not analyzing the article.

**Why it failed:** No grounding constraint. LLM defaulted to "what do I know about META" rather than "what does this article say."

**Lesson:** Prompts must explicitly constrain the LLM to use only article content + provided context. Drove v2/v3/v4 prompt design with grounding rules and point-in-time company facts.

## What we learned across all failures

1. **Don't compose models trained in isolation.** Train features together so they can learn interactions.
2. **RL needs good ML predictions to operate on.** Don't put RL in front of weak signals.
3. **Stock-specific context (fundamentals trajectory) and universe-level context (macro regime) are both required** to filter true setups from traps.
4. **LLMs need grounding.** Without explicit constraints, they pattern-match on training data instead of analyzing inputs.