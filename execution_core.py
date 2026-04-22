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
        self.last_plan: dict[str, Any] = {}
        self.last_shadow: dict[str, Any] = {}
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
        if not allocations or extra_budget <= 0.0:
            return
        per_symbol_extra = extra_budget / len(allocations)
        for symbol in list(allocations.keys()):
            allocations[symbol] = min(allocations[symbol] + per_symbol_extra, max_order_usd)

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
            "last_plan": self.last_plan,
            "last_shadow": self.last_shadow,
            "recent_events": list(self.recent_events),
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
