# Multi-Market Platform: crypto + forex + stocks (international & Vietnam)

> Architecture for a single platform that can run across 4 market groups.
> Date: 2026-09-20 · Related: [05-SMOOTH-FLOW](05-SMOOTH-FLOW.md) · [03-FEASIBILITY-FOR-INDIVIDUALS](03-FEASIBILITY-FOR-INDIVIDUALS.md) · [04-SCHOOL-B-EFFICACY](04-SCHOOL-B-EFFICACY.md)

---

## 0. Correction to [05-SMOOTH-FLOW](05-SMOOTH-FLOW.md)

Document 05 recommends **Freqtrade**, but Freqtrade **only supports crypto**. Under the new scope (crypto + forex + international stocks + Vietnam), Freqtrade **is no longer the right choice**.

What remains valuable from 05: the seam-free principle, the 8 gate steps, and ledger + DSR/PBO. Only the engine changes.

---

## 1. Conclusion: NautilusTrader is the core, but Vietnam must be custom-built

```
                    ┌─────────────────────────────┐
                    │      NautilusTrader Core    │
                    │  (Rust engine + Python API) │
                    │  backtest ≡ live, 1 code    │
                    └──────────────┬──────────────┘
                                   │
        ┌──────────────┬───────────┼───────────┬──────────────┐
        ↓              ↓           ↓           ↓              ↓
  ┌──────────┐  ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌─────────────┐
  │ Binance  │  │  Bybit   │ │   IB    │ │Databento │ │ SSI/DNSE    │
  │  OKX     │  │          │ │         │ │          │ │ ⚠️ CUSTOM   │
  ├──────────┤  ├──────────┤ ├─────────┤ ├──────────┤ ├─────────────┤
  │ Crypto   │  │ Crypto   │ │ Forex   │ │ Futures  │ │ HOSE/HNX    │
  │ spot+perp│  │ perp     │ │ Stocks  │ │ data     │ │ VN30F1M     │
  │          │  │          │ │ Futures │ │ high     │ │             │
  │    ✅    │  │    ✅    │ │   ✅    │ │ quality  │ │     🔴      │
  └──────────┘  └──────────┘ └─────────┘ └──────────┘ └─────────────┘
       READY          READY       READY       READY       MUST BUILD
```

**Official adapters already available:** Binance, Bybit, OKX, Coinbase International, **Interactive Brokers**, Databento, Betfair, Polymarket.

> 🔑 **Interactive Brokers is the most important piece** — one adapter gives you **forex + international equities + global futures**. Three of the four market groups you need are already available, with no code to write.

**Vietnam is the only remaining gap.**

---

## 2. The Vietnam segment — the adapter must be custom-built

### 2.1. Available sources

| Provider | Capability |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **SSI FastConnect** | Split into 2 parts: **FC Data** (realtime data for both cash and derivatives) + **FC Trading** (account queries, order placement/modification/cancellation, order status via streaming). Provides **Python** sample code |
| **DNSE** | Strong focus on supporting algo trading through APIs, aimed at high-volume traders |
| **vnstock** | Python data library with SSI FastConnect integration documentation |

### 2.2. What must be written

NautilusTrader requires 3 components for a new adapter:

| Component | Responsibility |
| ---------------------------- | ---------------------------------------------------------------------- |
| **InstrumentProvider** | Parse the venue API response into Nautilus `Instrument` objects |
| **DataClient** | Connect, subscribe to instruments, and publish market data into the platform |
| **ExecutionClient** | Submit orders, receive fills, and **reconcile state on reconnect** |

The officially recommended testing approach is to *"exercise the adapter's Python surface inside integration tests, mocking the PyO3 boundary with stubbed Rust clients"*.

**Effort estimate:** 3–6 weeks for one person already familiar with NautilusTrader. The hardest part is **ExecutionClient reconciliation** — handling disconnects and reconnects correctly while open positions still exist.

### 2.3. 🔴 But before writing it — read this carefully

From [03-FEASIBILITY-FOR-INDIVIDUALS](03-FEASIBILITY-FOR-INDIVIDUALS.md) and source report [92](../research_docs_vi/92-NGUON-THI-TRUONG-CHIEN-LUOC.md), the Vietnamese market has **3 characteristics that break backtest realism**:

