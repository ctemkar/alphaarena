from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class SignalRequest:
    model_name: str
    desk: str
    side_label: str
    total_order_usd: float
    max_order_usd: float
    live_trading: bool
    require_live_feed: bool
    live_feed: bool
    kill_switch: bool
    pause_all_desks: bool
    pause_desk: bool
    live_blocked: bool
    live_blocked_reason: str
    one_live_entry_per_desk_per_tick: bool
    one_live_entry_global_per_tick: bool
    desk_already_routed_this_tick: bool
    global_already_routed_this_tick: bool
    allow_cross_symbol_fallback: bool
    desk_symbols: list[str]
    universe_symbols: list[str]
    symbol_prices: dict[str, float]
    qty_precision: dict[str, int]
    min_notional: dict[str, float]
    duplicate_cooldown_seconds: float
    symbol_cooldown_seconds: float
    last_symbol_side_ts: dict[tuple[str, str], float]
    last_symbol_ts: dict[str, float]
    now_epoch: float


@dataclass
class OrderPlan:
    allowed: bool
    reason: str
    allocations: dict[str, float]
    required_usdt: float
    used_fallback_symbols: bool


@dataclass
class FlattenPlan:
    orders: list[dict[str, Any]]
    skipped: list[dict[str, Any]]


class ExecutionCore:
    """Greenfield execution planner with shadow/cutover controls."""

    VALID_MODES = {"legacy", "shadow", "cutover"}

    def __init__(self, mode: str = "shadow", fallback_enabled: bool = True) -> None:
        self.mode = mode if mode in self.VALID_MODES else "shadow"
        self.fallback_enabled = bool(fallback_enabled)
        self.plans_computed = 0
        self.plans_allowed = 0
        self.cutover_routed = 0
        self.shadow_compares = 0
        self.shadow_matches = 0
        self.shadow_diverges = 0
        self.flatten_runs = 0
        self.cutover_gate_enabled = True
        self.cutover_gate_auto_switch = True
        self.cutover_gate_threshold = 0.99
        self.cutover_gate_min_compares = 10
        self.cutover_gate_stability_checks = 3
        self.cutover_gate_consecutive_passes = 0
        self.cutover_gate_triggered = 0
        self.cutover_gate_last_decision = "waiting"
        self.cutover_gate_last_reason = "insufficient data"
        self.last_plan: dict[str, Any] = {}
        self.last_shadow: dict[str, Any] = {}
        self.last_flatten: dict[str, Any] = {}
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=40)

    @staticmethod
    def _symbol_floor(symbol: str, req: SignalRequest) -> float:
        price = max(float(req.symbol_prices.get(symbol, 0.0) or 0.0), 1e-9)
        precision = int(req.qty_precision.get(symbol, 3))
        step_floor = price / (10 ** precision)
        exchange_floor = max(float(req.min_notional.get(symbol, 5.0) or 5.0), 5.0)
        return max(step_floor, exchange_floor, 5.0)

    @staticmethod
    def _apply_remaining_budget(allocations: dict[str, float], extra_budget: float, max_order_usd: float) -> None:
        """Distribute extra_budget across symbols that have headroom below max_order_usd.

        Iterates up to 5 rounds so that budget freed from capped symbols is correctly
        redistributed to uncapped ones rather than being silently discarded.
        """
        if not allocations or extra_budget <= 0.0:
            return
        for _ in range(5):
            uncapped = [s for s in allocations if allocations[s] < max_order_usd - 1e-9]
            if not uncapped or extra_budget < 0.01:
                break
            per_symbol_extra = extra_budget / len(uncapped)
            redistributed = 0.0
            for symbol in uncapped:
                headroom = max_order_usd - allocations[symbol]
                added = min(per_symbol_extra, headroom)
                allocations[symbol] += added
                redistributed += added
            extra_budget -= redistributed

    def set_mode(self, mode: str) -> tuple[bool, str]:
        if mode not in self.VALID_MODES:
            return False, "mode must be one of: legacy, shadow, cutover"
        if self.mode == mode:
            return False, "no change"
        self.mode = mode
        self.recent_events.appendleft({
            "ts": datetime.now().isoformat(),
            "event": "mode_change",
            "mode": mode,
        })
        return True, "updated"

    def set_fallback_enabled(self, enabled: bool) -> tuple[bool, str]:
        enabled = bool(enabled)
        if self.fallback_enabled == enabled:
            return False, "no change"
        self.fallback_enabled = enabled
        self.recent_events.appendleft({
            "ts": datetime.now().isoformat(),
            "event": "fallback_change",
            "fallback_enabled": enabled,
        })
        return True, "updated"

    def set_cutover_gate(
        self,
        *,
        enabled: bool | None = None,
        auto_switch: bool | None = None,
        threshold: float | None = None,
        min_compares: int | None = None,
        stability_checks: int | None = None,
    ) -> tuple[bool, str]:
        changed = False
        if enabled is not None:
            enabled_val = bool(enabled)
            if self.cutover_gate_enabled != enabled_val:
                self.cutover_gate_enabled = enabled_val
                changed = True
        if auto_switch is not None:
            auto_switch_val = bool(auto_switch)
            if self.cutover_gate_auto_switch != auto_switch_val:
                self.cutover_gate_auto_switch = auto_switch_val
                changed = True
        if threshold is not None:
            bounded = min(max(float(threshold), 0.0), 1.0)
            if abs(self.cutover_gate_threshold - bounded) > 1e-12:
                self.cutover_gate_threshold = bounded
                changed = True
        if min_compares is not None:
            bounded = max(int(min_compares), 1)
            if self.cutover_gate_min_compares != bounded:
                self.cutover_gate_min_compares = bounded
                changed = True
        if stability_checks is not None:
            bounded = max(int(stability_checks), 1)
            if self.cutover_gate_stability_checks != bounded:
                self.cutover_gate_stability_checks = bounded
                changed = True
        if changed:
            self.cutover_gate_consecutive_passes = 0
            self.recent_events.appendleft({
                "ts": datetime.now().isoformat(),
                "event": "cutover_gate_change",
                "enabled": self.cutover_gate_enabled,
                "auto_switch": self.cutover_gate_auto_switch,
                "threshold": self.cutover_gate_threshold,
                "min_compares": self.cutover_gate_min_compares,
                "stability_checks": self.cutover_gate_stability_checks,
            })
            return True, "updated"
        return False, "no change"

    def plan_signal(self, req: SignalRequest) -> OrderPlan:
        self.plans_computed += 1

        if req.side_label not in {"LONG", "SHORT"}:
            return self._deny("non-directional signal")
        if req.pause_all_desks or req.pause_desk:
            return self._deny("desk paused")
        if not req.live_trading:
            return self._deny("live trading disabled")
        if req.require_live_feed and not req.live_feed:
            return self._deny("live feed required but offline")
        if req.kill_switch:
            return self._deny("kill switch active")
        hard_live_block = req.live_blocked and ("Insufficient USDT" not in (req.live_blocked_reason or ""))
        if hard_live_block:
            return self._deny("live blocked by auth/system error")
        if req.one_live_entry_per_desk_per_tick and req.desk_already_routed_this_tick:
            return self._deny("desk entry throttled this tick")
        if req.one_live_entry_global_per_tick and req.global_already_routed_this_tick:
            return self._deny("global entry throttled this tick")

        effective_order_usd = max(float(req.total_order_usd or 0.0), 0.0)
        if effective_order_usd < 5.0:
            return self._deny("order budget below minimum")

        allocations, remaining = self._allocate_for_symbols(req.desk_symbols, effective_order_usd, req)
        used_fallback = False

        if not allocations:
            if not req.allow_cross_symbol_fallback:
                return self._deny("desk budget cannot satisfy min notional")
            allocations, remaining = self._allocate_for_symbols(req.universe_symbols, effective_order_usd, req)
            used_fallback = bool(allocations)
            if not allocations:
                return self._deny("budget cannot satisfy any symbol min notional")

        self._apply_remaining_budget(allocations, remaining, req.max_order_usd)

        filtered: dict[str, float] = {}
        for symbol, order_usd in allocations.items():
            side_key = (symbol, req.side_label)
            last_side_ts = float(req.last_symbol_side_ts.get(side_key, 0.0) or 0.0)
            if req.duplicate_cooldown_seconds > 0.0 and (req.now_epoch - last_side_ts) < req.duplicate_cooldown_seconds:
                continue
            last_symbol_ts = float(req.last_symbol_ts.get(symbol, 0.0) or 0.0)
            if req.symbol_cooldown_seconds > 0.0 and (req.now_epoch - last_symbol_ts) < req.symbol_cooldown_seconds:
                continue
            filtered[symbol] = order_usd

        if not filtered:
            return self._deny("cooldown filtered all symbols")

        required = sum(filtered.values())
        plan = OrderPlan(
            allowed=True,
            reason="ok",
            allocations=filtered,
            required_usdt=required,
            used_fallback_symbols=used_fallback,
        )
        self.plans_allowed += 1
        self.last_plan = {
            "ts": datetime.now().isoformat(),
            "model": req.model_name,
            "desk": req.desk,
            "side": req.side_label,
            "allocations": dict(filtered),
            "required_usdt": round(required, 6),
            "used_fallback_symbols": used_fallback,
            "mode": self.mode,
        }
        return plan

    def record_cutover_routed(self, symbol_count: int) -> None:
        self.cutover_routed += max(int(symbol_count), 0)

    def record_shadow_compare(self, plan: OrderPlan, legacy_executed: bool, legacy_symbols: list[str]) -> None:
        self.shadow_compares += 1
        plan_symbols = sorted(plan.allocations.keys()) if plan.allowed else []
        legacy_set = sorted(set(legacy_symbols))
        expected_exec = bool(plan.allowed and len(plan_symbols) > 0)
        matched = (expected_exec == legacy_executed) and (plan_symbols == legacy_set)
        if matched:
            self.shadow_matches += 1
        else:
            self.shadow_diverges += 1
        self.last_shadow = {
            "ts": datetime.now().isoformat(),
            "plan_allowed": plan.allowed,
            "plan_reason": plan.reason,
            "plan_symbols": plan_symbols,
            "legacy_executed": bool(legacy_executed),
            "legacy_symbols": legacy_set,
            "matched": matched,
        }

    def health_snapshot(self) -> dict[str, Any]:
        match_rate = (self.shadow_matches / self.shadow_compares) if self.shadow_compares else 1.0
        return {
            "mode": self.mode,
            "fallback_enabled": self.fallback_enabled,
            "plans_computed": self.plans_computed,
            "plans_allowed": self.plans_allowed,
            "cutover_routed": self.cutover_routed,
            "shadow_compares": self.shadow_compares,
            "shadow_matches": self.shadow_matches,
            "shadow_diverges": self.shadow_diverges,
            "shadow_match_rate": round(match_rate, 4),
            "flatten_runs": self.flatten_runs,
            "cutover_gate": {
                "enabled": self.cutover_gate_enabled,
                "auto_switch": self.cutover_gate_auto_switch,
                "threshold": self.cutover_gate_threshold,
                "min_compares": self.cutover_gate_min_compares,
                "stability_checks": self.cutover_gate_stability_checks,
                "consecutive_passes": self.cutover_gate_consecutive_passes,
                "last_decision": self.cutover_gate_last_decision,
                "last_reason": self.cutover_gate_last_reason,
                "triggered": self.cutover_gate_triggered,
            },
            "last_plan": self.last_plan,
            "last_shadow": self.last_shadow,
            "last_flatten": self.last_flatten,
            "recent_events": list(self.recent_events),
        }

    def evaluate_cutover_gate(self) -> dict[str, Any]:
        if not self.cutover_gate_enabled:
            self.cutover_gate_last_decision = "disabled"
            self.cutover_gate_last_reason = "gate disabled"
            return {
                "allowed": True,
                "reason": self.cutover_gate_last_reason,
                "auto_switch": False,
            }
        if self.mode != "shadow":
            self.cutover_gate_last_decision = "not_shadow"
            self.cutover_gate_last_reason = "gate checks apply only in shadow mode"
            return {
                "allowed": False,
                "reason": self.cutover_gate_last_reason,
                "auto_switch": False,
            }

        compares = self.shadow_compares
        rate = (self.shadow_matches / compares) if compares else 0.0
        if compares < self.cutover_gate_min_compares:
            self.cutover_gate_consecutive_passes = 0
            self.cutover_gate_last_decision = "waiting"
            self.cutover_gate_last_reason = (
                f"need {self.cutover_gate_min_compares} compares, have {compares}"
            )
            return {
                "allowed": False,
                "reason": self.cutover_gate_last_reason,
                "auto_switch": False,
            }

        if rate >= self.cutover_gate_threshold:
            self.cutover_gate_consecutive_passes += 1
            self.cutover_gate_last_decision = "pass"
            self.cutover_gate_last_reason = (
                f"rate {rate:.4f} >= threshold {self.cutover_gate_threshold:.4f}"
            )
        else:
            self.cutover_gate_consecutive_passes = 0
            self.cutover_gate_last_decision = "fail"
            self.cutover_gate_last_reason = (
                f"rate {rate:.4f} < threshold {self.cutover_gate_threshold:.4f}"
            )
            return {
                "allowed": False,
                "reason": self.cutover_gate_last_reason,
                "auto_switch": False,
            }

        allowed = self.cutover_gate_consecutive_passes >= self.cutover_gate_stability_checks
        auto_switch = bool(allowed and self.cutover_gate_auto_switch)
        if allowed:
            self.cutover_gate_last_decision = "eligible"
            self.cutover_gate_last_reason = (
                f"eligible after {self.cutover_gate_consecutive_passes} consecutive passes"
            )
        return {
            "allowed": allowed,
            "reason": self.cutover_gate_last_reason,
            "auto_switch": auto_switch,
        }

    def consume_cutover_gate_trigger(self) -> None:
        self.cutover_gate_triggered += 1
        self.cutover_gate_consecutive_passes = 0
        self.recent_events.appendleft({
            "ts": datetime.now().isoformat(),
            "event": "cutover_gate_triggered",
            "triggered_count": self.cutover_gate_triggered,
        })

    def plan_flatten_orders(
        self,
        positions: list[dict[str, Any]],
        qty_precision: dict[str, int],
    ) -> FlattenPlan:
        orders: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in positions:
            symbol = str(row.get("symbol", "") or "")
            if not symbol:
                continue
            try:
                position_amt = float(row.get("positionAmt", "0") or 0.0)
            except (TypeError, ValueError):
                skipped.append({"symbol": symbol, "reason": "invalid positionAmt"})
                continue
            if abs(position_amt) <= 1e-12:
                continue

            side = "SELL" if position_amt > 0 else "BUY"
            precision = int(qty_precision.get(symbol, 3))
            factor = 10 ** precision
            qty = (int(abs(position_amt) * factor)) / factor
            if qty <= 0:
                skipped.append({
                    "symbol": symbol,
                    "reason": f"quantity rounded to zero (positionAmt={position_amt})",
                })
                continue

            payload = {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": qty,
                "newOrderRespType": "RESULT",
                "reduceOnly": "true",
            }
            position_side = str(row.get("positionSide", "") or "").upper()
            if position_side in {"LONG", "SHORT"}:
                payload["positionSide"] = position_side
                payload.pop("reduceOnly", None)
            orders.append(payload)

        self.flatten_runs += 1
        self.last_flatten = {
            "ts": datetime.now().isoformat(),
            "planned_orders": len(orders),
            "skipped": skipped,
        }
        return FlattenPlan(orders=orders, skipped=skipped)

    @staticmethod
    def compute_trade_metrics(trade_pnls: list[float]) -> dict:
        """Compute risk-adjusted performance metrics from a list of closed-trade PnL values.

        Returns a dict with: count, total_pnl, win_rate, expectancy, max_drawdown,
        sharpe (mean/std of trade returns), sortino (mean/downside-std).
        All values are safe to compute even on an empty list.
        """
        n = len(trade_pnls)
        if n == 0:
            return {
                "count": 0,
                "total_pnl": 0.0,
                "win_rate": 0.0,
                "expectancy": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "sortino": 0.0,
            }

        wins = sum(1 for p in trade_pnls if p > 0)
        total = sum(trade_pnls)
        expectancy = total / n
        win_rate = wins / n

        # Max drawdown on cumulative equity curve
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in trade_pnls:
            equity += p
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        # Sharpe: mean / std (trade-level, not annualised)
        mean = expectancy
        variance = sum((p - mean) ** 2 for p in trade_pnls) / n
        std = variance ** 0.5
        sharpe = mean / std if std > 1e-12 else 0.0

        # Sortino: mean / downside-std
        downside_var = sum((p - mean) ** 2 for p in trade_pnls if p < mean) / n
        downside_std = downside_var ** 0.5
        sortino = mean / downside_std if downside_std > 1e-12 else 0.0

        return {
            "count": n,
            "total_pnl": round(total, 6),
            "win_rate": round(win_rate, 4),
            "expectancy": round(expectancy, 6),
            "max_drawdown": round(max_dd, 6),
            "sharpe": round(sharpe, 4),
            "sortino": round(sortino, 4),
        }

    def _deny(self, reason: str) -> OrderPlan:
        return OrderPlan(
            allowed=False,
            reason=reason,
            allocations={},
            required_usdt=0.0,
            used_fallback_symbols=False,
        )

    def _allocate_for_symbols(
        self,
        symbols: list[str],
        budget_usd: float,
        req: SignalRequest,
    ) -> tuple[dict[str, float], float]:
        floors = {sym: self._symbol_floor(sym, req) for sym in symbols}
        allocations: dict[str, float] = {}
        remaining = budget_usd
        for symbol, floor in sorted(floors.items(), key=lambda kv: kv[1]):
            if floor <= remaining:
                allocations[symbol] = floor
                remaining -= floor
        return allocations, remaining
