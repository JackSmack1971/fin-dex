import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[4]


def test_coinpaprika_history_artifact_is_exact_contract_bounded_and_policy_conditional():
    route = json.loads((ROOT / ".codex/coinpaprika-history-routing.json").read_text(encoding="utf-8"))
    run = ROOT / "research-runs/2026-10-17-coinpaprika-ethereum-usdc-daily-replay"
    response = json.loads((run / "provider_response.json").read_text(encoding="utf-8"))
    observations = json.loads((run / "observations.json").read_text(encoding="utf-8"))

    assert route["access"]["policy_state"].startswith("CONDITIONAL_PERSONAL_NONCOMMERCIAL_ONLY")
    assert route["route"]["tool"] == response["tool"]
    assert response["query"]["interval"] == "1d"
    assert response["query"]["limit"] <= 10
    assert response["query"]["platformId"] == "eth-ethereum"
    assert response["query"]["contractAddress"].lower() == observations["contract_address"].lower()
    assert response["authorization_header_sent"] is False
    assert response["response_observed_no_later_than_utc"] == "2026-10-17T15:02:18Z"
    ticks = response["ticks"]
    assert observations["coverage"].startswith("7 daily points")
    assert len(ticks) == 7
    assert [tick["timestamp"] for tick in ticks] == sorted(tick["timestamp"] for tick in ticks)
    assert all(tick["price"] > 0 for tick in ticks)
    assert observations["observations"][0]["unit"] == "USD/token"
    assert route["evidence"]["exact_retrieval_timestamp"] == "not supplied by tool result"
