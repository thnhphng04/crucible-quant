"""Host-side USDT-M account: isolated wallets per side (Architecture §3.4, §3.5, ADR-0032).

Nautilus supplies the position and fill model. It cannot supply this: ``MarginAccount`` keys
margin by ``InstrumentId`` alone, so it cannot hold one symbol's LONG and SHORT isolated wallets
apart — which is exactly what Binance hedge mode is — and the backtest engine settles no funding
and models no liquidation at all.

So the account is replayed here, from fills. ``execution.engine._summarize`` already reconstructs
equity host-side from fills under a cash account, so this is a widening of a tested shape rather
than a new one.

**The cost, stated plainly:** P5 (backtest ≡ live) survives for signal → order → fill, including
ADR-0003's next-open convention. It does **not** survive for margin, funding and liquidation —
that arithmetic is ours, and ADR-0032 records reconciling it against the venue as an obligation
before any capital, not a footnote.

One wallet is one ``(symbol, side)``. Isolated means isolated: a wallet's loss stops at its own
margin and never reaches the account's free balance or the other side.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from quantcrucible.core.path_summary import SegmentedPath
from quantcrucible.core.strategy.base import ScopeDirection
from quantcrucible.execution.margin import BracketTable


class Liquidated(RuntimeError):
    """A wallet could not be funded, or was wiped out."""


@dataclass(slots=True)
class Wallet:
    """One side's isolated position and the margin backing it."""

    symbol: str
    side: ScopeDirection
    qty: float
    entry: float
    leverage: int
    margin: float  # the isolated balance; funding and fees move it

    @property
    def notional(self) -> float:
        return self.qty * self.entry

    def unrealized(self, mark: float) -> float:
        move = mark - self.entry
        return move * self.qty if self.side == "long" else -move * self.qty

    def bounded_pnl(self, price: float) -> float:
        """The PnL this wallet can actually realize: a loss stops at its own margin.

        Beyond that the position is bankrupt and the venue's insurance fund, not the free
        balance, carries the rest. Without the bound one wallet can drain the account, and a
        wallet's worst case stops being knowable — which is what the portfolio risk cap counts on.
        """
        return max(self.unrealized(price), -self.margin)

    def value(self, mark: float) -> float:
        """What this wallet is worth to the account: margin plus mark-to-market, never below 0."""
        return self.margin + self.bounded_pnl(mark)


@dataclass(slots=True)
class PerpAccount:
    """One USDT balance, many isolated wallets."""

    balance: float  # free USDT, outside every wallet
    tables: Mapping[str, BracketTable]
    _wallets: dict[tuple[str, str], Wallet] = field(default_factory=dict)
    _peak: float = 0.0

    def __post_init__(self) -> None:
        self._peak = self.balance

    # ── inspection ────────────────────────────────────────────────────────────────────

    @property
    def free(self) -> float:
        return self.balance

    def wallet(self, symbol: str, side: ScopeDirection) -> Wallet:
        """The open wallet for one side, or ``None`` when that side is flat.

        Typed as ``Wallet`` for the call sites that have just opened one; a closed side returns
        ``None`` and every caller checks.
        """
        return self._wallets.get((symbol, side))  # type: ignore[return-value]

    def _table(self, symbol: str) -> BracketTable:
        try:
            return self.tables[symbol]
        except KeyError:
            raise KeyError(f"no bracket table for {symbol!r}: it cannot be traded") from None

    def equity(self, marks: Mapping[str, float]) -> float:
        """Free balance, plus every wallet's margin and its mark-to-market."""
        total = self.balance
        for wallet in self._wallets.values():
            mark = marks.get(wallet.symbol)
            total += wallet.margin if mark is None else wallet.value(mark)
        return total

    def kill_switch_tripped(self, marks: Mapping[str, float], max_drawdown: float) -> bool:
        """Account-level drawdown against the running peak (D8). Unchanged in meaning by the
        move to perpetuals — it is measured on the joint account, as it always was."""
        now = self.equity(marks)
        self._peak = max(self._peak, now)
        if self._peak <= 0:
            return True
        return (self._peak - now) / self._peak > max_drawdown

    # ── position lifecycle ────────────────────────────────────────────────────────────

    def open(
        self, symbol: str, side: ScopeDirection, qty: float, price: float, leverage: int
    ) -> Wallet:
        if (symbol, side) in self._wallets:
            raise ValueError(f"{symbol} {side} is already open")
        table = self._table(symbol)
        notional = qty * price
        margin = table.initial_margin(notional, leverage)  # raises above the bracket
        if margin > self.balance:
            raise Liquidated(
                f"{symbol} {side}: cannot fund {margin:,.2f} of initial margin from "
                f"{self.balance:,.2f} free"
            )
        self.balance -= margin
        wallet = Wallet(symbol, side, qty, price, leverage, margin)
        self._wallets[(symbol, side)] = wallet
        return wallet

    def close(self, symbol: str, side: ScopeDirection, price: float) -> float:
        """Realize the position and return its margin plus PnL to the free balance.

        The loss is bounded by the wallet's own margin (:meth:`Wallet.bounded_pnl`). A fill
        through the bankruptcy price is reachable — the mark can gap past liquidation between
        bars — and it must not reach the free balance or the other side.
        """
        wallet = self._wallets.pop((symbol, side), None)
        if wallet is None:
            raise Liquidated(f"{symbol} {side} is not open")
        pnl = wallet.bounded_pnl(price)
        self.balance += wallet.margin + pnl
        return pnl

    def is_bankrupt(self, symbol: str, side: ScopeDirection, price: float) -> bool:
        """Whether closing at ``price`` would take more than the wallet's whole margin — i.e.
        the venue would have liquidated rather than filled."""
        wallet = self._wallets.get((symbol, side))
        return wallet is not None and wallet.unrealized(price) <= -wallet.margin

    def liquidate(self, symbol: str, side: ScopeDirection) -> float:
        """Wipe out one wallet. Its margin is lost and nothing else is: that is what isolated
        means, and it is why a liquidation never reaches the other side or the free balance."""
        wallet = self._wallets.pop((symbol, side), None)
        if wallet is None:
            raise Liquidated(f"{symbol} {side} is not open")
        return -wallet.margin

    # ── mark, funding, liquidation ────────────────────────────────────────────────────

    def liquidation_price(self, symbol: str, side: ScopeDirection) -> float:
        wallet = self._wallets[(symbol, side)]
        return self._table(symbol).liquidation_price(side, wallet.qty, wallet.entry, wallet.margin)

    def is_liquidated(self, symbol: str, side: ScopeDirection, mark: float) -> bool:
        price = self.liquidation_price(symbol, side)
        return mark <= price if side == "long" else mark >= price

    def apply_funding(self, symbol: str, rate: float, mark: float) -> None:
        """Settle one funding event against both sides of a symbol.

        A positive rate means longs pay shorts. The payment leaves the isolated wallet, so paying
        funding brings that side's liquidation closer — which is why it cannot be netted into a
        single account-level number.
        """
        for side in ("long", "short"):
            wallet = self._wallets.get((symbol, side))
            if wallet is None:
                continue
            payment = wallet.qty * mark * rate
            wallet.margin += -payment if side == "long" else payment


