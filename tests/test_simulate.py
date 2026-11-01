"""Tests for `vexar-mcp simulate`: cost projection built on the scanner's own estimator."""

from __future__ import annotations

import json
from datetime import date, timedelta
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
    PRICING_MANIFEST,
    PRICING_TABLE,
    REVIEW_WINDOW_DAYS,
    CostProjection,
    PricingError,
    UnknownModelError,
    load_pricing_table,
    parse_pricing_table,
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

    def test_every_entry_carries_its_own_review_date(self) -> None:
        # Per-entry, not one date for the file: adding a model must not backdate it to whatever
        # the oldest review in the table happens to be.
        assert PRICING_TABLE.entries
        for name, entry in PRICING_TABLE.entries.items():
            assert entry.model == name
            assert isinstance(entry.reviewed_on, date)

    def test_table_as_of_is_the_oldest_entry(self) -> None:
        # The single date shown in output has to describe the weakest entry, not the newest.
        oldest = min(e.reviewed_on for e in PRICING_TABLE.entries.values())
        assert oldest.strftime("%Y-%m") == PRICING_AS_OF


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


def _manifest(*, reviewed: date, price: float = 3.0, model: str = "claude-sonnet-4.5") -> dict:
    """A one-model manifest document, for exercising review-window logic at a fixed date."""
    return {
        "schema": "vexar-mcp-pricing/v1",
        "models": {model: {"input_usd_per_mtok": price, "reviewed_on": reviewed.isoformat()}},
    }


class TestReviewWindow:
    """The table must not be able to age quietly.

    Every assertion here uses a fixed `today` and a synthetic table. A test that only looked at
    the shipped manifest could not be written without waiting six months, and a test that edited
    the manifest to simulate age would be testing a file it had just broken.
    """

    TODAY = date(2026, 11, 1)

    def test_a_fresh_entry_is_not_stale(self) -> None:
        table = parse_pricing_table(_manifest(reviewed=date(2026, 10, 1)))
        assert table.stale(self.TODAY) == ()

    def test_an_entry_exactly_at_the_window_is_still_fresh(self) -> None:
        # The boundary is "older than", not "as old as": off-by-one here is a false alarm that
        # teaches people to ignore the warning.
        edge = self.TODAY - timedelta(days=REVIEW_WINDOW_DAYS)
        table = parse_pricing_table(_manifest(reviewed=edge))
        assert table.stale(self.TODAY) == ()

    def test_one_day_past_the_window_is_stale(self) -> None:
        edge = self.TODAY - timedelta(days=REVIEW_WINDOW_DAYS + 1)
        table = parse_pricing_table(_manifest(reviewed=edge))
        assert [e.model for e in table.stale(self.TODAY)] == ["claude-sonnet-4.5"]

    def test_stale_entries_are_ordered_oldest_first(self) -> None:
        table = parse_pricing_table(
            {
                "models": {
                    "recent": {"input_usd_per_mtok": 1.0, "reviewed_on": "2026-10-01"},
                    "older": {"input_usd_per_mtok": 2.0, "reviewed_on": "2025-01-01"},
                    "oldest": {"input_usd_per_mtok": 3.0, "reviewed_on": "2024-01-01"},
                }
            }
        )
        assert [e.model for e in table.stale(self.TODAY)] == ["oldest", "older"]

    def test_a_future_date_reads_as_fresh_rather_than_negative(self) -> None:
        # A clock skewed forward must not produce an age of -400 and index a weird list.
        table = parse_pricing_table(_manifest(reviewed=date(2099, 1, 1)))
        assert table.stale(self.TODAY) == ()
        assert table.entries["claude-sonnet-4.5"].age_days(self.TODAY) == 0

    def test_simulate_warns_when_the_priced_model_has_aged_out(self, clean_report) -> None:
        stale = parse_pricing_table(
            _manifest(reviewed=self.TODAY - timedelta(days=REVIEW_WINDOW_DAYS + 30))
        )
        projection = simulate(clean_report, pricing=stale, today=self.TODAY)

        assert projection.pricing_stale is True
        warning = " ".join(projection.notes)
        assert "last reviewed on" in warning
        assert "claude-sonnet-4.5" in warning
        # A warning nobody can act on is noise; this one names the file to edit.
        assert PRICING_MANIFEST in warning

    def test_the_rendered_header_says_review_overdue(self, clean_report) -> None:
        # The header is the line people screenshot, so the verdict has to reach it.
        stale = parse_pricing_table(
            _manifest(reviewed=self.TODAY - timedelta(days=REVIEW_WINDOW_DAYS + 1))
        )
        text = render_projection(simulate(clean_report, pricing=stale, today=self.TODAY))
        assert "REVIEW OVERDUE" in text.splitlines()[0]
        assert "WARNING:" in text

    def test_a_fresh_table_prints_the_unchanged_header(self, clean_report) -> None:
        fresh = parse_pricing_table(_manifest(reviewed=self.TODAY))
        text = render_projection(simulate(clean_report, pricing=fresh, today=self.TODAY))
        assert "REVIEW OVERDUE" not in text
        assert f"priced {PRICING_AS_OF})" in text.splitlines()[0]

    def test_an_unrelated_stale_entry_still_warns_but_names_itself(self, clean_report) -> None:
        # Priced model is fresh, another is not: still a table-level warning, different sentence.
        table = parse_pricing_table(
            {
                "models": {
                    "claude-sonnet-4.5": {
                        "input_usd_per_mtok": 3.0,
                        "reviewed_on": self.TODAY.isoformat(),
                    },
                    "ancient-model": {"input_usd_per_mtok": 9.0, "reviewed_on": "2020-01-01"},
                }
            }
        )
        projection = simulate(clean_report, pricing=table, today=self.TODAY)
        warning = " ".join(projection.notes)
        assert projection.pricing_stale is True
        assert "ancient-model" in warning
        assert "still within it" in warning

    def test_a_caller_supplied_price_is_not_accused_of_being_stale(self, clean_report) -> None:
        # --input-price is the user's own contract rate; the shipped table's age says nothing
        # about it, and warning there would train people to ignore the warning.
        stale = parse_pricing_table(_manifest(reviewed=date(2019, 1, 1)))
        projection = simulate(clean_report, input_price=0.5, pricing=stale, today=self.TODAY)
        assert projection.pricing_stale is False
        assert projection.notes == ()

    def test_json_output_exposes_the_verdict(self, clean_report) -> None:
        stale = parse_pricing_table(_manifest(reviewed=date(2019, 1, 1)))
        payload = simulate(clean_report, pricing=stale, today=self.TODAY).as_dict()
        assert payload["pricing_stale"] is True
        assert payload["pricing_review_window_days"] == REVIEW_WINDOW_DAYS


