"""Run a :class:`~quantcrucible.core.strategy.base.Strategy` inside NautilusTrader (§3.5, P5).

Execution rules (ADR-0003):

* A signal computed on the CLOSED bar t executes at the OPEN of bar t+1. Nautilus fills an order
  submitted in ``on_bar`` at the bar's close, so every bar t+1 is preceded by a synthetic trade
  tick at its open price, 1 ns after bar t+1 opened (= after bar t closed, unless the data has
  a gap), and orders carry 1 ns of latency.
* Limit orders fill only when price trades THROUGH the limit (``prob_fill_on_limit=0``).
* Slippage is charged as extra taker fee (bps); crypto tick sizes make "1 tick" meaningless.
* Spot venue, cash account: long or flat. A short signal means flat (counted, never traded).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import numpy as np
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, LatencyModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import Bar, BarType, TradeTick
from nautilus_trader.model.enums import (
    AccountType,
    AggressorSide,
    CurrencyType,
    OmsType,
    OrderSide,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy as NautilusStrategy

from quantcrucible.core.strategy.base import Bars, Signal, Strategy, step
from quantcrucible.data.source import timeframe_delta

PRICE_PRECISION = 8
SIZE_PRECISION = 8
OPEN_TICK_OFFSET_NS = 1
REBALANCE_BAND = 0.25  # an open position is resized only if the target moves by more than 25%


@dataclass(frozen=True, slots=True)
class CostModel:
    """Trading costs, locked per campaign. Gate ⑥′ doubles fee and slippage."""

    fee_rate: float = 0.001  # Binance spot taker
    slippage_bps: float = 5.0

    @property
    def taker_rate(self) -> Decimal:
        return Decimal(str(round(self.fee_rate + self.slippage_bps / 10_000, 10)))

    def scaled(self, fee: float = 1.0, slippage: float = 1.0) -> CostModel:
        return CostModel(self.fee_rate * fee, self.slippage_bps * slippage)


@dataclass(frozen=True, slots=True)
class FillRecord:
    ts: int  # ns
    symbol: str
    side: str  # BUY | SELL
    qty: float
    price: float
    commission: float  # quote currency


@dataclass(slots=True)
class BridgeLog:
    fills: list[FillRecord] = field(default_factory=list)
    signals: dict[str, int] = field(default_factory=lambda: {"long": 0, "short": 0, "flat": 0})
    denied_orders: int = 0
    bars_seen: int = 0


def venue_for(exchange: str) -> Venue:
    return Venue(exchange.upper())


def nautilus_symbol(symbol: str) -> str:
    return symbol.replace("/", "")


def _currency(code: str) -> Currency:
    try:
        return Currency.from_str(code)
    except ValueError:
        return Currency(code, SIZE_PRECISION, 0, code, CurrencyType.CRYPTO)


def make_instrument(symbol: str, venue: Venue, costs: CostModel) -> CurrencyPair:
    base, quote = symbol.split("/")
    raw = Symbol(nautilus_symbol(symbol))
    return CurrencyPair(
        instrument_id=InstrumentId(raw, venue),
        raw_symbol=raw,
        base_currency=_currency(base),
        quote_currency=_currency(quote),
        price_precision=PRICE_PRECISION,
        size_precision=SIZE_PRECISION,
        price_increment=Price(10**-PRICE_PRECISION, PRICE_PRECISION),
        size_increment=Quantity(10**-SIZE_PRECISION, SIZE_PRECISION),
        lot_size=None,
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
        maker_fee=costs.taker_rate,
        taker_fee=costs.taker_rate,
        ts_event=0,
        ts_init=0,
    )


def bar_type_for(instrument: CurrencyPair, timeframe: str) -> BarType:
    unit = {"m": "MINUTE", "h": "HOUR", "d": "DAY"}[timeframe[-1]]
    return BarType.from_str(f"{instrument.id}-{int(timeframe[:-1])}-{unit}-LAST-EXTERNAL")


def to_nautilus_data(bars: Bars, instrument: CurrencyPair) -> tuple[list[Bar], list[TradeTick]]:
    """Bars (stamped at close) + one synthetic open tick before every bar but the first.

    The tick sits 1 ns after the bar's own OPEN time (close − timeframe). With contiguous data
    that is 1 ns after the previous close; across a gap in the data, an order waits for the
    next bar to actually open instead of filling early at a price printed later.
    """
    bt = bar_type_for(instrument, bars.timeframe)
    ts = bars.ts.astype("datetime64[ns]").astype(np.int64)
    period = int(timeframe_delta(bars.timeframe).total_seconds() * 1e9)
    overlap = np.flatnonzero(np.diff(ts) < period)
    if len(overlap):
        i = int(overlap[0])
        raise ValueError(
            f"{bars.symbol}: bars closing at {bars.ts[i]} and {bars.ts[i + 1]} overlap"
            f" (timeframe {bars.timeframe})"
        )
    out_bars: list[Bar] = []
    ticks: list[TradeTick] = []
    volume_cap = 10.0**9
    for i in range(len(bars)):
        t = int(ts[i])
        out_bars.append(
            Bar(
                bt,
                Price(float(bars.open[i]), PRICE_PRECISION),
                Price(float(bars.high[i]), PRICE_PRECISION),
                Price(float(bars.low[i]), PRICE_PRECISION),
                Price(float(bars.close[i]), PRICE_PRECISION),
                Quantity(min(float(bars.volume[i]), volume_cap), SIZE_PRECISION),
                t,
                t,
            )
        )
        if i > 0:
            t_open = int(ts[i]) - period + OPEN_TICK_OFFSET_NS
            ticks.append(
                TradeTick(
                    instrument.id,
                    Price(float(bars.open[i]), PRICE_PRECISION),
                    Quantity(volume_cap, SIZE_PRECISION),
                    AggressorSide.NO_AGGRESSOR,
                    TradeId(f"open-{i}"),
                    t_open,
                    t_open,
                )
            )
    return out_bars, ticks


class TargetSizer(Protocol):
    """Signal → target quantity in base units, given the closed-bar window and the account's
    equity in the quote currency (the Risk layer, §3.4; :class:`~quantcrucible.execution.risk.
    RiskSizer` in production)."""

    def target(self, symbol: str, signal: Signal, window: Bars, equity: float) -> float: ...


class BridgeConfig(StrategyConfig, frozen=True):
    pass


class BridgeStrategy(NautilusStrategy):  # type: ignore[misc]
    """Adapts one venue-agnostic strategy to Nautilus for a set of instruments."""

    def __init__(
        self,
        strategy: Strategy,
        instruments: Mapping[str, CurrencyPair],
        bar_types: Mapping[str, BarType],
        timeframe: str,
        sizer: TargetSizer,
        lookback: int,
        log: BridgeLog,
    ) -> None:
        super().__init__(BridgeConfig())
        self._strategy = strategy
        self._instruments = dict(instruments)
        self._bar_types = dict(bar_types)
        self._by_type = {str(bt): sym for sym, bt in bar_types.items()}
        self._timeframe = timeframe
        self._sizer = sizer
        self._lookback = lookback
        self._record = log
        self._history: dict[str, list[tuple[int, float, float, float, float, float]]] = {
            s: [] for s in instruments
        }
        self._last_close: dict[str, float] = {}

    def _equity(self) -> float:
        """Cash in the quote currency plus every open position marked at its last close."""
        venue = next(iter(self._instruments.values())).id.venue
        account = self.portfolio.account(venue)
        cash = float(account.balance_total(USDT)) if account is not None else 0.0
        held = sum(
            float(self.portfolio.net_position(inst.id)) * self._last_close.get(sym, 0.0)
            for sym, inst in self._instruments.items()
        )
        return cash + held

    def on_start(self) -> None:
        for bt in self._bar_types.values():
            self.subscribe_bars(bt)

    def _window(self, symbol: str) -> Bars:
        rows = self._history[symbol][-self._lookback :]
        arr = np.array(rows, dtype=np.float64)
        ts = np.array([r[0] for r in rows], dtype=np.int64).astype("datetime64[ns]")
        return Bars(
            symbol, self._timeframe, ts,
            arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5],
        )  # fmt: skip

    def on_bar(self, bar: Bar) -> None:
        symbol = self._by_type[str(bar.bar_type)]
        self._record.bars_seen += 1
        close = float(bar.close)
        self._history[symbol].append(
            (bar.ts_event, float(bar.open), float(bar.high), float(bar.low), close,
             float(bar.volume))
        )  # fmt: skip
        self._last_close[symbol] = close
        window = self._window(symbol)
        sig = step(self._strategy, window)
        self._record.signals[sig.direction] += 1
        target = self._sizer.target(symbol, sig, window, self._equity())
        if sig.direction != "long":
            target = 0.0  # spot, cash account: long or flat
        inst = self._instruments[symbol]
        current = float(self.portfolio.net_position(inst.id))
        self.cancel_all_orders(inst.id)  # the protective stop is re-issued every bar
        min_step = 10.0**-SIZE_PRECISION
        # already in: resize only when the target moved by more than the band
        if (
            current >= min_step
            and target >= min_step
            and abs(target - current) <= REBALANCE_BAND * max(target, current)
        ):
            target = current
        delta = target - current
        if abs(delta) >= min_step:
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            self.submit_order(self.order_factory.market(inst.id, side, inst.make_qty(abs(delta))))
        if target >= min_step and sig.direction == "long":
            trigger = close - sig.stop_distance
            if trigger > 0:
                self.submit_order(
                    self.order_factory.stop_market(
                        inst.id, OrderSide.SELL, inst.make_qty(target),
                        inst.make_price(trigger), reduce_only=True,
                    )
                )  # fmt: skip

    def on_order_filled(self, event: Any) -> None:
        symbol = next(s for s, i in self._instruments.items() if i.id == event.instrument_id)
        self._record.fills.append(
            FillRecord(
                ts=int(event.ts_event),
                symbol=symbol,
                side="BUY" if event.order_side == OrderSide.BUY else "SELL",
                qty=float(event.last_qty),
                price=float(event.last_px),
                commission=float(event.commission),
            )
        )

    def on_order_denied(self, event: Any) -> None:
        self._record.denied_orders += 1

    def on_order_rejected(self, event: Any) -> None:
        self._record.denied_orders += 1


def build_engine(
    bars_by_symbol: Mapping[str, Bars],
    exchange: str,
    costs: CostModel,
    initial_cash: float,
    seed: int = 0,
) -> tuple[BacktestEngine, dict[str, CurrencyPair], dict[str, BarType]]:
    """A Nautilus engine with the pessimistic fill model and the data loaded."""
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    venue = venue_for(exchange)
    engine.add_venue(
        venue,
        OmsType.NETTING,
        AccountType.CASH,
        [Money(initial_cash, USDT)],
        base_currency=None,
        fill_model=FillModel(prob_fill_on_limit=0.0, prob_slippage=0.0, random_seed=seed),
        latency_model=LatencyModel(base_latency_nanos=OPEN_TICK_OFFSET_NS),
    )
    instruments: dict[str, CurrencyPair] = {}
    bar_types: dict[str, BarType] = {}
    for symbol, bars in bars_by_symbol.items():
        inst = make_instrument(symbol, venue, costs)
        engine.add_instrument(inst)
        nb, ticks = to_nautilus_data(bars, inst)
        engine.add_data(nb)
        if ticks:
            engine.add_data(ticks)
        instruments[symbol] = inst
        bar_types[symbol] = bar_type_for(inst, bars.timeframe)
    return engine, instruments, bar_types