class ClearanceRefused(RuntimeError):
    """Liquidation sits too close to the stop, or the stop is on the wrong side of entry."""


@dataclass(frozen=True, slots=True)
class BarOutcome:
    """What one bar did to one side.

    ``minute`` is the offset within the bar at which the reported event happened, or ``None``
    when nothing happened. ``ambiguous`` means the stop and the liquidation were first reached
    in the same minute: the order inside that minute is unknown, so ``event`` reports the worse
    of the two and the flag says the result was assumed, not observed.
    """

    event: str  # open | stop | liquidation
    ambiguous: bool = False
    minute: int | None = None


def resolve_bar(
    side: ScopeDirection,
    stop: float,
    path: SegmentedPath,
    liquidations: Sequence[float],
) -> BarOutcome:
    """Whether a bar's intrabar path stopped the position out, liquidated it, or neither.

    Both levels sit on the same side of the entry — below a long, above a short — so one path
    answers both questions. The **stop is constant** for the life of the position (P3-16: it is
    placed once at entry and never re-issued), while the **liquidation price moves at every
    funding settlement inside the bar**, which is why ``liquidations`` is one price per segment
    rather than a single number.

    A tie is resolved pessimistically and flagged. Note that with a stop honouring the clearance
    rule the plain liquidation branch is rare by construction: the levels are ordered, so on one
    monotone series the stop is reached first unless the liquidation moved under it mid-bar.
    """
    above = side == "short"
    stop_at = path.first_touch(stop, above=above)
    liq_at = path.first_touch_stepwise(liquidations, above=above)
    if stop_at is None and liq_at is None:
        return BarOutcome("open")
    if liq_at is None:
        return BarOutcome("stop", minute=stop_at)
    if stop_at is None:
        return BarOutcome("liquidation", minute=liq_at)
    if liq_at == stop_at:
        return BarOutcome("liquidation", ambiguous=True, minute=liq_at)  # worse, and say so
    if liq_at < stop_at:
        return BarOutcome("liquidation", minute=liq_at)
    return BarOutcome("stop", minute=stop_at)


def assert_clearance(
    account: PerpAccount, symbol: str, side: ScopeDirection, stop: float, fraction: float
) -> None:
    """Liquidation must sit at least ``fraction`` of the entry-to-stop distance beyond the stop.

    At low leverage this is close to vacuous — leverage 1 puts liquidation roughly a whole entry
    move away, far outside any few-ATR stop. It binds at high leverage or with a very wide stop,
    which is the case worth testing (ADR-0032).
    """
    wallet = account.wallet(symbol, side)
    if wallet is None:
        raise ClearanceRefused(f"{symbol} {side} is not open")
    entry = wallet.entry
    if side == "long" and stop >= entry:
        raise ClearanceRefused(f"{symbol} long: the stop must be below the entry, got {stop}")
    if side == "short" and stop <= entry:
        raise ClearanceRefused(f"{symbol} short: the stop must be above the entry, got {stop}")
    distance = abs(entry - stop)
    liquidation = account.liquidation_price(symbol, side)
    clearance = (stop - liquidation) if side == "long" else (liquidation - stop)
    if clearance < fraction * distance:
        raise ClearanceRefused(
            f"{symbol} {side}: liquidation {liquidation:,.2f} leaves clearance "
            f"{clearance:,.2f} beyond the stop, below {fraction:.0%} of the "
            f"{distance:,.2f} entry-to-stop distance"
        )
