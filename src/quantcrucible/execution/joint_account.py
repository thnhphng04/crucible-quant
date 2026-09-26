"""One account replay across many slots (Architecture §3.2.1, §3.4, ADR-0032, P3-08).

A portfolio used to be N independently backtested, independently self-financed return streams
combined by weight (:func:`quantcrucible.validation.portfolio.combine`). That stops describing
the real thing the moment ten strategies share one USDT balance, one margin pool and one kill
switch: their fills interact through the account, not through a weighted sum of their returns.

So the replay takes every slot's fills — each produced by its own single-strategy backtest —
plus the mark and funding series, and walks them through one :class:`PerpAccount` in time order.

**Per-bar order is fixed and hashed into the campaign protocol** (``PROTOCOL["replay"]``):
funding first, then liquidation on the mark, then the bar's fills, closes before opens. Funding
before liquidation matters, because funding leaves the isolated wallet and so can be what tips a
position over; closes before opens matters because a slot must free its margin before another
asks for it, and which slot that is has nothing to do with the alphabet.

A slot's *contribution* is the account PnL attributable to that slot — realized PnL, fees and
funding. Starting equity plus the summed contributions equals account equity, which is what
makes a per-instrument chart an honest decomposition rather than an invented subaccount.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars, ScopeDirection, Signal
from quantcrucible.execution.admission import EntryRequest, admit_batch
from quantcrucible.execution.margin import BracketTable
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.perp_account import (
    Liquidated,
    PerpAccount,
    resolve_bar,
)
from quantcrucible.execution.risk import RiskSettings

# Both sides, typed once so every loop over them narrows instead of needing an ignore.
_SIDES: tuple[ScopeDirection, ...] = ("long", "short")

Slot = tuple[str, str]  # (instrument, direction)


@dataclass(frozen=True, slots=True)
class SlotFill:
    """One fill, tagged with the slot that produced it.

    ``opening`` says whether this fill opens the slot's position or closes it. It is carried
    rather than inferred, because under hedge mode a BUY opens a long and closes a short.
    """

    ts: int  # ns
    instrument: str
    direction: ScopeDirection
    qty: float
    price: float
    commission: float
    opening: bool

    @property
    def slot(self) -> Slot:
        return (self.instrument, self.direction)

    @property
    def sort_key(self) -> tuple[int, int, str, int]:
        # Closes before opens at one timestamp: a slot must free its margin before another asks
        # for it, and which slot that is has nothing to do with the alphabet. The phase leads;
        # only inside a phase does canonical slot order apply, as in `admission`.
        phase = 1 if self.opening else 0
        return (self.ts, phase, self.instrument, 0 if self.direction == "long" else 1)


@dataclass(slots=True)
class ReplayResult:
    ts: npt.NDArray[np.datetime64]
    equity: npt.NDArray[np.float64]
    contributions: dict[Slot, float] = field(default_factory=dict)
    funding_paid: dict[Slot, float] = field(default_factory=dict)
    liquidations: list[Slot] = field(default_factory=list)
    denied: list[tuple[Slot, str]] = field(default_factory=list)

    @property
    def returns(self) -> npt.NDArray[np.float64]:
        if len(self.equity) < 2:
            return np.zeros(0)
        return self.equity[1:] / self.equity[:-1] - 1.0


def replay_account(
    fills: Sequence[SlotFill],
    ts: npt.NDArray[np.datetime64],
    marks: Mapping[str, npt.NDArray[np.float64]],
    funding: Mapping[str, npt.NDArray[np.float64]],
    tables: Mapping[str, BracketTable],
    initial_cash: float,
    leverage: int,
) -> ReplayResult:
    """Walk one account through every slot's fills.

    ``marks`` and ``funding`` are per instrument, indexed like ``ts``. A funding rate of 0 means
    no payment; a **missing** instrument means no funding data, which callers upstream must have
    already refused (INV-94) rather than silently treating as zero.
    """
    account = PerpAccount(balance=initial_cash, tables=tables)
    result = ReplayResult(ts=ts, equity=np.empty(len(ts), dtype=np.float64))
    contributions: dict[Slot, float] = {}
    funding_paid: dict[Slot, float] = {}

    by_bar: dict[int, list[SlotFill]] = {}
    ts_ns = ts.astype("datetime64[ns]").astype(np.int64)
    if len(ts_ns) == 0:
        raise ValueError("replay_account needs at least one bar")
    for fill in sorted(fills, key=lambda f: f.sort_key):
        # Refused, not clamped. Folding a fill from beyond the window into the last bar let a
        # future trade move equity inside the measured period; folding an early one into the
        # first bar did the same at the other end.
        if not ts_ns[0] <= fill.ts <= ts_ns[-1]:
            raise ValueError(
                f"{fill.instrument} {fill.direction} fill at {fill.ts} is outside the replay "
                f"window [{int(ts_ns[0])}, {int(ts_ns[-1])}]"
            )
        by_bar.setdefault(int(np.searchsorted(ts_ns, fill.ts, side="left")), []).append(fill)

    def credit(slot: Slot, amount: float) -> None:
        contributions[slot] = contributions.get(slot, 0.0) + amount

    for i in range(len(ts)):
        bar_marks = {s: float(series[i]) for s, series in marks.items()}

        # 1. funding, before liquidation: paying it can be what tips a wallet over
        for instrument, rates in funding.items():
            rate = float(rates[i])
            if rate == 0.0:
                continue
            mark = bar_marks.get(instrument)
            if mark is None:
                continue
            for side in _SIDES:
                wallet = account.wallet(instrument, side)
                if wallet is None:
                    continue
                payment = wallet.qty * mark * rate
                cost = payment if side == "long" else -payment
                slot = (instrument, side)
                funding_paid[slot] = funding_paid.get(slot, 0.0) + cost
                credit(slot, -cost)
            account.apply_funding(instrument, rate, mark)

        # 2. liquidation on the mark, not on the trade price
        for instrument, mark in bar_marks.items():
            for side in _SIDES:
                if account.wallet(instrument, side) is None:
                    continue
                if account.is_liquidated(instrument, side, mark):
                    lost = account.liquidate(instrument, side)
                    slot = (instrument, side)
                    credit(slot, lost)
                    result.liquidations.append(slot)

        # 3. the bar's fills
        for fill in by_bar.get(i, []):
            credit(fill.slot, -fill.commission)
            account.balance -= fill.commission
            if fill.opening:
                if account.wallet(fill.instrument, fill.direction) is not None:
                    continue  # already in: the engine re-issues, the account does not stack
                try:
                    account.open(fill.instrument, fill.direction, fill.qty, fill.price, leverage)
                except (Liquidated, ValueError) as exc:
                    result.denied.append((fill.slot, type(exc).__name__))
            else:
                if account.wallet(fill.instrument, fill.direction) is None:
                    continue  # already liquidated: nothing left to close
                if account.is_bankrupt(fill.instrument, fill.direction, fill.price):
                    # The fill is through the bankruptcy price — a gap the mark never printed.
                    # The venue liquidates there rather than filling, so record it as one.
                    credit(fill.slot, account.liquidate(fill.instrument, fill.direction))
                    result.liquidations.append(fill.slot)
                else:
                    credit(fill.slot, account.close(fill.instrument, fill.direction, fill.price))

        result.equity[i] = account.equity(bar_marks)

    result.contributions = contributions
    result.funding_paid = funding_paid
    return result


# ── signal-driven replay: the account sizes (P3-20, ADR-0035) ─────────────────────────


@dataclass(frozen=True, slots=True)
class SlotPlan:
    """One slot's decisions, bar by bar — what it *wants*, never what it gets.

    ``signals`` is the stream the ``signals`` sandbox job already produces. A signal whose
    direction is not this slot's own side reads as flat here: INV-91 guarantees a strategy only
    ever emits its own side, so anything else would be a bug upstream rather than an instruction.
    """

    instrument: str
    direction: ScopeDirection
    signals: tuple[Signal, ...]

    @property
    def slot(self) -> Slot:
        return (self.instrument, self.direction)

    def wants(self, bar: int) -> Signal | None:
        if bar >= len(self.signals):
            return None
        sig = self.signals[bar]
        return sig if sig.direction == self.direction and sig.stop_distance > 0 else None


@dataclass(frozen=True, slots=True)
class Opened:
    """A position as the account actually opened it."""

    bar: int
    ts: int
    instrument: str
    direction: ScopeDirection
    qty: float
    price: float
    stop: float
    leverage: int = 0


@dataclass(frozen=True, slots=True)
class _EntryTerms:
    leverage: int
    margin: float
    fee: float


@dataclass(slots=True)
class SignalReplay:
    ts: npt.NDArray[np.datetime64]
    equity: npt.NDArray[np.float64]
    # Per slot, cumulative and per bar: realized PnL, fees and funding, plus what an open wallet
    # is currently worth. Starting equity plus these sums to account equity at every bar,
    # including while positions are open — which is what makes a per-slot chart a decomposition.
    contributions: dict[Slot, npt.NDArray[np.float64]] = field(default_factory=dict)
    funding_paid: dict[Slot, float] = field(default_factory=dict)
    opened: list[Opened] = field(default_factory=list)
    liquidations: list[Slot] = field(default_factory=list)
    denied: list[tuple[Slot, str]] = field(default_factory=list)
    ambiguous_bars: int = 0
    # Observation only. `kill_switch_drawdown` is Group A in §10.1 — it "only matters live" — so
    # a backtest records the breach and changes nothing. Acting on it here would invent a
    # research-path rule the architecture deliberately does not have.
    drawdown_breach_at: np.datetime64 | None = None

    @property
    def returns(self) -> npt.NDArray[np.float64]:
        if len(self.equity) < 2:
            return np.zeros(0)
        previous = self.equity[:-1]
        out = np.zeros(len(self.equity) - 1)
        alive = previous > 0  # isolation floors equity at 0; a dead account stopped moving
        out[alive] = self.equity[1:][alive] / previous[alive] - 1.0
        return out


def replay_signals(
    plans: Sequence[SlotPlan],
    bars: Mapping[str, Bars],
    inputs: Mapping[str, PerpBundle],
    initial_cash: float,
    settings: RiskSettings,
    costs: CostModel,
    leverage: int = 5,
    max_portfolio_risk_pct: float = 0.10,
    max_drawdown: float = 0.20,
    clearance: float = 0.25,
) -> SignalReplay:
    """Walk one shared USDT account through every slot's signals.

    **The account does the sizing.** That is the whole difference from replaying fills: a fill's
    quantity was sized against its own standalone equity, so reusing it here would risk a
    different fraction of a different balance — 1.25% of 80,000 for an order sized from 100,000.
    Here each bar snapshots the shared equity once, and :func:`admit_batch` sizes every entry of
    that bar from that one number (INV-92).

    Per bar, in the order ``PROTOCOL["replay"]`` fixes: funding, then liquidation and stops on the
    mark and the intrabar path, then one equity snapshot, then admission. Entries fill at the
    **next** bar's open (ADR-0003) with the stop anchored to the signal bar's close, which is what
    the Nautilus-driven single-strategy path does — and what
    ``test_one_member_reproduces_the_single_strategy_curve`` pins this against.
    """
    axis = _common_axis(bars)
    n = len(axis)
    account = PerpAccount(
        balance=initial_cash,
        tables={s: BracketTable.from_rows(s, b.brackets) for s, b in inputs.items()},
    )
    result = SignalReplay(ts=axis, equity=np.empty(n, dtype=np.float64))
    by_slot = {p.slot: p for p in plans}
    booked: dict[Slot, float] = dict.fromkeys(by_slot, 0.0)
    stop_level: dict[Slot, float] = {}
    stop_distance: dict[Slot, float] = {}
    # Exits decided at a bar's close, filling at the next bar's open — the same next-open
    # convention entries use (ADR-0003). Closing at the deciding bar's own close would price the
    # exit on information the order could not have acted on.
    exit_pending: set[Slot] = set()
    curves: dict[Slot, npt.NDArray[np.float64]] = {
        slot: np.zeros(n, dtype=np.float64) for slot in by_slot
    }
    result.funding_paid = dict.fromkeys(by_slot, 0.0)

    def book(slot: Slot, amount: float) -> None:
        """Attribute a balance change the **account** already made to the slot that caused it.

        `PerpAccount.open` and `.close` move the shared balance themselves, so these must only be
        recorded. Charging them again here double-counted the margin, which showed up as equity
        drifting away from the summed contributions.
        """
        booked[slot] = booked[slot] + amount

    def charge(slot: Slot, amount: float) -> None:
        """A balance change the account does not know about — fees. Both moved and recorded."""
        account.balance += amount
        booked[slot] = booked[slot] + amount

    def pinned_leverage(symbol: str) -> int | None:
        """Binance keeps one leverage setting per symbol, shared by long and short wallets."""
        for side in _SIDES:
            wallet = account.wallet(symbol, side)
            if wallet is not None:
                return wallet.leverage
        return None

    def admission_terms(
        instrument: str,
        direction: ScopeDirection,
        qty: float,
        entry: float,
        stop: float,
    ) -> _EntryTerms | str:
        """Choose the lowest safe leverage before mutating the account."""
        if account.wallet(instrument, direction) is not None:
            return "already_open"
        if leverage < 1:
            return "leverage_cap"
        if (direction == "long" and stop >= entry) or (direction == "short" and stop <= entry):
            return "clearance"

        table = account._table(instrument)
        notional = abs(qty * entry)
        fee = notional * float(costs.taker_rate)
        try:
            bracket = table.bracket_for(notional)
        except ValueError:
            return "bracket"

        pinned = pinned_leverage(instrument)
        if pinned is not None:
            candidates: Sequence[int] = (pinned,)
        else:
            candidates = range(1, min(int(leverage), int(bracket.max_leverage)) + 1)

        last_reason = "free_balance"
        for lev in candidates:
            if lev < 1 or lev > leverage or lev > bracket.max_leverage:
                return "leverage_cap"
            try:
                margin = table.initial_margin(notional, int(lev))
            except ValueError:
                return "leverage_cap"
            if margin + fee > account.free:
                if pinned is not None:
                    return "free_balance"
                continue
            if not _has_clearance(table, direction, qty, entry, margin, stop, clearance):
                if pinned is not None:
                    return "clearance"
                last_reason = "clearance"
                continue
            return _EntryTerms(int(lev), margin, fee)
        return last_reason

    for bar in range(n):
        marks = {
            symbol: float(bundle.marks.close[bar])
            for symbol, bundle in inputs.items()
            if bar < len(bundle)
        }

        # 0. exits decided at the previous close fill at this bar's open, before anything else
        for slot in sorted(exit_pending):
            plan = by_slot[slot]
            wallet = account.wallet(plan.instrument, plan.direction)
            if wallet is None:
                continue
            fill = float(bars[plan.instrument].open[bar])
            charge(slot, -abs(wallet.qty * fill) * float(costs.taker_rate))
            margin = wallet.margin
            book(slot, margin + account.close(plan.instrument, plan.direction, fill))
            stop_level.pop(slot, None)
            stop_distance.pop(slot, None)
        exit_pending.clear()

        # 1. funding, then liquidation and the stop, per open slot
        for slot, plan in sorted(by_slot.items()):
            wallet = account.wallet(plan.instrument, plan.direction)
            if wallet is None:
                continue
            bundle = inputs[plan.instrument]
            if bar >= len(bundle):
                continue
            levels = [account.liquidation_price(plan.instrument, plan.direction)]
            for row in bundle.funding_at(bar):
                rate, at_mark = float(row[2]), float(row[3])
                payment = wallet.qty * at_mark * rate
                cost = payment if plan.direction == "long" else -payment
                result.funding_paid[slot] += cost
                account.apply_funding(plan.instrument, rate, at_mark)
                levels.append(account.liquidation_price(plan.instrument, plan.direction))

            path = bundle.paths[bar]
            per_segment = levels[: len(path.segments)]
            while len(per_segment) < len(path.segments):
                per_segment.append(per_segment[-1])
            outcome = resolve_bar(plan.direction, stop_level[slot], path, per_segment)
            if outcome.ambiguous:
                result.ambiguous_bars += 1
            if outcome.event == "liquidation":
                # The margin was booked against this slot when it opened and is simply gone;
                # `liquidate` returns nothing to the shared balance, so nothing is recorded here.
                account.liquidate(plan.instrument, plan.direction)
                result.liquidations.append(slot)
                stop_level.pop(slot, None)
                stop_distance.pop(slot, None)
            elif outcome.event == "stop":
                fill = _stop_fill(plan.direction, stop_level[slot], float(bundle.marks.open[bar]))
                charge(slot, -abs(wallet.qty * fill) * float(costs.taker_rate))
                margin = wallet.margin
                book(slot, margin + account.close(plan.instrument, plan.direction, fill))
                stop_level.pop(slot, None)
                stop_distance.pop(slot, None)
            elif plan.wants(bar) is None:
                exit_pending.add(slot)  # leaves at the next bar's open, like every other order

        # 2. one equity snapshot for the whole bar — this is what every entry sizes from
        equity = account.equity(marks)
        result.equity[bar] = equity
        for slot in by_slot:
            plan = by_slot[slot]
            wallet = account.wallet(plan.instrument, plan.direction)
            value = wallet.value(marks[plan.instrument]) if wallet is not None else 0.0
            curves[slot][bar] = booked[slot] + value
        if result.drawdown_breach_at is None and account.kill_switch_tripped(marks, max_drawdown):
            result.drawdown_breach_at = axis[bar]

        # 3. admission, on that snapshot, in canonical order
        if bar + 1 >= n:
            continue  # nothing fills at a bar that has no successor (ADR-0003)
        open_risk = 0.0
        for slot, distance in stop_distance.items():
            plan = by_slot[slot]
            wallet = account.wallet(plan.instrument, plan.direction)
            if wallet is not None:
                open_risk += wallet.qty * distance  # commitment at the stop, frozen at entry
        requests: list[EntryRequest] = []
        wanted: dict[Slot, tuple[Signal, float, float]] = {}
        for slot, plan in sorted(by_slot.items()):
            if account.wallet(plan.instrument, plan.direction) is not None:
                continue
            sig = plan.wants(bar)
            if sig is None or bar + 1 >= len(bars[plan.instrument]):
                continue
            entry = float(bars[plan.instrument].open[bar + 1])
            anchor = float(bars[plan.instrument].close[bar])
            trigger = (
                anchor - sig.stop_distance
                if plan.direction == "long"
                else anchor + sig.stop_distance
            )
            if trigger <= 0:
                continue
            wanted[slot] = (sig, entry, trigger)
            requests.append(EntryRequest(plan.instrument, plan.direction, entry, sig.stop_distance))
        if not requests:
            continue
        for decision in admit_batch(
            requests, equity, settings.max_risk_pct, max_portfolio_risk_pct, open_risk
        ):
            slot = (decision.instrument, decision.direction)
            if not decision.admitted:
                result.denied.append((slot, decision.reason))
                continue
            sig, entry, trigger = wanted[slot]
            terms = admission_terms(
                decision.instrument, decision.direction, decision.quantity, entry, trigger
            )
            if isinstance(terms, str):
                result.denied.append((slot, terms))
                continue
            try:
                account.open(
                    decision.instrument,
                    decision.direction,
                    decision.quantity,
                    entry,
                    terms.leverage,
                )
            except (Liquidated, ValueError, KeyError) as exc:
                result.denied.append((slot, type(exc).__name__))
                continue
            wallet = account.wallet(decision.instrument, decision.direction)
            book(slot, -wallet.margin)  # `open` already moved it out of the shared balance
            charge(slot, -terms.fee)
            stop_level[slot] = trigger
            stop_distance[slot] = sig.stop_distance
            result.opened.append(
                Opened(
                    bar + 1, int(axis[bar + 1].astype("int64")), decision.instrument,
                    decision.direction, decision.quantity, entry, trigger, terms.leverage,
                )
            )  # fmt: skip

    result.contributions = curves
    return result


def _stop_fill(side: ScopeDirection, trigger: float, bar_open: float) -> float:
    """Where a protective stop actually fills: at its trigger, or worse if the bar gapped past it
    before the stop could be reached. Never better — that would be a free option."""
    return min(trigger, bar_open) if side == "long" else max(trigger, bar_open)


def _has_clearance(
    table: BracketTable,
    side: ScopeDirection,
    qty: float,
    entry: float,
    margin: float,
    stop: float,
    fraction: float,
) -> bool:
    """Admission-time liquidation clearance, before an account wallet exists."""
    if side == "long" and stop >= entry:
        return False
    if side == "short" and stop <= entry:
        return False
    distance = abs(entry - stop)
    liquidation = table.liquidation_price(side, qty, entry, margin)
    clearance = (stop - liquidation) if side == "long" else (liquidation - stop)
    return clearance >= fraction * distance


def _common_axis(bars: Mapping[str, Bars]) -> npt.NDArray[np.datetime64]:
    """One timestamp axis for every slot, refusing a set that does not share it.

    Not a union: a slot whose bars are offset from the others would have its funding and
    liquidation resolved against another instrument's bar, which is the desynchronisation
    ADR-0034 exists to prevent.
    """
    if not bars:
        raise ValueError("a replay needs at least one instrument")
    series = list(bars.values())
    axis = series[0].ts.astype("datetime64[ns]")
    for other in series[1:]:
        if len(other) != len(axis) or not np.array_equal(other.ts.astype("datetime64[ns]"), axis):
            raise ValueError(
                "every instrument in one replay must share a timestamp axis; "
                f"{other.symbol} does not"
            )
    return axis
