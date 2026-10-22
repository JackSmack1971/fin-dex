# Testing

Single test command, run from the repository root (requires `pytest` and `jsonschema`):

    python -m pytest .agents/skills/crypto-mcp-research/tests paper-testing/tests -q -rs

It collects every test file in both packages, including the pytest-style
`test_instruction_invariants.py`. `-rs` reports any skipped test with its reason.
The suite is offline: no secrets, provider calls, or live MCP servers.