class TestShippedManifest:
    """Guards on the real file this build ships."""

    def test_no_shipped_price_is_past_its_review_window(self) -> None:
        """The acceptance criterion, as a test: an aged-out price is a red suite.

        This is meant to fail on a calendar date with no code change. When it does, the fix is
        one line: verify each flagged model's current list price with the vendor and update its
        `reviewed_on` in src/mcp_doctor/simulate/pricing.json. Do not widen REVIEW_WINDOW_DAYS
        to make it pass — that is the same as deleting the test.
        """
        overdue = PRICING_TABLE.stale(date.today())
        assert not overdue, (
            "price table is past its review window: "
            + ", ".join(f"{e.model} (reviewed {e.reviewed_on.isoformat()})" for e in overdue)
            + f". Re-check each rate and update reviewed_on in {PRICING_MANIFEST}."
        )

    def test_manifest_is_declared_as_package_data(self) -> None:
        """A data file that is not shipped is a crash on `pip install`, not a test failure.

        Editable installs read straight from the source tree, so this is invisible in local
        runs and only breaks for users. Pin the declaration instead of trusting it.
        """
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        section = pyproject.split("[tool.setuptools.package-data]", 1)[1].split("\n[", 1)[0]
        assert "simulate/pricing.json" in section

    def test_the_manifest_is_readable_as_packaged_data(self) -> None:
        assert PRICING_TABLE.entries
        assert PRICING_TABLE.source == PRICING_MANIFEST


class TestManifestValidation:
    """A malformed manifest must fail loudly at load, not produce a quietly wrong projection."""

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            ({}, "no 'models'"),
            ({"models": {}}, "no 'models'"),
            ({"models": {"m": {}}}, "numeric input_usd_per_mtok"),
            ({"models": {"m": {"input_usd_per_mtok": "3.0"}}}, "numeric input_usd_per_mtok"),
            ({"models": {"m": {"input_usd_per_mtok": 0}}}, "non-positive"),
            ({"models": {"m": {"input_usd_per_mtok": -1}}}, "non-positive"),
            ({"models": {"m": {"input_usd_per_mtok": 3.0}}}, "reviewed_on"),
            (
                {"models": {"m": {"input_usd_per_mtok": 3.0, "reviewed_on": "October"}}},
                "not an ISO date",
            ),
            ({"models": {"m": []}}, "must be an object"),
        ],
    )
    def test_bad_entries_are_rejected_with_a_useful_message(
        self, document: dict, expected: str
    ) -> None:
        with pytest.raises(PricingError) as exc:
            parse_pricing_table(document)
        assert expected in str(exc.value)

    def test_a_non_object_manifest_is_rejected(self) -> None:
        with pytest.raises(PricingError, match="must be a JSON object"):
            parse_pricing_table(["not", "a", "manifest"])

    def test_a_non_string_model_name_is_rejected(self) -> None:
        # JSON keys are always strings, so this only happens via a hand-built document — but a
        # silently accepted integer key would make `resolve_price("1")` behave strangely.
        with pytest.raises(PricingError, match="model names must be strings"):
            parse_pricing_table({"models": {1: {"input_usd_per_mtok": 3.0}}})

    def test_an_explicit_path_overrides_the_shipped_manifest(self, tmp_path: Path) -> None:
        custom = tmp_path / "my-prices.json"
        custom.write_text(json.dumps(_manifest(reviewed=date(2026, 5, 1), price=7.5)), "utf-8")
        table = load_pricing_table(custom)
        assert table.price("claude-sonnet-4.5") == 7.5
        # Errors must name the file the reader can actually edit.
        assert table.source == str(custom)

    def test_a_boolean_price_is_not_a_price(self) -> None:
        # bool is an int subclass, and True would otherwise pass as $1.00/1M tokens.
        with pytest.raises(PricingError):
            parse_pricing_table(
                {"models": {"m": {"input_usd_per_mtok": True, "reviewed_on": "2026-10-01"}}}
            )


class TestDocumentedOutputIsReal:
    """The usage reference quotes a simulate header; keep it byte-identical to the real one."""

    def test_quoted_pricing_line_matches_rendered_output(self, clean_report) -> None:
        usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
        fresh = parse_pricing_table(_manifest(reviewed=date.today()))
        header = render_projection(simulate(clean_report, pricing=fresh)).splitlines()[0]
        assert "MODEL: claude-sonnet-4.5" in usage
        assert header.startswith("MODEL: claude-sonnet-4.5")
        assert f"priced {PRICING_AS_OF})" in usage
