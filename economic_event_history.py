"""Persistent, transparent history and performance summaries for economic events.

Scores only outcomes supported by recorded market-reaction snapshots. Missing evidence
stays unscored; directional forecast accuracy is not presented as trade profitability.
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
            rows.append({
                "at": item.get("created_at"),
                "stage": str(item.get("checkpoint")).upper(),
                "direction": analysis.get("combined_direction", analysis.get("direction", "UNSCORED")),
                "confidence": analysis.get("confidence"),
                "summary": item.get("evidence_note") or "; ".join(analysis.get("reasons", [])[:3]),
                "verification": item.get("verification"),
            })
        elif item.get("post_event_report"):
            report = item["post_event_report"]
            analysis = report.get("analysis") or {}
            rows.append({
                "at": report.get("created_at"),
                "stage": "POST-RELEASE",
                "direction": analysis.get("combined_direction", analysis.get("direction", "UNSCORED")),
                "confidence": analysis.get("confidence"),
                "summary": report.get("notes") or "Published actual release recorded.",
            })
    return sorted(rows, key=lambda row: str(row.get("at") or ""))


def performance_summary(events: list[dict]) -> dict:
    """Aggregate recorded event outcomes without inventing missing scores."""
    summary = {
        "events": len(events), "bullish": 0, "bearish": 0, "no_clear_direction": 0,
        "directional_scored": 0, "directional_correct": 0,
        "trades_scored": 0, "trade_wins": 0, "trade_losses": 0,
        "net_r": 0.0, "unscored": 0,
    }
    for event in events:
        final = event.get("final_15m") or event.get("checkpoints", {}).get("15m") or {}
        analysis = final.get("analysis") or {}
        direction = analysis.get("combined_direction", analysis.get("direction"))
        if direction == "BULLISH":
            summary["bullish"] += 1
        elif direction == "BEARISH":
            summary["bearish"] += 1
        else:
            summary["no_clear_direction"] += 1

        report = event.get("post_event_report") or {}
        reactions = [
            item for item in report.get("market_reactions", [])
            if item.get("direction") in {"BULLISH", "BEARISH"}
        ]
        if direction in {"BULLISH", "BEARISH"} and reactions:
            summary["directional_scored"] += 1
            # Score the latest recorded reaction, not every sample as a separate event.
            if reactions[-1]["direction"] == direction:
                summary["directional_correct"] += 1
        else:
            summary["unscored"] += 1

        trade = event.get("trade_outcome") or report.get("trade_outcome")
        if isinstance(trade, dict) and trade.get("result") in {"WIN", "LOSS"}:
            summary["trades_scored"] += 1
            summary["trade_wins" if trade["result"] == "WIN" else "trade_losses"] += 1
            try:
                summary["net_r"] += float(trade.get("r_multiple", 0))
            except (TypeError, ValueError):
                pass

    summary["directional_accuracy_pct"] = (
        round(100 * summary["directional_correct"] / summary["directional_scored"], 1)
        if summary["directional_scored"] else None
    )
    summary["net_r"] = round(summary["net_r"], 2)
    return summary
