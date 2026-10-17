"""Read-only exact-pair research with a bounded DexPaprika fallback."""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from dexscreener_public import DexScreenerClient, ProviderError as DexScreenerError
from dexpaprika_public import DexPaprikaClient, ProviderError as DexPaprikaError


# Only same-spelling network IDs with documented support are mapped. Unknown
# network aliases fail closed instead of risking a cross-chain pool lookup.
DEXPAPRIKA_NETWORKS = {
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "optimism": "optimism",
    "avalanche": "avalanche",
    "bsc": "bsc",
    "solana": "solana",
}
FALLBACK_ERRORS = {"NOT_FOUND", "RATE_LIMITED", "TIMEOUT", "NETWORK_ERROR"}


def _fallback_eligible(error: DexScreenerError) -> bool:
    if error.status in FALLBACK_ERRORS:
        return True
    # Generic HTTP errors are eligible only for server-side 5xx responses.
    match = re.search(r"HTTP (\d{3})", str(error))
    return error.status == "HTTP_ERROR" and bool(match) and match.group(1).startswith("5")


def _has_observation(result: Any) -> bool:
    if not isinstance(result, dict):
        raise DexScreenerError("MALFORMED_DATA", "DEX Screener client returned a non-object envelope")
    count = result.get("pair_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise DexScreenerError("MALFORMED_DATA", "DEX Screener client returned an invalid pair_count")
    return count > 0


class DexPairResearchClient:
    """Query DEX Screener first, then DexPaprika for the same exact supported pool.

    At most one request is made to each provider per call. Authentication,
    payment, forbidden, malformed, and identity errors stop without fallback.
    """

    def __init__(self, dexscreener: Any | None = None, dexpaprika: Any | None = None):
        self.dexscreener = dexscreener or DexScreenerClient()
        self.dexpaprika = dexpaprika or DexPaprikaClient()

    def pair(self, chain_id: str, pair_address: str) -> dict[str, Any]:
        reason: str
        try:
            primary = self.dexscreener.pair(chain_id, pair_address)
            if _has_observation(primary):
                return {
                    "status": "RESPONDED",
                    "fallback_used": False,
                    "fallback_reason": None,
                    "provider_observation": primary,
                }
            reason = "NO_OBSERVATION"
        except DexScreenerError as exc:
            if not _fallback_eligible(exc):
                raise
            reason = exc.status

        network = DEXPAPRIKA_NETWORKS.get(chain_id)
        if network is None:
            raise DexScreenerError(
                "FALLBACK_UNSUPPORTED_CHAIN",
                f"no verified DexPaprika network mapping for {chain_id!r}; primary result: {reason}",
            ) from None
        try:
            secondary = self.dexpaprika.pool_snapshot(network, pair_address)
        except DexPaprikaError as exc:
            raise DexPaprikaError(
                exc.status,
                f"DexPaprika fallback failed after DEX Screener {reason}: {exc}",
            ) from None

        data = secondary["data"]
        return {
            "status": "RESPONDED",
            "fallback_used": True,
            "fallback_reason": reason,
            "provider_observation": {
                "provider": secondary["provider"],
                "upstream_origin": secondary["upstream_origin"],
                "endpoint": secondary["endpoint"],
                "source_url": secondary["source_url"],
                "requested_chain_id": chain_id,
                "requested_pair_address": pair_address,
                "retrieved_at_utc": secondary["retrieved_at_utc"],
                "observation_timestamp": None,
                "observation_timestamp_note": (
                    "DexPaprika price_time/last_updated are preserved in the source payload; "
                    "they are not established as timestamps for liquidity or volume."
                ),
                "identity_status": "VERIFIED",
                "pair_count": 1,
                "data": {
                    "chainId": data["chain"],
                    "pairAddress": data["id"],
                    "dexId": data.get("dex_id"),
                    "baseToken": {"address": data["base_token_id"]},
                    "quoteToken": {"address": data["quote_token_id"]},
                    "priceUsd": data.get("last_price_usd"),
                    "liquidity": {"usd": data.get("liquidity_usd")},
                    "volume": {
                        timeframe: values.get("volume_usd")
                        for timeframe, values in data.items()
                        if timeframe in {"24h", "6h", "1h", "30m", "15m", "5m"}
                        and isinstance(values, dict)
                    },
                    "txns": {
                        timeframe: {
                            "buys": values.get("buys"),
                            "sells": values.get("sells"),
                        }
                        for timeframe, values in data.items()
                        if timeframe in {"24h", "6h", "1h", "30m", "15m", "5m"}
                        and isinstance(values, dict)
                        and ("buys" in values or "sells" in values)
                    },
                    "priceChange": {
                        timeframe: values.get("last_price_usd_change")
                        for timeframe, values in data.items()
                        if timeframe in {"24h", "6h", "1h", "30m", "15m", "5m"}
                        and isinstance(values, dict)
                        and "last_price_usd_change" in values
                    },
                    "buySellVolumeUsd": {
                        timeframe: {
                            "buys": values.get("buy_usd"),
                            "sells": values.get("sell_usd"),
                        }
                        for timeframe, values in data.items()
                        if timeframe in {"24h", "6h", "1h", "30m", "15m", "5m"}
                        and isinstance(values, dict)
                        and ("buy_usd" in values or "sell_usd" in values)
                    },
                    "source_payload": data,
                },
                "caveats": secondary["caveats"] + [
                    "This is the same chain and pool address. DexPaprika's last_price_usd_change value is passed through without conversion; retain its source field and do not assume equivalence to DEX Screener definitions. Upstream independence remains unknown."
                ],
            },
        }

    def token_pools(self, chain_id: str, token_address: str) -> dict[str, Any]:
        """Search pools for an exact EVM contract or Solana mint; preserve first-page coverage."""
        reason: str
        try:
            primary = self.dexscreener.token_pairs(chain_id, token_address)
            if _has_observation(primary):
                return {
                    "status": "RESPONDED",
                    "fallback_used": False,
                    "fallback_reason": None,
                    "provider_observation": primary,
                }
            reason = "NO_OBSERVATION"
        except DexScreenerError as exc:
            if not _fallback_eligible(exc):
                raise
            reason = exc.status

        network = DEXPAPRIKA_NETWORKS.get(chain_id)
        if network is None:
            raise DexScreenerError(
                "FALLBACK_UNSUPPORTED_CHAIN",
                f"no verified DexPaprika network mapping for {chain_id!r}; primary result: {reason}",
            ) from None
        try:
            secondary = self.dexpaprika.token_pools(network, token_address, limit=100)
        except DexPaprikaError as exc:
            raise DexPaprikaError(
                exc.status,
                f"DexPaprika fallback failed after DEX Screener {reason}: {exc}",
            ) from None

        rows = secondary["data"]["results"]
        pairs = []
        for row in rows:
            token_ids = [token["id"] for token in row["tokens"]]
            pairs.append({
                "chainId": row["chain"],
                "pairAddress": row["id"],
                "dexId": row.get("dex_id"),
                "tokens": [{"address": token_id} for token_id in token_ids],
                "base_quote_orientation": "UNVERIFIED",
                "priceUsd": row.get("price_usd"),
                "liquidity": {"usd": row.get("liquidity_usd")},
                "volume": {"h24": row.get("volume_usd_24h")},
                "source_payload": row,
            })
        return {
            "status": "RESPONDED" if pairs else "NO_OBSERVATION",
            "fallback_used": True,
            "fallback_reason": reason,
            "provider_observation": {
                "provider": secondary["provider"],
                "upstream_origin": secondary["upstream_origin"],
                "endpoint": secondary["endpoint"],
                "source_url": secondary["source_url"],
                "requested_chain_id": chain_id,
                "requested_token_address": token_address,
                "retrieved_at_utc": secondary["retrieved_at_utc"],
                "observation_timestamp": None,
                "observation_timestamp_note": "Pool-search rows do not provide a verified liquidity/volume as-of timestamp; base/quote orientation is not documented.",
                "identity_status": "VERIFIED" if pairs else "NO_OBSERVATION",
                "pair_count": len(pairs),
                "coverage_status": secondary["coverage_status"],
                "has_next_page": secondary["has_next_page"],
                "next_cursor": secondary["next_cursor"],
                "data": {"pairs": pairs},
                "source_payload": secondary["data"],
                "caveats": secondary["caveats"] + [
                    "Only the first DexPaprika search page was requested; additional pages are not fetched automatically.",
                    "DEX Screener and DexPaprika pool-search ranking, field definitions, and upstream independence are not established as equivalent.",
                ],
            },
        }

    def pool_ohlcv(self, chain_id: str, pair_address: str, *, start: str = "-24h",
                   interval: str = "1h", limit: int = 24) -> dict[str, Any]:
        """Retrieve bounded pool candles from DexPaprika (DEX Screener has no OHLCV route)."""
        network = DEXPAPRIKA_NETWORKS.get(chain_id)
        if network is None:
            raise DexPaprikaError("UNSUPPORTED_CHAIN", f"no verified DexPaprika network mapping for {chain_id!r}")
        result = self.dexpaprika.pool_ohlcv(
            network, pair_address, start=start, interval=interval, limit=limit,
        )
        return {
            "status": "RESPONDED",
            "fallback_used": False,
            "fallback_reason": None,
            "provider_observation": {
                **result,
                "requested_chain_id": chain_id,
                "requested_pair_address": pair_address,
                "observation_timestamp": None,
                "data": {"candles": result["data"]},
                "caveats": result["caveats"] + [
                    "DEX Screener does not expose an OHLCV endpoint; this is a direct DexPaprika pool-candle route, not a fallback observation."
                ],
            },
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair", help="retrieve one exact pool by chain and pair address")
    pair.add_argument("--chain", required=True)
    pair.add_argument("--pair", required=True)
    token = sub.add_parser("token-pools", help="retrieve pools containing one exact token address")
    token.add_argument("--chain", required=True)
    token.add_argument("--token", required=True)
    ohlcv = sub.add_parser("pool-ohlcv", help="retrieve a bounded DexPaprika candle window for one pool")
    ohlcv.add_argument("--chain", required=True)
    ohlcv.add_argument("--pair", required=True)
    ohlcv.add_argument("--start", default="-24h")
    ohlcv.add_argument("--interval", choices=("1h", "6h", "12h"), default="1h")
    ohlcv.add_argument("--limit", type=int, default=24)
    args = parser.parse_args(argv)
    try:
        client = DexPairResearchClient()
        if args.command == "pair":
            result = client.pair(args.chain, args.pair)
        elif args.command == "token-pools":
            result = client.token_pools(args.chain, args.token)
        else:
            result = client.pool_ohlcv(args.chain, args.pair, start=args.start,
                                       interval=args.interval, limit=args.limit)
    except (DexScreenerError, DexPaprikaError, ValueError, KeyError) as exc:
        print(json.dumps({"status": getattr(exc, "status", "INVALID_DATA"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
