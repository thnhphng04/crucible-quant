# Smooth flow: data miễn phí → backtest → live

> Pipeline không có đường nối (seam-free), dữ liệu $0, cùng một file strategy chạy xuyên suốt.
> Ngày: 2026-09-20 · Liên quan: [[00-TONG-HOP-NGHIEN-CUU]] · [[04-TRUONG-PHAI-B-HIEU-QUA]]

---

## 0. Vấn đề cần giải: "đường nối"

Setup điển hình của hầu hết người mới:

```
pandas/Jupyter          vectorbt            ccxt script
(nghịch dữ liệu)  →   (backtest)    →   (bot live)
     ↑                    ↑                   ↑
  viết lại            viết lại            viết lại
```

**Mỗi mũi tên là một lần viết lại logic — và đó là nơi bug chui vào.** Strategy backtest ra Sharpe 1.5 rồi live lỗ, phần lớn không phải vì overfit mà vì **code live khác code backtest**.

**Smooth flow = xóa hết mũi tên đó.** Một file strategy duy nhất chạy qua mọi giai đoạn.

---

## 1. Chọn nền tảng: hai ứng viên xóa được seam

|                                     | **Freqtrade**           | **NautilusTrader**                              |
| ----------------------------------- | ----------------------------- | ----------------------------------------------------- |
| Cùng file strategy backtest→live  | ✅                            | ✅                                                    |
| Dữ liệu free                      | ✅ ccxt                       | ✅ ccxt/adapter                                       |
| Download data built-in              | ✅`download-data`           | 🟡 tự viết                                          |
| Backtest built-in                   | ✅                            | ✅                                                    |
| **Optimizer built-in**        | ✅**hyperopt (Optuna)** | ❌ tự lắp                                           |
| Dry-run (paper)                     | ✅                            | ✅                                                    |
| Live                                | ✅ 14+ sàn                   | ✅ Binance, Bybit, IB, Databento, Betfair, Polymarket |
| Monitoring UI                       | ✅ web UI                     | 🟡                                                    |
| **Multi-asset ngoài crypto** | ❌**chỉ crypto**       | ✅**futures, equities qua IB/Databento**        |
| Microstructure thật                | 🟡                            | ✅ order book, queue position, partial fill           |
| Độ dốc học                      | 🟢 thấp                      | 🔴 cao                                                |

**Cam kết kỹ thuật của mỗi bên:**

- **Freqtrade:** *"The backtesting engine feeds historical OHLCV data into the same `populate_*` hooks; the live freqtrade trade process pulls real-time candles via ccxt and triggers the same hooks."*
- **NautilusTrader:** *"The DataEngine guarantees 100% identical data handling in both backtesting and live trading"* — deploy *"with no code changes"*.

### 🎯 Khuyến nghị: bắt đầu bằng Freqtrade

Ba lý do, lý do thứ ba là quan trọng nhất:

1. **Batteries included** — download-data, backtest, hyperopt, dry-run, live, UI: một CLI, không cần lắp gì
2. **Hyperopt đã dùng Optuna** — đúng optimizer mà [[00-TONG-HOP-NGHIEN-CUU]] khuyến nghị, không phải cài thêm
3. 🔑 **Cấu trúc gò bó của nó là LỢI THẾ cho agent sinh code.** Strategy Freqtrade luôn có đúng 3 hook: `populate_indicators` / `populate_entry_trend` / `populate_exit_trend`. Đây là **template cố định để LLM điền vào chỗ trống** — thay vì để LLM tự do sáng tác cấu trúc file (nguồn bug vô tận).

**Khi nào chuyển sang NautilusTrader:** khi bạn cần vượt ra ngoài crypto để lấy breadth thật (futures đa tài sản — xem [[04-TRUONG-PHAI-B-HIEU-QUA]] mục 5), hoặc khi cần mô phỏng order book.

---

## 2. Dữ liệu: tại sao $0 là đủ

```bash
freqtrade download-data \
  --exchange binance \
  --pairs BTC/USDT ETH/USDT SOL/USDT ... \
  --timeframes 1h 4h 1d \
  --timerange 20200101-
```

- **Không cần API key** — klines lịch sử của Binance là public endpoint
- Lưu thành file cục bộ, dùng lại vô hạn cho backtest và hyperopt
- Cùng một `ccxt` sẽ kéo nến real-time lúc live → **không có mismatch nguồn dữ liệu**

> ⚠️ **Dữ liệu free không có nghĩa là dữ liệu đủ.** Backtest funding-rate carry cần 4 luồng (OHLCV + funding 8h + open interest + liquidation), Freqtrade chỉ cho bạn OHLCV. Nếu strategy của bạn động tới funding, phải tự kéo thêm bằng ccxt.

---

## 3. Flow đầy đủ — 8 bước, một file strategy

