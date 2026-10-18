# Liquid runtime capability discovery

**Retrieved:** 2026-10-18 (UTC; tool inventory read at 04:01 UTC)

**Source:** Runtime tool metadata exposed to this Codex session. This is tool-description inspection only; no Liquid tool was invoked.
**Account/venue response:** None observed. No live account, mode, balances, instruments, or OAuth grant was queried.

## Relevant exposed tools

| Runtime tool | Published behavior | Boundary note |
|---|---|---|
| mcp__codex_apps__co_invest_paper_trading_status | Reports whether paper trading is enabled for the current wallet in this MCP connector. | Describes a wallet-keyed simulator shared across Liquid MCP connectors; does not expose returned status fields without invocation. |
| mcp__codex_apps__co_invest_enable_paper_trading | Enables simulated funds for the current wallet. | State-changing; shared-wallet scope. Not called. |
| mcp__codex_apps__co_invest_disable_paper_trading | Disables paper mode for the wallet. | State-changing; not called. |
| mcp__codex_apps__co_invest_get_portfolio | Reads balances/positions and identifies paper or live mode. | Read operation; not called. |
| mcp__codex_apps__co_invest_suggest_order | Creates a paper-only prefilled order review link; cannot select live mode. | Not an order submission. Requires paper mode already active and a later confirmation in Liquid. Its description warns that the Liquid web app does not inherit connector paper mode and may use real funds. Not called. |
| mcp__codex_apps__co_invest_suggest_trade / ...suggest_trades_batch | Creates one or more prefilled review links for live or paper accounts, depending on current account mode. | Proposal only; paper mode must be confirmed in Liquid before submit. Not called. |
| mcp__codex_apps__co_invest_view_open_orders | Lists open perpetual orders and identifiers. | Read operation; not called. |
| mcp__codex_apps__co_invest_cancel_paper_order | Cancels an open paper order. | State-changing; not called. |
| mcp__codex_apps__co_invest_close_paper_position / ...close_all_paper_positions | Closes paper positions. | State-changing; not called. |
| mcp__codex_apps__co_invest_reset_paper_account | Resets paper equity to $10,000 and clears positions, orders, fills, funding history, and leverage overrides. | Destructive across connectors sharing the wallet-keyed simulator; not called. |

The inspected Co-Invest inventory had no direct place_order or submit_order operation. The supplied declarations describe argument and return types for exposed tools, but no tool response or connected OAuth scope metadata was available. Therefore OAuth permissions, actual account identity, exact provider-side schemas, paper-mode state, and instrument precision remain UNVERIFIED.

The relevant declared inputs were:

- paper_trading_status: optional user_intent string.
- get_portfolio: optional skipCache boolean and user_intent string.
- suggest_order: asset string; leverage number; optional limitPrice and markPrice numbers; orderType market/limit; reasoning string; side long/short; size number (the declaration says USD notional, not units); optional sizeBasis notional/margin, stopLoss, takeProfit, timeInForce gtc/ioc/alo, and user_intent.
- suggest_trade has the same declared order fields and returns a proposal/review link. suggest_trades_batch accepts 2–8 such trade records and returns a basket review flow. Neither submits or confirms an order.
- enable_paper_trading: optional user_intent string.

No response schema for paper mode or order reconciliation was observed because no tool was invoked. The Co-Invest tool descriptions explicitly state that suggest_order only creates a review link and that confirmation happens in the separate Liquid app.

## Conclusion

NO_VERIFIED_CONCLUSION on whether a safe automated Liquid paper-order path exists. The presently exposed review-link flow cannot satisfy an adapter that must bind simulation mode at the execution boundary, because confirmation occurs later in a separate web app whose mode is not controlled by this connector. No paper-mode enablement, order review, order, or account mutation was performed.
