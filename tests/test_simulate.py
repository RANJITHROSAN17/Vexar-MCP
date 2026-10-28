"""Tests for `vexar-mcp simulate`: cost projection built on the scanner's own estimator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_doctor.cli import main
from mcp_doctor.config import load_config
from mcp_doctor.engine import scan_paths
from mcp_doctor.models import ScanStatus
from mcp_doctor.simulate.cost_model import (
    DEFAULT_CALLS_PER_DAY,
    MODEL_PRICES_USD_PER_MTOK,
    PRICING_AS_OF,
    CostProjection,
    UnknownModelError,
    per_month_usd,
    render_projection,
    resolve_price,
    simulate,
)

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "examples" / "clean_server.py"


@pytest.fixture(scope="module")
def clean_report():
    cfg = load_config(start_dir=ROOT)
    return scan_paths([CLEAN], cfg)


class TestPricing:
    def test_known_model_resolves_to_table_price(self) -> None:
        label, price = resolve_price("claude-sonnet-4.5")
        assert label == "claude-sonnet-4.5"
        assert price == MODEL_PRICES_USD_PER_MTOK["claude-sonnet-4.5"]

    def test_unknown_model_raises_rather_than_defaulting(self) -> None:
        # The regression this guards: a silent default price makes a guess look measured.
        with pytest.raises(UnknownModelError) as exc:
            resolve_price("some-model-that-does-not-exist")
        assert "input-price" in str(exc.value)
        assert "some-model-that-does-not-exist" in str(exc.value)

    def test_unknown_model_is_allowed_with_an_explicit_price(self) -> None:
        label, price = resolve_price("internal-finetune", input_price=0.75)
        assert label == "internal-finetune"
        assert price == 0.75

    def test_negative_price_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            resolve_price("claude-sonnet-4.5", input_price=-1.0)

    def test_pricing_table_is_dated(self) -> None:
        assert PRICING_AS_OF
        assert all(price > 0 for price in MODEL_PRICES_USD_PER_MTOK.values())


class TestProjection:
    def test_catalog_tokens_come_from_the_scan_summary(self, clean_report) -> None:
        projection = simulate(clean_report, model="claude-sonnet-4.5")
        assert projection.catalog_tokens == clean_report.summary.total_estimated_tokens
        assert projection.tools_scanned == len(clean_report.tools)

    def test_cost_is_linear_in_volume_and_price(self, clean_report) -> None:
        one = simulate(clean_report, calls_per_day=100, input_price=2.0)
        two = simulate(clean_report, calls_per_day=200, input_price=2.0)
        assert two.monthly_usd == pytest.approx(one.monthly_usd * 2)
        assert per_month_usd(1_000_000, 3.0, 1) == pytest.approx(90.0)

    def test_cost_per_call_matches_monthly_over_call_count(self, clean_report) -> None:
        projection = simulate(clean_report, calls_per_day=250, input_price=10.0)
        calls_per_month = 250 * 30
        assert projection.monthly_usd == pytest.approx(
            projection.cost_per_call_usd * calls_per_month
        )

    def test_top_tools_are_ordered_and_bounded(self, clean_report) -> None:
        projection = simulate(clean_report)
        top = projection.top(limit=1)
        assert len(top) == 1
        assert top[0].tokens == max(t.tokens for t in projection.tools)

    def test_trim_savings_never_exceeds_the_total(self, clean_report) -> None:
        projection = simulate(clean_report)
        assert projection.trim_savings(len(projection.tools)) == pytest.approx(
            projection.monthly_usd
        )
        assert projection.trim_savings(0) == 0.0

    def test_incomplete_scan_is_labelled_a_lower_bound(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken_server.py"
        broken.write_text("def register(:\n", encoding="utf-8")
        good = tmp_path / "good_server.py"
        good.write_text(CLEAN.read_text(encoding="utf-8"), encoding="utf-8")
        report = scan_paths([tmp_path], load_config(start_dir=tmp_path))
        assert report.status is ScanStatus.PARTIAL

        projection = simulate(report)
        assert projection.analysis_complete is False
        assert any("LOWER BOUND" in note for note in projection.notes)

    def test_negative_volume_is_rejected(self, clean_report) -> None:
        with pytest.raises(ValueError):
            simulate(clean_report, calls_per_day=-5)

    def test_as_dict_is_json_serialisable(self, clean_report) -> None:
        payload = json.loads(json.dumps(simulate(clean_report).as_dict()))
        assert payload["model"] == "claude-sonnet-4.5"
        assert payload["catalog_tokens_per_turn"] > 0
        assert payload["pricing_as_of"] == PRICING_AS_OF
        assert payload["top_tools"]


class TestRendering:
    def test_terminal_output_names_the_model_volume_and_total(self, clean_report) -> None:
        text = render_projection(simulate(clean_report, calls_per_day=1234))
        assert "claude-sonnet-4.5" in text
        assert "1,234 calls/day" in text
        assert "CATALOG TOTAL" in text

    def test_empty_projection_does_not_divide_by_zero(self) -> None:
        empty = CostProjection(
            model="claude-sonnet-4.5",
            price_per_mtok=3.0,
            calls_per_day=DEFAULT_CALLS_PER_DAY,
            catalog_tokens=0,
            tools=(),
            tools_scanned=0,
        )
        text = render_projection(empty)
        assert "CATALOG TOTAL" in text
        assert "save" not in text


class TestCli:
    def test_simulate_json_round_trip(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["simulate", str(CLEAN), "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["tools_scanned"] >= 1

    def test_unknown_model_exits_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["simulate", str(CLEAN), "--model", "not-a-real-model"])
        assert code == 2
        assert "unknown model" in capsys.readouterr().err

    def test_no_tools_exits_4(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert main(["simulate", str(empty)]) == 4

    def test_incomplete_scan_exits_3_unless_allowed(self, tmp_path: Path) -> None:
        (tmp_path / "bad_server.py").write_text("def broken(:\n", encoding="utf-8")
        assert main(["simulate", str(tmp_path)]) == 3
        assert main(["simulate", str(tmp_path), "--allow-incomplete"]) == 0

    def test_negative_calls_per_day_is_a_config_error(self) -> None:
        assert main(["simulate", str(CLEAN), "--calls-per-day", "-1"]) == 2
