"""Read-only snapshot renderer.

This tool is read-only.
It parses immutable logs.
It must never write, infer, or mutate state.
Output format may change; logic may not.
"""

# Snapshot has a single data assembly path; all renderers consume identical entries.
# Renderers must never compute, filter, or infer.

from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _is_valid_ts(value: Any) -> bool:
    """Return True when timestamps are ISO-8601 UTC strings for lexicographic ordering."""
    if not isinstance(value, str):
        return False
    if "T" not in value:
        return False
    return value.endswith("Z") or value.endswith("+00:00")


def _is_newer(timestamp: str, current: Dict[str, Any] | None) -> bool:
    if current is None:
        return True
    current_ts = current.get("timestamp")
    if not isinstance(current_ts, str):
        return True
    return timestamp > current_ts


def _parse_event_spine(path: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    latest_regime_by_symbol: Dict[str, Dict[str, Any]] = {}
    latest_change_by_symbol: Dict[str, Dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                event_type = event.get("event_type")
                if event_type not in {"market.regime", "market.regime_change"}:
                    continue
                timestamp = event.get("timestamp")
                if not _is_valid_ts(timestamp):
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                symbol = payload.get("symbol")
                if not isinstance(symbol, str):
                    continue
                if event_type == "market.regime":
                    regime = payload.get("regime")
                    if not isinstance(regime, str):
                        continue
                    current = latest_regime_by_symbol.get(symbol)
                    if not _is_newer(timestamp, current):
                        continue
                    entry = {"timestamp": timestamp, "regime": regime}
                    if "confidence" in payload:
                        entry["confidence"] = payload.get("confidence")
                    latest_regime_by_symbol[symbol] = entry
                else:
                    from_regime = payload.get("from")
                    to_regime = payload.get("to")
                    if not isinstance(from_regime, str) or not isinstance(to_regime, str):
                        continue
                    current = latest_change_by_symbol.get(symbol)
                    if not _is_newer(timestamp, current):
                        continue
                    entry = {"timestamp": timestamp, "from": from_regime, "to": to_regime}
                    if "confidence" in payload:
                        entry["confidence"] = payload.get("confidence")
                    latest_change_by_symbol[symbol] = entry
    except OSError:
        return {}
    folded: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for symbol, regime_entry in latest_regime_by_symbol.items():
        symbol_entry = {"market.regime": regime_entry}
        change_entry = latest_change_by_symbol.get(symbol)
        if change_entry is not None:
            symbol_entry["market.regime_change"] = change_entry
        folded[symbol] = symbol_entry
    return folded


def _parse_router_intents(path: Path) -> Dict[str, Dict[str, Any]]:
    latest_intent_by_symbol: Dict[str, Dict[str, Any]] = {}
    latest_ts_by_symbol: Dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                timestamp = record.get("timestamp")
                if not _is_valid_ts(timestamp):
                    continue
                intent = record.get("payload")
                if not isinstance(intent, dict):
                    intent = record.get("intent")
                if not isinstance(intent, dict):
                    continue
                symbol = record.get("symbol")
                if not isinstance(symbol, str):
                    symbol = intent.get("symbol")
                if not isinstance(symbol, str):
                    continue
                current_ts = latest_ts_by_symbol.get(symbol)
                if isinstance(current_ts, str) and timestamp <= current_ts:
                    continue
                latest_ts_by_symbol[symbol] = timestamp
                latest_intent_by_symbol[symbol] = {
                    "direction": intent.get("direction"),
                    "size_pct": intent.get("size_pct"),
                    "risk_cap": intent.get("risk_cap"),
                    "rationale": intent.get("rationale"),
                }
    except OSError:
        return {}
    return latest_intent_by_symbol


def _build_snapshot_entries(
    symbols: list[str],
    spine_summary: Dict[str, Dict[str, Dict[str, Any]]],
    intent_summary: Dict[str, Dict[str, Any]],
) -> list[Dict[str, Any]]:
    # The only place snapshot data is assembled; renderers must not diverge semantics.
    entries: list[Dict[str, Any]] = []
    for symbol in symbols:
        regime_entry = spine_summary.get(symbol, {}).get("market.regime")
        change_entry = spine_summary.get(symbol, {}).get("market.regime_change")
        intent_entry = intent_summary.get(symbol)

        regime = regime_entry.get("regime") if isinstance(regime_entry, dict) else "—"
        regime_ts = regime_entry.get("timestamp") if isinstance(regime_entry, dict) else "—"
        change_from = change_entry.get("from") if isinstance(change_entry, dict) else None
        change_to = change_entry.get("to") if isinstance(change_entry, dict) else None
        change_ts = change_entry.get("timestamp") if isinstance(change_entry, dict) else "—"

        if change_from is not None and change_to is not None:
            change_value = f"{change_from} -> {change_to} @ {change_ts}"
        else:
            change_value = "—"

        if isinstance(intent_entry, dict):
            direction = intent_entry.get("direction", "—")
            size_pct = intent_entry.get("size_pct", "—")
            risk_cap = intent_entry.get("risk_cap", "—")
            rationale = intent_entry.get("rationale")
        else:
            direction = "—"
            size_pct = "—"
            risk_cap = "—"
            rationale = None

        entries.append(
            {
                "symbol": symbol,
                "regime": regime,
                "regime_ts": regime_ts,
                "change_value": change_value,
                "direction": direction,
                "size_pct": size_pct,
                "risk_cap": risk_cap,
                "rationale": rationale,
            }
        )
    return entries


def _render_markdown(header_ts: str, entries: list[Dict[str, Any]]) -> None:
    # Formatting only; no logic or ordering changes.
    print(f"# synthdesk snapshot (utc): {header_ts}")
    print("")
    for entry in entries:
        print(f"## {entry['symbol']}")
        print("")
        print(f"- **regime:** {entry['regime']} @ {entry['regime_ts']}")
        print(f"- **last regime change:** {entry['change_value']}")
        print(
            f"- **posture:** {entry['direction']} / {entry['risk_cap']} / size={entry['size_pct']}"
        )
        print("")
        print("**rationale:**")
        rationale = entry.get("rationale")
        if isinstance(rationale, list) and rationale:
            for line in rationale:
                print(f"- {line}")
        else:
            print("- —")
        print("")


def _render_terminal(header_ts: str, entries: list[Dict[str, Any]]) -> None:
    # Formatting only; no logic or ordering changes.
    print(f"synthdesk snapshot (utc): {header_ts}")
    print("")
    for entry in entries:
        print(entry["symbol"])
        print(f"regime: {entry['regime']} @ {entry['regime_ts']}")
        print(f"last regime change: {entry['change_value']}")
        print(
            f"posture: {entry['direction']} / {entry['risk_cap']} / size={entry['size_pct']}"
        )
        print("rationale:")
        rationale = entry.get("rationale")
        if isinstance(rationale, list) and rationale:
            for line in rationale:
                print(f"- {line}")
        else:
            print("- —")
        print("")


def _render_html(header_ts: str, entries: list[Dict[str, Any]]) -> None:
    # Formatting only; no logic or ordering changes.
    def _escape(value: Any) -> str:
        return html.escape(str(value), quote=True)

    print("<!doctype html>")
    print('<html lang="en">')
    print("<head>")
    print('  <meta charset="utf-8">')
    print("  <title>synthdesk snapshot</title>")
    print("</head>")
    print("<body>")
    print(f"  <h1>synthdesk snapshot (utc): {_escape(header_ts)}</h1>")
    print("")
    for entry in entries:
        print("  <section>")
        print(f"    <h2>{_escape(entry['symbol'])}</h2>")
        print("    <ul>")
        print(
            f"      <li><strong>regime:</strong> {_escape(entry['regime'])} @ {_escape(entry['regime_ts'])}</li>"
        )
        print(f"      <li><strong>last regime change:</strong> {_escape(entry['change_value'])}</li>")
        print(
            f"      <li><strong>posture:</strong> {_escape(entry['direction'])} / "
            f"{_escape(entry['risk_cap'])} / size={_escape(entry['size_pct'])}</li>"
        )
        print("    </ul>")
        print("")
        print("    <strong>rationale:</strong>")
        print("    <ul>")
        rationale = entry.get("rationale")
        if isinstance(rationale, list) and rationale:
            for line in rationale:
                print(f"      <li>{_escape(line)}</li>")
        else:
            print("      <li>—</li>")
        print("    </ul>")
        print("  </section>")
    print("</body>")
    print("</html>")


def main() -> None:
    """Entry point stub for snapshot renderer."""
    if len(sys.argv) < 3:
        return None
    event_spine_path = Path(sys.argv[1])
    router_intents_path = Path(sys.argv[2])
    if not event_spine_path.exists():
        return None
    spine_summary = _parse_event_spine(event_spine_path)
    intent_summary = _parse_router_intents(router_intents_path) if router_intents_path.exists() else {}

    header_ts = datetime.now(timezone.utc).isoformat()
    symbols = sorted(set(spine_summary.keys()) | set(intent_summary.keys()))
    entries = _build_snapshot_entries(symbols, spine_summary, intent_summary)
    output_mode = sys.argv[3] if len(sys.argv) > 3 else None
    if output_mode == "markdown":
        _render_markdown(header_ts, entries)
        return None
    if output_mode == "html":
        _render_html(header_ts, entries)
        return None
    _render_terminal(header_ts, entries)
    return None


if __name__ == "__main__":
    main()