| Characteristic | Platform consequence |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Price limit ±7% (HOSE)** | When the price hits the ceiling/floor, orders can pile up with **no counterparty** ("lock"). A backtest will tell you the order filled; reality will not. **This is the most severe simulation error, and it does not exist in the US market** |
| **T+2 equities** | Shares cannot be turned over intraday. Any intraday strategy on Vietnamese equities is **not feasible** |
| **No short-selling yet** | Long-only. This removes half the alpha of long-short strategies |

> 🎯 **Design consequence:** for Vietnam, **VN30F1M (futures) is the only realistic target** for systematic trading — it is **T+0**, can be shorted through futures positions, and does not suffer equity-style lock conditions.
>
> → The phase-1 Vietnam adapter should **support derivatives only** and skip the cash market. This cuts roughly 60% of the work and avoids all 3 traps above.

---

## 3. The hardest problem is not the adapter — it is normalization

Even once the adapters exist, the job is not done. The four market groups differ in **everything**:

### 3.1. Trading calendars

| Market | Session |
| -------------- | ----------------------------------------- |
| Crypto | 24/7, no breaks |
| Forex | 24/5 |
| US stocks | 9:30–16:00 ET |
| **HOSE** | 9:00–15:00 ICT, **with a lunch break** |

Misaligning bars across sessions → **creates look-ahead without you noticing**. This is a quiet and lethal error.

### 3.2. Position units

1 BTC ≠ 1 lot of EURUSD ≠ 100 HOSE shares ≠ 1 VN30F1M contract (multiplier 100,000 VND/point).

> 🔑 **Core design principle: a strategy must NEVER speak in lots/shares/contracts. A strategy only speaks in RISK units.**
>
> ```python
> # ❌ WRONG — tied to a venue
> buy(size=0.5)  # 0.5 of what?
>
> # ✅ RIGHT — venue-agnostic
> buy(risk_pct=0.5, stop_distance=2*atr)
> # → the Position Sizer converts this into lots/shares/contracts per venue
> ```
>
> ⚠️ *Update 21 Sep 2026:* combined with the vol targeting in §3.3, this example **divides by volatility twice** (the stop = k×ATR and the vol scalar both shrink with volatility). The corrected contract is in [Architecture_Design.md](Architecture_Design.md) §3.3–3.4: `Signal(strength, stop_distance)` — vol targeting is the **only** place volatility enters the size; the stop is just a risk cap (`min`, not multiply).

This abstraction layer is **mandatory** if one strategy is meant to run across 4 markets.

### 3.3. Vol scaling — what determines whether you actually capture breadth

[04-SCHOOL-B-EFFICACY](04-SCHOOL-B-EFFICACY.md) shows Sharpe going from 0.4 → 1.60 thanks to breadth. But that only works **when every position is scaled to the same volatility target** (Hurst-Ooi-Pedersen use 10%/year).

Without this, BTC (vol ~60%/year) will consume the entire portfolio risk budget, while VN30F1M (vol ~20%) will be effectively invisible → **you may have 4 markets, but still only 1 bet**.

### 3.4. Currencies

USD (crypto, US stocks) · USD/EUR/JPY (forex) · **VND** (Vietnam). A unified conversion and PnL accounting layer is required.

---

## 4. Proposed architecture

```
┌──────────────────────────────────────────────────────────┐
│ STRATEGY LAYER — venue-agnostic, speaks in risk units    │
│ • Agent generates code here                              │
│ • Only uses: returns, ATR, z-score (NO absolute prices)  │
└───────────────────────┬──────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│ RISK & SIZING LAYER                                      │
│ • Vol targeting → scale every market to 10%/year         │
│ • strength + stop_distance → lots/shares/contracts       │
│ • Currency conversion, unified PnL accounting            │
└───────────────────────┬──────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│ NAUTILUS CORE — backtest ≡ live, same code               │
└───────────────────────┬──────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│ ADAPTER LAYER                                            │
│ Binance/Bybit ✅ │ IB ✅ │ Databento ✅ │ SSI 🔴 custom │
└──────────────────────────────────────────────────────────┘

        ┌─────────────────────────────────────────┐
        │ PARALLEL — venue-independent            │
        │ • Trial ledger (SQLite/Postgres)        │
        │ • Validation: DSR/PBO/CPCV (purgedcv)   │
        │ • Guardrail: AST similarity, lookahead  │
        │ • Leaky oracle test                     │
        └─────────────────────────────────────────┘
```

The top three layers **know nothing about venues**. That is the condition for the platform to be truly multi-market.

