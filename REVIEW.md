<!-- reviewed-by: independent-subagent -->
# Independent Code Review — LLM 输入预算闸 (NewApiLLMClient)

## Verdict
PASS_WITH_NOTES

The change is well-structured and achieves its stated goal: it turns `NewApiLLMClient`
into a generic OpenAI-compatible client with an input-token-budget gate, and the
self-heal retry is correctly ordered **before** the `_UNSUPPORTED_PARAM_RE` / `_is_non_retryable`
paths in **both** `complete()` and `complete_with_tools()`. Security is clean (no hardcoded
secrets), and `tools`/`tool_choice` are provably never dropped during self-heal. It was also
proven by a real e2e run. However, there is a genuine latent correctness bug in
`fit_input_budget` (orchestrator.py:141) that defeats the budget gate in the
empty-`system` path and that the self-heal cannot recover from. It is latent only because
every current call site passes a non-empty `system`; it should be fixed before merge.

## Findings

### F1 — `fit_input_budget` user-truncation guard is wrong for empty/`sys_t==0` system (orchestrator.py:140-142)
`usr_budget = max(0, max_input_tokens - sys_t)` then the truncation is gated by
`if usr_budget < max_input_tokens:` (line 141). Since `usr_budget == max_input_tokens - sys_t`
whenever `sys_t <= max_input_tokens`, this condition reduces to `sys_t > 0`.
- When `system` is empty (`estimate_tokens("") == 0`, see orchestrator.py:109-110), `usr_budget == max_input_tokens`, so `usr_budget < max_input_tokens` is **False** and the oversized `user` is **never truncated**, even though `sys_t + usr_t > max_input_tokens` (early-return at line 138 was skipped).
- Worse, the self-heal cannot recover: halving `factor` (lines 580-581 / 642-643) changes `cur_max` but never changes `sys_t == 0`, so `usr_budget` and the guard stay identical on every retry. The 400 self-heal loops until `factor` floors at `0.125` and then exhausts retries → permanent `LLMError` instead of a truncated success.
- The intended comment ("system 未占满，裁 user") is also misleading: the real condition should be "trim user when the user itself exceeds its share", i.e. `if estimate_tokens(user) > usr_budget:`. As written, the branch is effectively dead for `sys_t == 0`.
- Impact today: all real call sites (orchestrator.py:1272, 1643, 1825, 2011, etc.) and the e2e script (verify_llm_openai_e2e.py:71,86) pass a non-empty `system`, so the bug is **latent**, not exercised by the verified path. But it silently breaks the gate for any empty-system caller and for `complete_with_tools` if ever called with an empty system.
- Suggested fix: replace the guard at line 141 with `if estimate_tokens(user) > usr_budget:` (and keep `usr_budget = max(0, max_input_tokens - sys_t)`).

### F2 — Negative budget when `max_input_tokens - reserved_output_tokens <= 0` (orchestrator.py:559 / 623, 140)
`fit_input_budget` is called with `cur_max - self._reserved_output_tokens` (default reserved = 4096).
If `LLM_MAX_INPUT_TOKENS` or the constructor `max_input_tokens` is set below `reserved_output_tokens`
(e.g. a tiny-context model, or `max_input_tokens < 4096`), the budget argument becomes `<= 0`.
Then `usr_budget = max(0, <=0 - sys_t) = 0` and the line-141 guard `0 < (<=0)` is **False**, so user is
never truncated; only `system` is clamped to 1 token (line 144-146). The oversized `user` is sent verbatim → 400 not prevented. This is the same root cause as F1 (the guard fails for non-positive budgets). Default config (128000) is safe; flag for small-model configurations.

### F3 — Documented factor floor `0.125` (1/8) is never actually reached as a request (orchestrator.py:556-585 / 620-647)
`for attempt in range(self._max_retries + 1)` with the default `max_retries=2` yields **3** attempts.
Each 400 self-heal does `factor *= 0.5; continue`, producing request factors `1.0 → 0.5 → 0.25`.
The final `factor = 0.125` is set on the last iteration's `continue` but the loop then ends, so a request
at `1/8` never executes. The comments at lines 578-579 / 642-643 promise "最低 0.125 / 1/8", which is not
honored. Not blocking (the primary gate is `fit_input_budget` truncation, and halving twice to 1/4 is already
extreme), but the stated guarantee is false. Either raise `max_retries` for the self-heal or decouple
halving iterations from the retry count.

## Notes (non-blocking)

