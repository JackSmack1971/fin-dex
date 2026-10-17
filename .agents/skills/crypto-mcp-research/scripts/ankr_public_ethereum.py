"""Bounded, anonymous, read-only Ethereum ERC-20 Transfer log retrieval via Ankr Public RPC."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

ENDPOINT = "https://rpc.ankr.com/eth"
ROUTE_CONTRACT = Path(__file__).resolve().parents[4] / ".codex" / "ankr-public-ethereum-routing.json"
SOURCE_URL = "https://rpc.ankr.com/eth"
ALLOWED_HOST = "rpc.ankr.com"
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_BLOCK_SPAN = 1000
MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 10
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
HASH32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
HEX_QUANTITY = re.compile(r"^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _assert_route_permitted(route_contract: Path) -> None:
    try:
        contract = json.loads(route_contract.read_text(encoding="utf-8"))
        readiness = contract["access"]["runtime_readiness"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        raise ProviderError("ROUTE_POLICY_UNAVAILABLE", "Ankr route contract missing or invalid; no request sent") from None
    if readiness != "READY_ZERO_CHARGE_REQUALIFIED":
        raise ProviderError("ROUTE_UNAVAILABLE", f"Ankr public route disabled by contract status {readiness!r}; no request sent")


def _validate_request(token_address: str, from_block: int, to_block: int) -> None:
    if not isinstance(token_address, str) or not EVM_ADDRESS.fullmatch(token_address):
        raise ValueError("token_address must be a 20-byte EVM contract address")
    if (not isinstance(from_block, int) or isinstance(from_block, bool)
            or not isinstance(to_block, int) or isinstance(to_block, bool)
            or from_block < 0 or to_block < from_block):
        raise ValueError("block bounds must be non-negative integers with to_block >= from_block")
    if to_block - from_block + 1 > MAX_BLOCK_SPAN:
        raise ValueError(f"block range may cover at most {MAX_BLOCK_SPAN} inclusive blocks")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("UNSAFE_REDIRECT", "Ankr RPC redirect rejected; request stopped")


def _rpc(method: str, params: list[Any], *, timeout: float,
         opener: Callable[..., Any] | None = None) -> Any:
    if method not in {"eth_chainId", "eth_getLogs"}:
        raise ValueError("only the fixed eth_chainId and eth_getLogs methods are permitted")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                      separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(ENDPOINT, data=body, headers={
        "Accept": "application/json", "Content-Type": "application/json", "User-Agent": USER_AGENT,
    }, method="POST")
    if opener is None:
        opener = urllib.request.build_opener(_NoRedirect()).open
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            final_url = response.geturl() if hasattr(response, "geturl") else ENDPOINT
            if status_code < 200 or status_code >= 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {status_code}")
            if (urllib.parse.urlsplit(final_url).scheme != "https"
                    or urllib.parse.urlsplit(final_url).hostname != ALLOWED_HOST):
                raise ProviderError("UNSAFE_REDIRECT", "RPC response did not remain on Ankr's fixed HTTPS host")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        kind = {401: "UNAUTHORIZED", 402: "PAYMENT_REQUIRED", 403: "FORBIDDEN",
                404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
        raise ProviderError(kind, f"Ankr returned HTTP {exc.code}; provider branch stopped") from None
    except ProviderError:
        raise
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or (isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)):
            raise ProviderError("TIMEOUT", "Ankr RPC request timed out") from None
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    except OSError as exc:
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError("RESPONSE_TOO_LARGE", f"response exceeded {MAX_RESPONSE_BYTES} bytes")
    try:
        payload = json.loads(raw.decode("utf-8"),
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "response was not valid UTF-8 JSON") from None
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or payload.get("id") != 1:
        raise ProviderError("MALFORMED_DATA", "response JSON-RPC envelope did not match the request")
    if payload.get("error") is not None:
        error = payload["error"]
        if not isinstance(error, dict):
            raise ProviderError("MALFORMED_DATA", "JSON-RPC error must be an object")
        code, message = error.get("code"), error.get("message")
        if not isinstance(code, int) or isinstance(code, bool) or not isinstance(message, str):
            raise ProviderError("MALFORMED_DATA", "JSON-RPC error is missing a valid code or message")
        if re.search(r"unauthorized|api key required|authenticate your request", message, re.IGNORECASE):
            raise ProviderError("UNAUTHORIZED", f"JSON-RPC {code}: anonymous access requires authentication")
        raise ProviderError("RPC_ERROR", f"JSON-RPC {code}: {message[:300]}")
    if "result" not in payload:
        raise ProviderError("MALFORMED_DATA", "JSON-RPC response is missing result")
    return payload["result"]


def _quantity(value: Any, field: str) -> int:
    if not isinstance(value, str) or not HEX_QUANTITY.fullmatch(value):
        raise ProviderError("MALFORMED_DATA", f"{field} must be a canonical hexadecimal quantity")
    return int(value, 16)


def _parse_log(row: Any, token_address: str, from_block: int, to_block: int) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ProviderError("MALFORMED_DATA", "each log result must be an object")
    address, topics = row.get("address"), row.get("topics")
    if not isinstance(address, str) or not EVM_ADDRESS.fullmatch(address) or address.lower() != token_address.lower():
        raise ProviderError("IDENTITY_MISMATCH", "returned log address did not match the requested token contract")
    if (not isinstance(topics, list) or len(topics) != 3
            or not isinstance(topics[0], str) or topics[0].lower() != TRANSFER_TOPIC):
        raise ProviderError("IDENTITY_MISMATCH", "returned log did not match the ERC-20 Transfer(address,address,uint256) event")
    if not all(isinstance(topic, str) and HASH32.fullmatch(topic) for topic in topics):
        raise ProviderError("MALFORMED_DATA", "Transfer topics must be 32-byte hex values")
    if topics[1][2:26] != "0" * 24 or topics[2][2:26] != "0" * 24:
        raise ProviderError("MALFORMED_DATA", "indexed Transfer addresses must be zero-padded to 32 bytes")
    data = row.get("data")
    if not isinstance(data, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", data):
        raise ProviderError("MALFORMED_DATA", "Transfer data must contain one 32-byte uint256 amount")
    block_number = _quantity(row.get("blockNumber"), "blockNumber")
    if not from_block <= block_number <= to_block:
        raise ProviderError("IDENTITY_MISMATCH", "returned log block fell outside the requested inclusive range")
    block_hash, tx_hash = row.get("blockHash"), row.get("transactionHash")
    if not isinstance(block_hash, str) or not HASH32.fullmatch(block_hash):
        raise ProviderError("MALFORMED_DATA", "blockHash must be 32-byte hex")
    if not isinstance(tx_hash, str) or not HASH32.fullmatch(tx_hash):
        raise ProviderError("MALFORMED_DATA", "transactionHash must be 32-byte hex")
    log_index = _quantity(row.get("logIndex"), "logIndex")
    removed = row.get("removed")
    if not isinstance(removed, bool):
        raise ProviderError("MALFORMED_DATA", "removed must be boolean")
    return {
        "block_number": block_number,
        "block_hash": block_hash,
        "transaction_hash": tx_hash,
        "log_index": log_index,
        "from_address": "0x" + topics[1][-40:],
        "to_address": "0x" + topics[2][-40:],
        "amount_raw_integer": str(int(data, 16)),
        "token_address": address,
        "removed": removed,
        "block_timestamp_utc": None,
    }


class AnkrEthereumPublicClient:
    def __init__(self, *, opener: Callable[..., Any] | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS, utc_clock: Callable[[], str] = _utc_now,
                 route_contract: Path | None = None):
        if timeout <= 0 or timeout > 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        self.opener = opener
        self.timeout = timeout
        self.utc_clock = utc_clock
        self.route_contract = Path(route_contract) if route_contract else ROUTE_CONTRACT

    def transfer_logs(self, token_address: str, from_block: int, to_block: int) -> dict[str, Any]:
        _validate_request(token_address, from_block, to_block)
        _assert_route_permitted(self.route_contract)
        chain_id = _rpc("eth_chainId", [], timeout=self.timeout, opener=self.opener)
        if not isinstance(chain_id, str) or not HEX_QUANTITY.fullmatch(chain_id) or int(chain_id, 16) != 1:
            raise ProviderError("IDENTITY_MISMATCH", "Ankr endpoint did not report Ethereum mainnet chain ID 1")
        logs = _rpc("eth_getLogs", [{
            "address": token_address,
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "topics": [TRANSFER_TOPIC],
        }], timeout=self.timeout, opener=self.opener)
        if not isinstance(logs, list):
            raise ProviderError("MALFORMED_DATA", "eth_getLogs result must be an array")
        if len(logs) > 200_000:
            raise ProviderError("RESPONSE_TOO_LARGE", "eth_getLogs exceeded Ankr's documented 200,000 log limit")
        parsed = [_parse_log(row, token_address, from_block, to_block) for row in logs]
        return {
            "provider": "ankr_public_ethereum_rpc",
            "upstream_origin": "ankr_public_ethereum_rpc; underlying serving node not disclosed per observation",
            "endpoint": "POST https://rpc.ankr.com/eth JSON-RPC eth_getLogs",
            "source_url": SOURCE_URL,
            "chain_id": 1,
            "chain": "ethereum",
            "token_address": token_address,
            "event_signature": "Transfer(address,address,uint256)",
            "event_topic0": TRANSFER_TOPIC,
            "block_range": {"from_inclusive": from_block, "to_inclusive": to_block},
            "retrieved_at_utc": self.utc_clock(),
            "observation_timestamp": None,
            "status": "RESPONDED" if parsed else "NO_OBSERVATION",
            "log_count": len(parsed),
            "data": parsed,
            "units": {"amount_raw_integer": "token base units; decimals and USD value not inferred"},
            "coverage_status": "BOUNDED_RPC_RESPONSE; provider does not attest completeness or reorganization finality",
            "caveats": [
                "Only the requested Ethereum block range and exact token contract were queried.",
                "Transfer logs are gross events; mint/burn records require explicit zero-address treatment before net-flow calculations.",
                "Block timestamps are not included by eth_getLogs and are null; do not convert block windows to UTC event windows without a separate bounded block lookup.",
                "Raw token amounts are base units; token decimals and USD values are not inferred.",
                "A returned empty array is NO_OBSERVATION, not proof of zero transfers or complete index coverage.",
                "Ankr documents its Public plan for internal use; do not redistribute provider service or use this route for third-party access or commercial exploitation without prior written consent.",
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-address", required=True, help="exact 20-byte Ethereum token contract")
    parser.add_argument("--from-block", required=True, type=int, help="inclusive decimal block number")
    parser.add_argument("--to-block", required=True, type=int, help="inclusive decimal block number")
    args = parser.parse_args(argv)
    try:
        result = AnkrEthereumPublicClient().transfer_logs(args.token_address, args.from_block, args.to_block)
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "ankr_public_ethereum_rpc", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