---

## 5. Build order — more important than the architecture

⚠️ **Do not build all 4 markets at once.** Build in ascending order of difficulty, and only move on after validating each step.

| Phase | Market | Adapter | Why this order | Estimate |
| ----------- | --------------------------------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| **1** | **Crypto** | Binance ✅ ready | Free data, 24/7 (no session issue), no roll issue, adapter already available. **Debug the entire architecture here at $0 cost** | 4–6 weeks |
| **2** | **Forex + international stocks** | IB ✅ ready | One adapter for both. First encounter with the session/calendar problem | 3–4 weeks |
| **3** | **Multi-asset futures** | Databento / IB ✅ | This is where real breadth comes from (Sharpe → 1.6). First encounter with continuous contract roll | 3–4 weeks |
| **4** | **Vietnam (VN30F1M)** | SSI 🔴 custom | Hardest, with the lowest marginal value. **Leave it for last** | 3–6 weeks |

**Realistic total: 4–6 months** if done properly, excluding the validation layer and the agent loop.

### Why Vietnam comes last

Not because it is unimportant, but because:

1. It is the **only adapter that must be custom-built** → highest technical risk
2. Only VN30F1M is usable → **1 instrument**, contributing almost zero breadth
3. Until short-selling and T+0 arrive (2026–2028 roadmap), the potential remains capped
4. But: **if you have an informational edge in the Vietnamese market**, this is an advantage foreign traders do not have. That is the only legitimate reason to prioritize it earlier

---

## 6. Direct answer: should you build it?

**Yes, the architecture is feasible.** NautilusTrader is designed properly for multi-venue trading, and 3/4 markets already have available adapters.

**But be honest with yourself about three things:**

**1. This is a 4–6 month project, not a 4–6 week project.** And that only covers the *infrastructure* — not the strategy-generating agent loop and validation layer, which are the parts that actually create value.

**2. Multi-market does not automatically give you breadth.** Without vol targeting and position sizing in risk units, you will have 4 adapters but still only 1 effective bet. **The Risk & Sizing layer matters more than the Adapter layer.**

**3. The real question is: do you need a platform, or do you need a strategy with edge?** [04-SCHOOL-B-EFFICACY](04-SCHOOL-B-EFFICACY.md) shows that professional CTA funds with 60+ markets just lost money for 3 consecutive years. A beautiful platform does not create edge — it only helps deploy edge if you have it.

> 💡 **Balanced recommendation:** build the architecture so it is **multi-market-ready from day one** (the 3 venue-agnostic layers above), but **only connect crypto in phase 1**. Once you have proven that a strategy survives DSR/PBO + OOS + dry-run, then open the next adapter. Adding another adapter to a correct architecture takes a few weeks — fixing a wrong architecture after 4 adapters already exist takes months.

---

## 7. Sources

- [NautilusTrader — Integrations (adapter list)](https://nautilustrader.io/docs/latest/integrations/)
- [NautilusTrader — Adapters (concepts)](https://nautilustrader.io/docs/latest/concepts/adapters/) · [Developer guide: writing adapters](https://nautilustrader.io/docs/nightly/developer_guide/adapters/)
- [NautilusTrader — Interactive Brokers integration](https://nautilustrader.io/docs/nightly/integrations/ib/) · [Databento](https://nautilustrader.io/docs/latest/integrations/databento/) · [Binance](https://nautilustrader.io/docs/latest/integrations/binance/)
- [SSI FastConnect — Introduction](https://guide.ssi.com.vn/ssi-products) · [API Specs](https://guide.ssi.com.vn/ssi-products/fastconnect-data/api-specs) · [FCData Specs v2.0 (PDF)](<https://www.ssi.com.vn/upload/files/KHCN/fast%20connect/FastConnectData_Specs_v2_0.pdf>)
- [vnstock — SSI FastConnect integration](https://docs.vnstock.site/integrate/ssi_fast_connect_api/)
- [AlgoTrade VN — Stock Trading API in Vietnam Market](https://hub.algotrade.vn/knowledge-hub/api-in-vietnam-stock-market/)

---

## One-sentence summary

> NautilusTrader + the IB adapter gives you crypto, forex, and international equities with almost no engineering effort; Vietnam requires a custom adapter and should only target VN30F1M. But **the Risk & Sizing layer matters more than the Adapter layer** — without vol targeting, 4 markets are still only 1 bet.
