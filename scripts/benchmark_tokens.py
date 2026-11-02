"""Reproducible tokenizer comparison; run from the repository root after installing the extra.

Only the optional benchmark imports tiktoken. This does not measure API usage or
model framing. The first run may download the tokenizer tables into tiktoken's cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from importlib.metadata import version
from pathlib import Path
from typing import Any

from vexar_mcp.tokens import estimate_tokens

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "benchmarks/token_corpus.json"
REPORT = ROOT / "benchmarks/token_report.json"
ENCODINGS = ("cl100k_base", "o200k_base")


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Absolute percentage error; p95 uses nearest rank (ceil(.95 * n))."""
    if not rows:
        raise ValueError("cannot summarize an empty group")
    errors = sorted(row["absolute_percentage_error"] for row in rows)
    worst = max(rows, key=lambda row: row["absolute_percentage_error"])
    return {
        "count": len(rows),
        "mean_absolute_percentage_error": round(sum(errors) / len(errors), 6),
        "p95_absolute_percentage_error": round(errors[math.ceil(0.95 * len(errors)) - 1], 6),
        "worst_absolute_percentage_error": round(errors[-1], 6),
        "worst_case_id": worst["case_id"],
    }


def recorded_version(path: Path = REPORT) -> str | None:
    """The tiktoken version the committed report was generated with, if there is one.

    This is the single source of truth for the pin. A module constant duplicates a value
    that already lives in the report and in pyproject.toml, and only pyproject.toml is
    visible to a dependency bot -- so a bump moved one of the three and left the other two
    behind, which is a red CI job that reads like an incompatibility. Reading the report
    means the drift is reported where it can be acted on: regenerate, then review the diff.
    """
    if not path.exists():
        return None
    try:
        return str(json.loads(path.read_text(encoding="utf8"))["tokenizer"]["version"])
    except (KeyError, json.JSONDecodeError, OSError):
        return None


def measure(corpus_path: Path = CORPUS) -> dict[str, Any]:
    """Compare the same serialized bytes with the estimator and two reference encodings."""
    try:
        import tiktoken
    except ImportError as exc:
        raise RuntimeError(
            'Install the opt-in extra: python -m pip install -e ".[exact-tokenizer]"'
        ) from exc
    installed = version("tiktoken")
    raw = corpus_path.read_text(encoding="utf8").encode("utf8")
    corpus = json.loads(raw)
    if corpus["schema"] != "vexar-token-corpus/v1" or not corpus["cases"]:
        raise ValueError("expected a non-empty vexar-token-corpus/v1 corpus")
    encoders = {name: tiktoken.get_encoding(name) for name in ENCODINGS}
    rows: list[dict[str, Any]] = []
    for case in corpus["cases"]:
        for escaped in (True, False):
            text = json.dumps(case["tool"], ensure_ascii=escaped, separators=(",", ":"))
            estimate = estimate_tokens(text)
            for name, encoder in encoders.items():
                exact = len(encoder.encode(text, disallowed_special=()))
                if exact == 0:
                    raise ValueError("non-empty tool serialized to zero tokens")
                rows.append(
                    {
                        "case_id": case["id"],
                        "encoding": name,
                        "ensure_ascii": escaped,
                        "serialized_utf8_bytes": len(text.encode("utf8")),
                        "serialized_sha256": hashlib.sha256(text.encode("utf8")).hexdigest(),
                        "estimated_tokens": estimate,
                        "exact_tokens": exact,
                        "signed_token_error": estimate - exact,
                        "absolute_percentage_error": 100 * abs(estimate - exact) / exact,
                    }
                )
    groups = []
    for name in ENCODINGS:
        for escaped in (True, False):
            selected = [r for r in rows if r["encoding"] == name and r["ensure_ascii"] == escaped]
            groups.append({"encoding": name, "ensure_ascii": escaped, **summarize(selected)})
    return {
        "schema": "vexar-token-benchmark/v1",
        "corpus_sha256": hashlib.sha256(raw).hexdigest(),
        "tokenizer": {"package": "tiktoken", "version": installed},
        "serialization": {
            "separators": [",", ":"],
            "sort_keys": False,
            "order": "committed JSON key order",
        },
        "method": "100 * abs(estimated - exact) / exact; p95 nearest rank; group by encoding and ensure_ascii",
        "framing": {
            "included_in_measurement": False,
            "raw_schema_path_heuristic_addend": 8,
            "note": "Counts serialized tool JSON only; neither API usage nor model-specific framing. The parameter-based fallback is not benchmarked.",
        },
        "groups": groups,
        "observations": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT)
    parser.add_argument(
        "--check", action="store_true", help="recompute and compare, without writing"
    )
    args = parser.parse_args(argv)
    try:
        result = json.dumps(measure(), ensure_ascii=False, indent=2) + "\n"
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(2, f"benchmark_tokens: {exc}\n")
    if args.check:
        recorded = recorded_version(args.output)
        measured = json.loads(result)["tokenizer"]["version"]
        if recorded is not None and recorded != measured:
            parser.exit(
                2,
                f"benchmark_tokens: the committed report was generated with tiktoken=={recorded}, "
                f"but {measured} is installed. Regenerate with `python scripts/benchmark_tokens.py` "
                f"and review the diff: if only the tokenizer.version line moved, no measurement "
                f"changed.\n",
            )
        if not args.output.exists() or args.output.read_text(encoding="utf8") != result:
            parser.exit(1, "benchmark_tokens: report drifted; regenerate and review the diff\n")
    else:
        args.output.write_text(result, encoding="utf8")
    for group in json.loads(result)["groups"]:
        print(
            f"{group['encoding']} ensure_ascii={group['ensure_ascii']}: "
            f"mean={group['mean_absolute_percentage_error']:.2f}% "
            f"p95={group['p95_absolute_percentage_error']:.2f}% "
            f"worst={group['worst_absolute_percentage_error']:.2f}% ({group['worst_case_id']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
