import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "indicator_replay", ROOT / "scripts" / "indicator_replay.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
FIXTURE = ROOT / "tests" / "fixtures" / "macd_conflict.json"


def test_conflicting_macd_signal_seed_is_detected():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    replay = module.replay(fixture["closes"])
    specialist = {"macd12_26_usd": replay["macd12_26_usd"],
                  "macd_signal9_usd": replay["macd_signal9_usd"] + 0.25,
                  "macd_histogram_usd": replay["macd_histogram_usd"] - 0.25}
    comparisons = module.compare(replay, specialist)
    mismatches = [x for x in comparisons if x["status"] == "MISMATCH"]
    assert {x["indicator"] for x in mismatches} == {
        "macd_signal9_usd", "macd_histogram_usd"}
    assert "first observed close" in replay["methodology"]["ema_seed"]


def test_rsi_method_is_explicit_and_atr_remains_unavailable_without_ohlc():
    result = module.replay(list(range(1, 31)))
    assert result["methodology"]["rsi"].startswith("RSI(14) uses the arithmetic mean")
    assert result["atr14"].startswith("UNAVAILABLE")


def test_expected_missing_fields_are_not_reported_and_cannot_pass_complete_replay():
    recomputed = module.replay(list(range(1, 31)))
    partial = {"macd12_26_usd": recomputed["macd12_26_usd"]}
    statuses = module.compare(recomputed, partial, ["macd12_26_usd", "rsi14"])
    assert [item["status"] for item in statuses] == ["MATCH", "NOT_REPORTED"]
    assert all(item["status"] != "MATCH" for item in
               module.compare(recomputed, partial, ["rsi14"]))


def test_aero_retained_calculations_match_independent_point_price_replay():
    history = ROOT.parents[2] / "research-runs" / "market-analyst" / "coinpaprika-daily-history.json"
    calculations = ROOT.parents[2] / "research-runs" / "20261018-aero" / "calculations.json"
    if not history.is_file() or not calculations.is_file():
        return  # These retained, user-owned research artifacts are not test fixtures.
    result = module.replay_run(history, calculations)
    assert result["status"] == "MATCH", result["assets"]
    assert all(item["status"] == "MATCH" for asset in result["assets"]
               for item in asset["field_results"])