```
┌─ [1] DOWNLOAD ────────────────────────────────────────────┐
│  freqtrade download-data --timerange 20200101-            │
│  → dữ liệu $0, dùng lại mãi                               │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [2] AGENT SINH STRATEGY ─────────────────────────────────┐
│  LLM điền vào template 3 hook                             │
│  → user_data/strategies/GenStrat_007.py                   │
│  🔒 Gate: AST similarity vs strategy zoo (từ AlphaAgent)  │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [3] BACKTEST IN-SAMPLE ──────────────────────────────────┐
│  freqtrade backtesting --strategy GenStrat_007 \          │
│    --timerange 20200101-20240101                          │
│  🔒 Gate: edge còn sống khi siết phí? Không → loại        │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [4] HYPEROPT (Optuna) ───────────────────────────────────┐
│  freqtrade hyperopt --strategy GenStrat_007 \             │
│    --epochs 200 --hyperopt-loss SharpeHyperOptLoss        │
│  📝 GHI TOÀN BỘ 200 trial vào ledger                      │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [5] VALIDATION LAYER ⚠️ BẠN PHẢI TỰ XÂY ─────────────────┐
│  export returns → purgedcv                                │
│  → DSR (deflate theo TỔNG trial thực tế)                  │
│  → PBO qua CPCV                                           │
│  🔒 Gate: PBO < 0.5 VÀ DSR > 0.95 → mới được đi tiếp      │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [6] OOS HOLDOUT ĐÓNG BĂNG ───────────────────────────────┐
│  freqtrade backtesting --timerange 20240101-20260101      │
│  🔒 Chạy ĐÚNG MỘT LẦN. Nhìn rồi là cháy.                  │
│  🔒 Gate: Sharpe OOS ≥ 50% Sharpe IS                      │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [7] DRY-RUN (paper) ─────────────────────────────────────┐
│  freqtrade trade --dry-run                                │
│  🔒 Gate: 4–6 tuần tối thiểu, spread/phí thật             │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [8] LIVE ────────────────────────────────────────────────┐
│  freqtrade trade                                          │
│  → bắt đầu bằng size nhỏ nhất có thể                      │
└───────────────────────────────────────────────────────────┘
```

**Bước 2 → 8: CÙNG MỘT FILE `GenStrat_007.py`.** Không viết lại dòng nào.

---

## 4. ⚠️ Hai chỗ Freqtrade KHÔNG lo cho bạn

Đây là phần quan trọng nhất của tài liệu này. Freqtrade làm cho flow smooth, **nhưng nó không có lớp chống overfitting** — đúng như kết luận mục 1.2 [[00-TONG-HOP-NGHIEN-CUU]] về cả ngành.

### 4.1. 🔴 Hyperopt là cỗ máy sinh overfit

`--epochs 200` = **200 trial**. Chọn epoch tốt nhất trong 200 lần thử mà không deflate chính là cái bẫy mục 1.5 file tổng hợp mô tả.

Và nếu agent chạy 50 strategy × 200 epoch = **10.000 trial thực tế**, không phải 200.

**Bắt buộc:** ledger phải đếm **tổng trial tích lũy toàn hệ thống**, không phải per-run.

```python
# Pseudo — bước 5
returns = load_freqtrade_trades(backtest_result)
n_trials = ledger.total_trials()        # TÍCH LŨY, không reset

dsr = deflated_sharpe_ratio(returns, n_trials=n_trials)
pbo = probability_of_backtest_overfitting(is_matrix, oos_matrix)

if pbo >= 0.5 or dsr <= 0.95:    # DSR là xác suất 0–1, không phải giá trị Sharpe
    ledger.record(strategy, verdict="REJECT")
    return  # KHÔNG tinh chỉnh, KHÔNG thử lại
```

### 4.2. 🔴 Không có structural guardrail chống look-ahead

Freqtrade có cảnh báo lookahead cơ bản, nhưng LLM vẫn sinh được code nhìn tương lai (dùng nến chưa đóng, indicator repaint).

Và nhớ bài học arXiv 2608.27734: **DSR/PBO KHÔNG bắt được leakage** — một leaky oracle Sharpe 34.7 vẫn qua DSR = 1.00. Cần guardrail cấu trúc riêng:

- Chỉ cho agent dùng whitelist indicator đã kiểm tra không repaint
- Reject code chứa `.shift(-n)`, truy cập index tương lai
- Cài sẵn một **leaky oracle giả** vào harness — nếu pipeline không từ chối nó, pipeline hỏng

---

## 5. Cấu trúc thư mục đề xuất

