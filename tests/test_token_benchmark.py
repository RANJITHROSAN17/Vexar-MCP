"""Check the measurement method without a mandatory runtime tokenizer dependency."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from vexar_mcp.tokens import estimate_tokens

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "benchmark_tokens", ROOT / "scripts/benchmark_tokens.py"
)
assert spec is not None and spec.loader is not None
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_error_statistics_use_absolute_errors_and_nearest_rank_p95() -> None:
    rows = [{"absolute_percentage_error": i, "case_id": str(i)} for i in range(1, 21)]
    assert benchmark.summarize(rows) == {
        "count": 20,
        "mean_absolute_percentage_error": 10.5,
        "p95_absolute_percentage_error": 19,
        "worst_absolute_percentage_error": 20,
        "worst_case_id": "20",
    }
    with pytest.raises(ValueError, match="empty"):
        benchmark.summarize([])


def test_corpus_has_provenance_license_and_boundary_cases() -> None:
    corpus = json.loads(benchmark.CORPUS.read_text(encoding="utf8"))
    assert corpus["license"] == "MIT"
    assert "Original synthetic" in corpus["provenance"]
    ids = [case["id"] for case in corpus["cases"]]
    assert len(ids) == len(set(ids)) == 12
    tags = {tag for case in corpus["cases"] for tag in case["tags"]}
    assert {"portuguese", "cjk", "emoji", "nested", "long-identifiers"} <= tags


def test_committed_report_matches_current_estimator_and_serialization() -> None:
    raw = benchmark.CORPUS.read_text(encoding="utf8").encode("utf8")
    cases = {c["id"]: c for c in json.loads(raw)["cases"]}
    report = json.loads(benchmark.REPORT.read_text(encoding="utf8"))
    assert report["corpus_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["tokenizer"]["version"] == benchmark.TOKENIZER_VERSION
    assert report["framing"]["included_in_measurement"] is False
    assert report["framing"]["raw_schema_path_heuristic_addend"] == 8
    rows = report["observations"]
    assert len(rows) == 48
    identities = {(r["case_id"], r["ensure_ascii"], r["encoding"]) for r in rows}
    assert len(identities) == len(rows)
    for row in rows:
        text = json.dumps(
            cases[row["case_id"]]["tool"], ensure_ascii=row["ensure_ascii"], separators=(",", ":")
        )
        assert row["serialized_sha256"] == hashlib.sha256(text.encode("utf8")).hexdigest()
        assert row["serialized_utf8_bytes"] == len(text.encode("utf8"))
        assert row["estimated_tokens"] == estimate_tokens(text)  # No hidden +8.
        error = row["estimated_tokens"] - row["exact_tokens"]
        assert row["signed_token_error"] == error
        assert row["absolute_percentage_error"] == pytest.approx(
            100 * abs(error) / row["exact_tokens"]
        )
    for group in report["groups"]:
        selected = [
            r
            for r in rows
            if (r["encoding"], r["ensure_ascii"]) == (group["encoding"], group["ensure_ascii"])
        ]
        assert {k: group[k] for k in benchmark.summarize(selected)} == benchmark.summarize(selected)


def test_missing_optional_tokenizer_has_actionable_install_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    with pytest.raises(RuntimeError, match="exact-tokenizer"):
        benchmark.measure()


def test_exact_tokenizers_reproduce_committed_counts() -> None:
    pytest.importorskip("tiktoken")
    assert benchmark.measure() == json.loads(benchmark.REPORT.read_text(encoding="utf8"))
    assert benchmark.main(["--check"]) == 0


def test_check_refuses_missing_or_edited_report(tmp_path: Path) -> None:
    pytest.importorskip("tiktoken")
    target = tmp_path / "report.json"
    with pytest.raises(SystemExit) as error:
        benchmark.main(["--check", "--output", str(target)])
    assert error.value.code == 1
    assert not target.exists()
    assert benchmark.main(["--output", str(target)]) == 0
    assert benchmark.main(["--check", "--output", str(target)]) == 0
    target.write_text("{}", encoding="utf8")
    with pytest.raises(SystemExit) as error:
        benchmark.main(["--check", "--output", str(target)])
    assert error.value.code == 1
    assert target.read_text(encoding="utf8") == "{}"


def test_corpus_line_endings_do_not_change_measurement(tmp_path: Path) -> None:
    pytest.importorskip("tiktoken")
    windows_copy = tmp_path / "corpus.json"
    text = benchmark.CORPUS.read_text(encoding="utf8")
    windows_copy.write_bytes(text.replace("\n", "\r\n").encode("utf8"))
    assert benchmark.measure(windows_copy) == benchmark.measure()