- **Security — clean.** No hardcoded API keys/secrets in the diff or in `scripts/verify_llm_openai_e2e.py`.
  `_max_input_tokens = int(os.getenv("LLM_MAX_INPUT_TOKENS", max_input_tokens))` (orchestrator.py:457) reads
  from env; `api_key` is injected via `cfg["api_key"]` from `.env` (gitignored). The verify script reads
  `NEWAPI_API_KEY` from env or the gitignored `.env` (verify_llm_openai_e2e.py:28-39). Good.
- **Ordering — correct.** In `complete()` the input-too-long self-heal (lines 580-585) precedes
  `_UNSUPPORTED_PARAM_RE` (588-597) and `_is_non_retryable` (599-600). In `complete_with_tools()` the same
  order holds (642-647 before 650-658 and 659-660). No dead-code risk. Good.
- **tools/tool_choice preserved — correct.** `complete_with_tools()` keeps `tools`/`tool_choice` in `body`
  (lines 615-616) and `params` is rebuilt from `body` each iteration (line 624); the self-heal only mutates
  `factor` and `continue`s. No code path drops tools. The existing test
  `tests/test_llm_drop_params.py:161-163` confirms tools survive all retries. Good.
- **Return values used — correct.** `sys_t, usr_t = fit_input_budget(...)` (lines 559 / 623) and the
  truncated values are sent in the messages (lines 565-566 / 629-630); the originals `system`/`user` are
  never sent. No ignored return values. Good.
- **Truncation direction — correct.** `_trunc_keep_tail` returns `text[-keep:]` (tail kept, head dropped,
  orchestrator.py:127) — matches the requirement. `system` truncation keeps the head (line 145). Correct.
- **`_trunc_keep_tail` is length-proportional, not token-proportional** (orchestrator.py:125-127). For text
  whose tail is denser in tokens than its head (e.g. CJK-heavy tail), the kept tail may still exceed
  `max_tokens`. This is acceptable because `complete()`'s input-too-long self-heal is the backstop — but note
  the backstop only works when F1 is fixed for the empty-system case.
- **`estimate_tokens` `+1` bias** (orchestrator.py:115) slightly over-estimates; harmless for budgeting
  (conservative) but can over-trim by ~1 token at exact boundaries. Minor.
- **`_INPUT_TOO_LONG_RE` over-broadness** (orchestrator.py:151-155): patterns like `token.*exceed` /
  `exceed.*context` could match non-input errors (e.g. `"token quota exceeded"` rate-limit 429), causing a
  spurious factor-halving retry. Low impact (worst case wastes one retry before falling through to
  `_is_non_retryable`). Consider tightening the patterns to input-context-specific phrases.
- **`system` overflow discards user** (orchestrator.py:143-146): when `system` alone exceeds the whole budget,
  both branches fire and `user` is truncated to the empty marker while `system` is also cut. Ideally only
  `system` would be trimmed in that case, but it still fits the budget and avoids 400 — acceptable.

## Post-review fixes applied (by main agent, after verdict)
Verdict `PASS_WITH_NOTES` stands; the three findings were addressed and re-verified (not re-signed here).

- **F1 fixed**: `fit_input_budget` user-truncation guard changed from `if usr_budget < max_input_tokens:` to
  `if usr_t > usr_budget:` (orchestrator.py:141). Now truncates user whenever the user itself exceeds its
  share, regardless of whether `system` is empty. Re-verified by new e2e case **[3b]**: empty `system` +
  110001-token oversized `user` → truncated to ~3908 tokens, returns 188 chars, **zero 400** (previously this
  path would 400-loop until retries exhausted).
- **F2 fixed**: budget passed to `fit_input_budget` is now clamped — `budget = max(1, cur_max - self._reserved_output_tokens)`
  in both `complete()` (line ~559) and `complete_with_tools()` (line ~623). Non-positive budget can no longer
  slip through the guard.
- **F3 fixed**: loop bound changed to `for attempt in range(max(self._max_retries + 1, 4))` in both methods, so
  the documented factor floor `0.125` (1/8) is actually issued (≥4 attempts). Comments updated to match.

Re-verification: `py_compile orchestrator.py` rc=0; `scripts/verify_llm_openai_e2e.py` all four checks green
(incl. new [3b]); concrete model `LongCat-2.0` returns real 66-char answer confirming live token output.
No further blocking issues. `<!-- reviewed-by: independent-subagent -->` unchanged.