```
TradingProject/
├── research_docs/                ← tài liệu nghiên cứu
├── user_data/
│   ├── data/binance/              ← dữ liệu tải về ($0)
│   ├── strategies/
│   │   ├── _template.py           ← template 3 hook cho LLM điền
│   │   ├── _zoo/                  ← strategy zoo để đo AST similarity
│   │   └── GenStrat_*.py          ← agent sinh ra
│   └── hyperopt_results/
├── pipeline/
│   ├── agent_loop.py              ← LangGraph: sinh → backtest → đọc → lặp
│   ├── ledger.py                  ← SQLite: MỌI trial
│   ├── validation.py              ← purgedcv: DSR/PBO/CPCV
│   ├── guardrail.py               ← AST similarity + lookahead check
│   └── leaky_oracle.py            ← strategy giả để test harness
└── config.json                    ← Freqtrade config
```

---

## 6. Lộ trình

**Tuần 1 — Dựng harness TRƯỚC, agent SAU**

Đây là thứ tự [[00-TONG-HOP-NGHIEN-CUU]] mục 7 nhấn mạnh và hay bị làm ngược.

```bash
pip install freqtrade purgedcv
freqtrade create-userdir --userdir user_data
freqtrade download-data --exchange binance \
  --pairs BTC/USDT ETH/USDT SOL/USDT BNB/USDT XRP/USDT \
  --timeframes 1h --timerange 20200101-
```

Viết tay **1 strategy đơn giản** (EMA crossover) → chạy hết 8 bước bằng tay một lần.
✅ *Gate tuần 1:* harness **từ chối được `leaky_oracle.py`** mà bạn cố tình cài vào.

**Tuần 2–3 — Ledger + validation layer**
Dựng `ledger.py` + `validation.py`. Chạy lại strategy tuần 1 qua DSR/PBO.
✅ *Gate:* mọi backtest đều được ghi vào ledger, không có đường vòng.

**Tuần 4–6 — Agent loop**
LangGraph: LLM điền template → chạy CLI Freqtrade → parse kết quả → lặp.
✅ *Gate:* ≥50 iteration tự động, mỗi cái qua đủ gate.

**Tuần 7+ — Mở breadth**
Tăng whitelist lên 15–30 cặp **ít tương quan**.
⚠️ Nhớ: 30 altcoin ≈ 1 bet (đều theo BTC). Trộn thêm cặp ít tương quan hơn.

**Sau đó — vượt khỏi crypto**
Muốn lấy breadth thật (Sharpe 0.4 → 1.6) phải sang futures đa tài sản → **migrate sang NautilusTrader** + Norgate ($270/năm).

---

## 7. Đánh đổi phải biết trước

| Được                       | Mất                                                         |
| ----------------------------- | ------------------------------------------------------------ |
| $0 dữ liệu                  | Chỉ OHLCV — không funding/OI/liquidation                  |
| Smooth, không seam           | Khóa vào cấu trúc Freqtrade                              |
| Hyperopt sẵn (Optuna)        | **Hyperopt là máy sinh overfit nếu không deflate** |
| Template tốt cho LLM codegen | Khó diễn đạt logic quá phức tạp                       |
| Live ngay 14+ sàn            | **Crypto only** → trần breadth thấp                 |
| Nhanh tới end-to-end         | Microstructure mô phỏng thô hơn Nautilus                 |

**Điểm đau lớn nhất:** crypto-only nghĩa là trần Sharpe của bạn bị giới hạn bởi việc mọi coin đều tương quan với BTC. [[04-TRUONG-PHAI-B-HIEU-QUA]] cho thấy breadth là biến quyết định — và crypto cho breadth *danh nghĩa* cao nhưng *hiệu dụng* thấp.

→ Coi Freqtrade là **bệ phóng để dựng và debug agent-loop với chi phí $0**, không phải đích đến.

---

## 8. Nguồn

- [Freqtrade — Backtesting](https://www.freqtrade.io/en/stable/backtesting/) · [Hyperopt](https://www.freqtrade.io/en/stable/hyperopt/) · [Strategy Customization](https://www.freqtrade.io/en/stable/strategy-customization/) · [Bot Basics](https://www.freqtrade.io/en/stable/bot-basics/) · [Utility Sub-commands](https://www.freqtrade.io/en/stable/utils/)
- [NautilusTrader — Live Trading](https://nautilustrader.io/docs/latest/concepts/live/) · [Adapters](https://nautilustrader.io/docs/latest/concepts/adapters/) · [Bybit integration](https://nautilustrader.io/docs/nightly/integrations/bybit/)
- [Freqtrade 2026 — Installation &amp; Usage Tutorial](https://addrom.com/freqtrade-2026-complete-installation-guide-and-practical-usage-tutorial-for-the-leading-open-source-cryptocurrency-trading-bot/)

---

## Một câu tóm tắt

> Freqtrade cho bạn flow không seam với $0 dữ liệu — nhưng nó **không có lớp chống overfitting**, và `--epochs 200` là cỗ máy sinh overfit nếu bạn không đếm trial. Phần Freqtrade lo là phần dễ; phần bạn phải tự xây (ledger + DSR/PBO + guardrail) mới là phần quyết định.
