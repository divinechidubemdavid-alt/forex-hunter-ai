"""Persistent, transparent history and performance summaries for economic events.

Scores only outcomes supported by saved market evidence. Directional accuracy is
kept separate from hypothetical event-trade outcomes.
"""
from __future__ import annotations


def event_conversation(event: dict) -> list[dict]:
    """Return the saved event research/checkpoint conversation in chronological order."""
    rows = []
    for item in event.get("history", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("checkpoint"):
            analysis = item.get("analysis") or {}
            market_snapshots = item.get("market_technical_snapshots") or {}
            market_summary = "; ".join(
                f'{market} {snapshot.get("direction", "UNAVAILABLE")} '
                f'(ADX {snapshot.get("adx", "N/A")})'
                for market, snapshot in market_snapshots.items()
            )
            summary = item.get("evidence_note") or "; ".join(analysis.get("reasons", [])[:3])
            if market_summary:
                summary = (summary + " | " if summary else "") + "Per-market: " + market_summary
            rows.append({
                "at": item.get("created_at"),
                "stage": str(item.get("checkpoint")).upper(),
                "direction": analysis.get("combined_direction", analysis.get("direction", "UNSCORED")),
                "confidence": analysis.get("confidence"),
                "summary": summary,
                "verification": item.get("verification"),
            })
        elif item.get("post_event_report"):
            report = item["post_event_report"]
            analysis = report.get("analysis") or {}
            reaction_summary = "; ".join(
                f'{item.get("market")} +{item.get("minutes_after")}m '
                f'currency {item.get("direction")} / pair {item.get("pair_direction", "N/A")}'
                for item in report.get("market_reactions", [])
            )
            coverage_summary = "; ".join(
                f'+{item.get("minutes_after")}m {len(item.get("available_markets", []))}/'
                f'{len(item.get("expected_markets", []))} markets covered'
                for item in report.get("market_reaction_coverage", [])
            )
            summary = report.get("notes") or "Published actual release recorded."
            if reaction_summary:
                summary += " | Reactions: " + reaction_summary
            if coverage_summary:
                summary += " | Coverage: " + coverage_summary
            rows.append({
                "at": report.get("created_at"),
                "stage": "POST-RELEASE",
                "direction": analysis.get("combined_direction", analysis.get("direction", "UNSCORED")),
                "confidence": analysis.get("confidence"),
                "summary": summary,
                "market_reactions": report.get("market_reactions", []),
                "market_reaction_coverage": report.get("market_reaction_coverage", []),
            })
    return sorted(rows, key=lambda row: str(row.get("at") or ""))


def update_event_trade_progress(event: dict, snapshot: dict) -> dict:
    """Update a saved conditional event setup from a completed OHLC candle.

    Once any TP is reached, the trade is a WIN permanently. Later targets are
    still tracked; a later retracement can never turn a TP1/TP2 win into a loss.
    """
    trade = event.get("event_trade") or {}
    if trade.get("status") not in {"WAITING_ENTRY", "MONITORING", "WIN"} or snapshot.get("status") != "AVAILABLE":
        return trade
    candle_time = snapshot.get("candle_time")
    if candle_time and candle_time == trade.get("last_candle_time"):
        return trade
    try:
        high = float(snapshot["signal_candle_high"])
        low = float(snapshot["signal_candle_low"])
        stop = float(trade["stop_loss"])
        targets = [float(value) for value in trade.get("take_profits", [])[:3]]
        entry = float(trade["entry_trigger"])
    except (KeyError, TypeError, ValueError):
        return trade
    side = str(trade.get("side", "")).upper()
    hits = set(trade.get("hit_targets", []))
    if trade.get("status") == "WAITING_ENTRY":
        entry_hit = high >= entry if side == "BUY" else low <= entry if side == "SELL" else False
        if entry_hit:
            trade["status"] = "MONITORING"
            trade["result"] = "PENDING"
            trade["entered_at"] = candle_time
            trade["entry_price"] = entry
            trade["note"] = "Entry trigger reached; target/stop checks begin on the next completed candle."
        trade["last_candle_time"] = candle_time
        trade["last_checked_at"] = snapshot.get("retrieved_at")
        event["event_trade"] = trade
        return trade
    if side == "BUY":
        stop_hit = low <= stop
        target_hits = [i + 1 for i, target in enumerate(targets) if high >= target]
    elif side == "SELL":
        stop_hit = high >= stop
        target_hits = [i + 1 for i, target in enumerate(targets) if low <= target]
    else:
        return trade

    # If one OHLC candle spans a stop and a new target, its intrabar sequence
    # is unknowable. A prior TP win remains a win regardless of later reversal.
    new_hits = [index for index in target_hits if index not in hits]
    if stop_hit and new_hits and not hits:
        trade["status"] = "AMBIGUOUS"
        trade["result"] = "UNSCORED"
        trade["note"] = "One candle touched both stop and target; intrabar order is unknown."
    elif stop_hit and hits:
        # A previous TP remains a win. The stop closes the remaining tracked
        # position, so stop polling rather than pretending later targets remain open.
        trade["status"] = "WIN"
        trade["result"] = "WIN"
        trade["exit_reason"] = "STOP_AFTER_TP"
        trade["partial_win"] = True
        trade["note"] = f'TP{min(hits)} remains a win; stop later closed the remaining position.'
    elif stop_hit and new_hits:
        trade["status"] = "AMBIGUOUS"
        trade["result"] = "UNSCORED"
        trade["note"] = "One candle touched both stop and target; intrabar order is unknown."
    else:
        hits.update(new_hits)
        if hits:
            trade["status"] = "WIN"
            trade["result"] = "WIN"
            trade["winning_targets"] = sorted(hits)
            trade["first_target"] = min(hits)
            trade["note"] = f'TP{min(hits)} reached; this trade remains a win.'
        elif stop_hit:
            trade["status"] = "LOSS"
            trade["result"] = "LOSS"
            trade["note"] = "Stop-loss reached before any take-profit."
    trade["hit_targets"] = sorted(hits)
    trade["last_candle_time"] = candle_time
    trade["last_checked_at"] = snapshot.get("retrieved_at")
    event["event_trade"] = trade
    return trade


def performance_summary(events: list[dict]) -> dict:
    """Aggregate event-level accuracy and trade tracking without inventing scores."""
    summary = {
        "events": len(events), "bullish": 0, "bearish": 0, "no_clear_direction": 0,
        "not_finalized": 0, "directional_scored": 0, "directional_correct": 0,
        "trades_scored": 0, "trade_wins": 0, "trade_losses": 0,
        "tp1": 0, "tp2": 0, "tp3": 0, "partial_wins": 0,
        "no_trade": 0, "monitoring": 0, "ambiguous": 0,
        "buy_scored": 0, "buy_correct": 0, "buy_wins": 0, "buy_losses": 0,
        "sell_scored": 0, "sell_correct": 0, "sell_wins": 0, "sell_losses": 0,
        "confidence_total": 0, "confidence_count": 0, "net_r": 0.0, "unscored": 0,
        "by_event_type": {},
        "by_market": {},
        "by_horizon": {},
    }
    for event in events:
        final = event.get("final_15m") or event.get("checkpoints", {}).get("15m") or {}
        analysis = final.get("analysis") or {}
        direction = analysis.get("combined_direction", analysis.get("direction"))
        if not analysis:
            summary["not_finalized"] += 1
        elif direction == "BULLISH":
            summary["bullish"] += 1
        elif direction == "BEARISH":
            summary["bearish"] += 1
        else:
            summary["no_clear_direction"] += 1
        if analysis.get("confidence") is not None:
            try:
                summary["confidence_total"] += float(analysis["confidence"])
                summary["confidence_count"] += 1
            except (TypeError, ValueError):
                pass

        title = str(event.get("title", "Other")).upper()
        event_type = next((name for name in ("CPI", "NFP", "FOMC", "GDP", "UNEMPLOYMENT", "PCE")
                           if name in title or (name == "NFP" and "PAYROLL" in title)), "OTHER")
        bucket = summary["by_event_type"].setdefault(event_type, {
            "events": 0, "directional_scored": 0, "directional_correct": 0,
            "trades_scored": 0, "trade_wins": 0, "trade_losses": 0,
        })
        bucket["events"] += 1

        report = event.get("post_event_report") or {}
        reactions = [
            item for item in report.get("market_reactions", [])
            if item.get("direction") in {"BULLISH", "BEARISH"}
        ]
        market_biases = analysis.get("market_biases") or report.get("analysis", {}).get("market_biases") or {}
        reactions_by_market = {}
        for reaction in reactions:
            market = reaction.get("market")
            pair_direction = reaction.get("pair_direction")
            expected_bias = market_biases.get(market)
            if market and expected_bias in {"BUY", "SELL"} and pair_direction in {"BULLISH", "BEARISH"}:
                expected_pair_direction = "BULLISH" if expected_bias == "BUY" else "BEARISH"
                market_row = summary["by_market"].setdefault(market, {
                    "events": 0, "observations": 0, "scored": 0, "correct": 0,
                    "reversals": 0, "final_24h_scored": 0, "final_24h_correct": 0,
                })
                market_row["observations"] += 1
                market_row["scored"] += 1
                if pair_direction == expected_pair_direction:
                    market_row["correct"] += 1
                horizon = f'{int(reaction.get("minutes_after", 0))}m'
                horizon_row = summary["by_horizon"].setdefault(horizon, {
                    "observations": 0, "scored": 0, "correct": 0,
                })
                horizon_row["observations"] += 1
                horizon_row["scored"] += 1
                if pair_direction == expected_pair_direction:
                    horizon_row["correct"] += 1
                reactions_by_market.setdefault(market, []).append(reaction)

        # Score one final event-currency direction from the latest available
        # observation for each affected market, not the last arbitrary pair
        # in the stored list.
        latest_currency_directions = []
        for market, market_reactions in reactions_by_market.items():
            ordered = sorted(market_reactions, key=lambda item: int(item.get("minutes_after", 0)))
            summary["by_market"][market]["events"] += 1
            first_direction = ordered[0].get("pair_direction")
            final_direction = ordered[-1].get("pair_direction")
            if first_direction in {"BULLISH", "BEARISH"} and final_direction in {"BULLISH", "BEARISH"}:
                if first_direction != final_direction:
                    summary["by_market"][market]["reversals"] += 1
            latest = max(market_reactions, key=lambda item: int(item.get("minutes_after", 0)))
            latest_currency_directions.append(latest.get("direction"))
            if int(latest.get("minutes_after", 0)) >= 1440:
                expected_bias = market_biases.get(market)
                if expected_bias in {"BUY", "SELL"} and latest.get("pair_direction") in {"BULLISH", "BEARISH"}:
                    summary["by_market"][market]["final_24h_scored"] += 1
                    expected_pair_direction = "BULLISH" if expected_bias == "BUY" else "BEARISH"
                    if latest.get("pair_direction") == expected_pair_direction:
                        summary["by_market"][market]["final_24h_correct"] += 1

        correct = None
        if direction in {"BULLISH", "BEARISH"} and latest_currency_directions:
            bullish = latest_currency_directions.count("BULLISH")
            bearish = latest_currency_directions.count("BEARISH")
            final_currency_direction = (
                "BULLISH" if bullish > bearish else
                "BEARISH" if bearish > bullish else None
            )
            if final_currency_direction:
                correct = final_currency_direction == direction
        elif direction in {"BULLISH", "BEARISH"} and reactions:
            # Backward compatibility for older event records without market IDs.
            correct = reactions[-1]["direction"] == direction

        if correct is not None:
            summary["directional_scored"] += 1
            bucket["directional_scored"] += 1
            if correct:
                summary["directional_correct"] += 1
                bucket["directional_correct"] += 1
        else:
            summary["unscored"] += 1

        plan = (event.get("checkpoints", {}).get("5m") or {}).get("trade_plan") or final.get("trade_plan") or {}
        trade = event.get("event_trade") or event.get("trade_outcome") or report.get("trade_outcome") or {}
        side = str(trade.get("side") or plan.get("side") or "").upper()
        if plan.get("status") == "NO_TRADE" and not trade:
            summary["no_trade"] += 1
        status = str(trade.get("status", trade.get("result", ""))).upper()
        if status == "MONITORING":
            summary["monitoring"] += 1
        if status == "AMBIGUOUS":
            summary["ambiguous"] += 1
        if status in {"WIN", "LOSS"}:
            summary["trades_scored"] += 1
            bucket["trades_scored"] += 1
            if status == "WIN":
                summary["trade_wins"] += 1
                bucket["trade_wins"] += 1
            else:
                summary["trade_losses"] += 1
                bucket["trade_losses"] += 1
            hits = set(trade.get("hit_targets", trade.get("winning_targets", [])))
            for tp in (1, 2, 3):
                if tp in hits:
                    summary[f"tp{tp}"] += 1
            if status == "WIN" and hits and max(hits) < 3:
                summary["partial_wins"] += 1
            if side == "BUY":
                summary["buy_wins" if status == "WIN" else "buy_losses"] += 1
            elif side == "SELL":
                summary["sell_wins" if status == "WIN" else "sell_losses"] += 1
            try:
                summary["net_r"] += float(trade.get("r_multiple", 0))
            except (TypeError, ValueError):
                pass
        if correct is not None and side in {"BUY", "SELL"}:
            summary[f"{side.lower()}_scored"] += 1
            if correct:
                summary[f"{side.lower()}_correct"] += 1

    summary["directional_accuracy_pct"] = (
        round(100 * summary["directional_correct"] / summary["directional_scored"], 1)
        if summary["directional_scored"] else None
    )
    summary["trade_win_rate_pct"] = (
        round(100 * summary["trade_wins"] / summary["trades_scored"], 1)
        if summary["trades_scored"] else None
    )
    summary["buy_accuracy_pct"] = (
        round(100 * summary["buy_correct"] / summary["buy_scored"], 1)
        if summary["buy_scored"] else None
    )
    summary["sell_accuracy_pct"] = (
        round(100 * summary["sell_correct"] / summary["sell_scored"], 1)
        if summary["sell_scored"] else None
    )
    summary["average_confidence"] = (
        round(summary["confidence_total"] / summary["confidence_count"], 1)
        if summary["confidence_count"] else None
    )
    buy_trades = summary["buy_wins"] + summary["buy_losses"]
    sell_trades = summary["sell_wins"] + summary["sell_losses"]
    summary["buy_trade_win_rate_pct"] = round(100 * summary["buy_wins"] / buy_trades, 1) if buy_trades else None
    summary["sell_trade_win_rate_pct"] = round(100 * summary["sell_wins"] / sell_trades, 1) if sell_trades else None
    for market_row in summary["by_market"].values():
        market_row["accuracy_pct"] = (
            round(100 * market_row["correct"] / market_row["scored"], 1)
            if market_row["scored"] else None
        )
        market_row["final_24h_accuracy_pct"] = (
            round(100 * market_row["final_24h_correct"] / market_row["final_24h_scored"], 1)
            if market_row["final_24h_scored"] else None
        )
    for horizon_row in summary["by_horizon"].values():
        horizon_row["accuracy_pct"] = (
            round(100 * horizon_row["correct"] / horizon_row["scored"], 1)
            if horizon_row["scored"] else None
        )
    summary["net_r"] = round(summary["net_r"], 2)
    return summary
