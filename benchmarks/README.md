# Token-estimator benchmark

## Corpus provenance and license

`token_corpus.json` contains 12 original synthetic MCP-style tool definitions,
authored for issue #63 and licensed under this repository's [MIT license](../LICENSE).
No external tool schemas or contributor-provided examples were copied. @NixEngine's
comment motivated the escaped/unescaped Unicode dimension; the fixture and counts
from that comment are not used here because its exact schema was not provided.

Cases cover English, Portuguese, Chinese, Japanese, emoji (including modifiers and
joined sequences), mixed scripts and combining marks, long identifiers/descriptions,
nested objects/arrays, bounds/enums, minimal tools and escaped text. This is a small,
purpose-built set of boundary cases, **not a representative statistical sample** of
production traffic. Per-language conclusions or a universal accuracy bound would
require a larger, independently sourced corpus.

## Measurement

Run from the repository root after installing `.[exact-tokenizer]`:

```bash
python scripts/benchmark_tokens.py
python scripts/benchmark_tokens.py --check
```

The script pins tiktoken 0.14.0 and compares `estimate_tokens` with `cl100k_base`
and `o200k_base`. Each definition is serialized twice with compact separators
`(',', ':')`, committed key order (`sort_keys=False`), and `ensure_ascii=True` or
`False`. The **same string** goes to the estimator and reference encoder. UTF-8 byte
length and serialized SHA-256 are recorded, alongside a hash of the UTF-8 corpus text with LF-normalized newlines (so
Windows checkouts reproduce the same report).
Encoding uses `disallowed_special=()` so tool text is counted as ordinary text.

There are 48 observations: 12 schemas × 2 serializations × 2 encodings. Each of the
four groups has 12 observations. Signed token errors are stored per observation;
group statistics use absolute percentage error:

`100 * abs(estimated_tokens - exact_tokens) / exact_tokens`

The mean is the unweighted arithmetic mean over schemas. p95 uses nearest rank:
sort the errors and select `ceil(0.95 * n)`, using 1-based indexing. **With n=12,
p95 equals the worst case**; it is not a robust population percentile estimate.
The JSON report retains raw integer counts and six-decimal aggregate statistics.
Ties for worst case use the first case in committed corpus order. No timestamp is
included, making an unchanged rerun byte-for-byte comparable.

## What is not measured

- No API calls, billing, model selection, chat wrappers or provider-specific framing.
- No benchmark of the parameter-based fallback in `estimate_tool_tokens`.
- The raw-schema branch's extra `+8` is disclosed as an unmeasured heuristic, not
  added to the reference counts or presented as exact overhead.
- Tokenizers are reference encodings, not evidence that every model uses these tables.
- The default scanner still uses the original estimator. Changing it would alter
  token-budget rules and cost projections and needs separate calibration/regressions.

The script-generated [report](token_report.json) shows large errors on these cases;
it does not support the previous ~8% claim. Do not replace it with another broad
claim based only on this corpus.

The first tokenizer run may fetch encoding tables into tiktoken's cache, where
upstream verifies their hashes. Install/setup therefore needs network access;
repeated measurements can run with cached tables. Tokenizer dependencies are
optional and are never imported by the runtime scanner.
