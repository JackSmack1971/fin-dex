#!/usr/bin/env python3
"""Deterministically replay point-price indicators from retained JSON only."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any


def ema(values: list[float], period: int) -> list[float]:
    """Recursive EMA with the first observed value as the seed."""
    if period < 1 or not values:
        raise ValueError("EMA requires a positive period and at least one observation")
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def replay(closes: list[float]) -> dict[str, Any]:
    if not closes or any(not isinstance(x, (int, float)) or isinstance(x, bool)
                         or not math.isfinite(x) for x in closes):
        raise ValueError("close series must contain finite numeric values")
    close = [float(x) for x in closes]
    last = close[-1]
    averages = {str(period): sum(close[-period:]) / period
                for period in (7, 20, 50, 90) if len(close) >= period}
    rsi = None
    if len(close) >= 15:
        changes = [right - left for left, right in zip(close[-15:-1], close[-14:])]
        gains = sum(max(change, 0.0) for change in changes) / 14
        losses = sum(max(-change, 0.0) for change in changes) / 14
        rsi = 50.0 if gains == 0 and losses == 0 else (100.0 if losses == 0 else 100 - 100 / (1 + gains / losses))
    fast, slow = ema(close, 12), ema(close, 26)
    macd = [left - right for left, right in zip(fast, slow)]
    signal = ema(macd, 9)
    peak = close[0]
    drawdown = 0.0
    for value in close:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return {
        "close_usd": last,
        "returns_pct": {f"{days}d": (last / close[-days - 1] - 1) * 100
                        for days in (7, 30, 90) if len(close) > days},
        "sma_usd": averages,
        "rsi14": rsi,
        "macd12_26_usd": macd[-1],
        "macd_signal9_usd": signal[-1],
        "macd_histogram_usd": macd[-1] - signal[-1],
        "max_drawdown_pct": drawdown * 100,
        "atr14": "UNAVAILABLE: point-price observations do not contain OHLC candles",
        "methodology": {
            "ema_seed": "first observed close; alpha=2/(period+1)",
            "macd": "EMA(12)-EMA(26) over the full series; signal is EMA(9) of the MACD series, seeded with its first value",
            "rsi": "RSI(14) uses the arithmetic mean of the last 14 close-to-close gains and losses (simple-average RSI, not Wilder smoothing)",
            "sma": "arithmetic mean of the last N closes; reported only when N observations exist",
            "atr": "unavailable because point-price observations do not supply open, high, and low",
        },
    }


COMPLETE_FIELDS = (
    "close_2026_10_18_usd", "returns_pct.7d", "returns_pct.30d", "returns_pct.90d",
    "sma_usd.7", "sma_usd.20", "sma_usd.50", "sma_usd.90", "rsi14",
    "macd12_26_usd", "macd_signal9_usd", "macd_histogram_usd",
    "max_drawdown_pct", "atr14",
)


def _value(obj: dict[str, Any], path: str) -> Any:
    value: Any = obj
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def compare(replayed: dict[str, Any], stored: dict[str, Any],
            expected_fields: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
    """Return explicit MATCH, MISMATCH, or NOT_REPORTED for expected fields."""
    fields = expected_fields if expected_fields is not None else tuple(
        [key for key in ("rsi14", "macd12_26_usd", "macd_signal9_usd",
                         "macd_histogram_usd", "max_drawdown_pct") if key in stored]
        + [f"sma_usd.{period}" for period in stored.get("sma_usd", {})]
    )
    results = []
    for field in fields:
        replay_field = {"close_2026_10_18_usd": "close_usd"}.get(field, field)
        actual, expected = _value(replayed, replay_field), _value(stored, field)
        if expected is None:
            results.append({"indicator": field, "status": "NOT_REPORTED"})
            continue
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            digits = max(0, len(str(expected).partition(".")[2]))
            tolerance = 0.5 * (10 ** -digits) + 1e-12
            matched = abs(actual - expected) <= tolerance
        else:
            tolerance = None
            matched = (actual == expected or
                       isinstance(actual, str) and isinstance(expected, str)
                       and actual.startswith("UNAVAILABLE:") and expected.startswith("UNAVAILABLE:"))
        result = {"indicator": field, "status": "MATCH" if matched else "MISMATCH",
                  "replayed": actual, "stored": expected}
        if tolerance is not None:
            result["tolerance"] = tolerance
        results.append(result)
    return results


def replay_run(history_path: Path, calculations_path: Path) -> dict[str, Any]:
    history = json.loads(history_path.read_text(encoding="utf-8"))
    calculations = json.loads(calculations_path.read_text(encoding="utf-8"))
    assets, results = history.get("assets"), calculations.get("assets")
    if not isinstance(assets, dict) or not isinstance(results, dict):
        raise ValueError("history and calculations must contain asset objects")
    output = []
    for asset, rows in assets.items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) or "price" not in row for row in rows):
            raise ValueError(f"invalid close observations for {asset}")
        timestamps = [row.get("timestamp") for row in rows]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise ValueError(f"timestamps must be unique and chronological for {asset}")
        stored_asset = results.get(asset)
        if not isinstance(stored_asset, dict):
            output.append({"asset": asset, "status": "NO_STORED_COMPARISON"})
            continue
        actual = replay([row["price"] for row in rows])
        field_results = compare(actual, stored_asset, COMPLETE_FIELDS)
        mismatches = [item for item in field_results if item["status"] == "MISMATCH"]
        unreported = [item for item in field_results if item["status"] == "NOT_REPORTED"]
        status = "MISMATCH" if mismatches else "INCOMPLETE" if unreported else "MATCH"
        output.append({"asset": asset, "observation_count": len(rows),
                       "status": status,
                       "replayed": actual, "stored": stored_asset,
                       "field_results": field_results,
                       "mismatches": mismatches, "not_reported": unreported})
    return {"status": "MATCH" if all(x["status"] == "MATCH" for x in output) else
            "MISMATCH" if any(x["status"] == "MISMATCH" for x in output) else "INCOMPLETE",
            "history_path": str(history_path), "calculations_path": str(calculations_path),
            "network_calls_made": False, "assets": output}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path)
    parser.add_argument("calculations", type=Path)
    args = parser.parse_args()
    try:
        output = replay_run(args.history, args.calculations)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if output["status"] == "MATCH" else 1


if __name__ == "__main__":
    raise SystemExit(main())
