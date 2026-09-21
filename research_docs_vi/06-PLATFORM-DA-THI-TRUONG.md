# Platform đa thị trường: crypto + forex + stock (quốc tế & Việt Nam)

> Kiến trúc cho một platform duy nhất chạy được trên 4 nhóm thị trường.
> Ngày: 2026-09-20 · Liên quan: [[05-SMOOTH-FLOW]] · [[03-KHA-THI-CHO-CA-NHAN]] · [[04-TRUONG-PHAI-B-HIEU-QUA]]

---

## 0. Đính chính với [[05-SMOOTH-FLOW]]

Tài liệu 05 khuyến nghị **Freqtrade** — nhưng Freqtrade **chỉ chạy crypto**. Với phạm vi mới (crypto + forex + stock quốc tế + Việt Nam), Freqtrade **không còn là lựa chọn đúng**.

Vẫn giữ giá trị từ 05: nguyên tắc seam-free, 8 bước gate, ledger + DSR/PBO. Chỉ thay engine.

---

## 1. Kết luận: NautilusTrader là core, nhưng Việt Nam phải tự viết

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
  │  OKX     │  │          │ │         │ │          │ │ ⚠️ TỰ VIẾT  │
  ├──────────┤  ├──────────┤ ├─────────┤ ├──────────┤ ├─────────────┤
  │ Crypto   │  │ Crypto   │ │ Forex   │ │ Futures  │ │ HOSE/HNX    │
  │ spot+perp│  │ perp     │ │ Stocks  │ │ data     │ │ VN30F1M     │
  │          │  │          │ │ Futures │ │ chất cao │ │             │
  │    ✅    │  │    ✅    │ │   ✅    │ │    ✅    │ │     🔴      │
  └──────────┘  └──────────┘ └─────────┘ └──────────┘ └─────────────┘
       CÓ SẴN         CÓ SẴN      CÓ SẴN      CÓ SẴN     PHẢI XÂY
```

**Adapter chính thức có sẵn:** Binance, Bybit, OKX, Coinbase International, **Interactive Brokers**, Databento, Betfair, Polymarket.

> 🔑 **Interactive Brokers là mảnh ghép quan trọng nhất** — một adapter duy nhất cho bạn **forex + cổ phiếu quốc tế + futures toàn cầu**. Ba trong bốn nhóm thị trường bạn cần, đã có sẵn, không phải viết dòng nào.

**Chỉ còn Việt Nam là khoảng trống.**

---

## 2. Mảng Việt Nam — phải tự xây adapter

### 2.1. Nguồn có sẵn

| Nhà cung cấp            | Năng lực                                                                                                                                                                                         |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **SSI FastConnect** | Chia 2 phần:**FC Data** (realtime cả cash lẫn phái sinh) + **FC Trading** (query tài khoản, đặt/sửa/hủy lệnh, order status qua streaming). Có sample code **Python** |
| **DNSE**            | Tập trung mạnh vào hỗ trợ algo trading qua API, hướng tới trader khối lượng lớn                                                                                                        |
| **vnstock**         | Thư viện Python cho dữ liệu, có tài liệu tích hợp SSI FastConnect                                                                                                                         |

### 2.2. Phải viết gì

NautilusTrader yêu cầu 3 thành phần cho một adapter mới:

| Thành phần                 | Nhiệm vụ                                                             |
| ---------------------------- | ---------------------------------------------------------------------- |
| **InstrumentProvider** | Parse response API của sàn thành`Instrument` object của Nautilus |
| **DataClient**         | Kết nối, subscribe instrument, phát market data vào platform       |
| **ExecutionClient**    | Submit order, nhận fill,**reconcile state khi reconnect**       |

Cách test theo khuyến nghị chính thức: *"exercise the adapter's Python surface inside integration tests, mocking the PyO3 boundary with stubbed Rust clients"*.

**Ước lượng công sức:** 3–6 tuần cho một người đã quen NautilusTrader. Phần khó nhất là **ExecutionClient reconciliation** — xử lý đúng khi mất kết nối rồi nối lại mà vẫn có vị thế đang mở.

### 2.3. 🔴 Nhưng trước khi viết — đọc kỹ phần này

Từ [[03-KHA-THI-CHO-CA-NHAN]] và báo cáo gốc `92`, thị trường VN có **3 đặc tính phá vỡ backtest realism**:

| Đặc tính                       | Hệ quả với platform                                                                                                                                                                                                                          |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Price limit ±7% (HOSE)** | Khi chạm trần/sàn → lệnh chất đống**không có đối ứng** ("lock"). Backtest sẽ báo bạn khớp lệnh, thực tế không khớp. **Đây là lỗi mô phỏng nghiêm trọng nhất, không tồn tại ở thị trường Mỹ** |
| **T+2 cổ phiếu**          | Không quay vòng trong ngày được. Mọi strategy intraday trên cổ phiếu VN là**không khả thi**                                                                                                                                  |
| **Chưa có short-selling** | Long-only. Mất một nửa alpha của strategy long-short                                                                                                                                                                                        |

> 🎯 **Hệ quả thiết kế:** với Việt Nam, **VN30F1M (futures) là mục tiêu thực tế duy nhất** cho systematic trading — nó **T+0**, short được qua vị thế futures, và không bị lock kiểu cổ phiếu.
>
> → Adapter VN giai đoạn 1 nên **chỉ hỗ trợ phái sinh**, bỏ qua cash market. Giảm khoảng 60% công sức và tránh hết 3 cái bẫy trên.

---

## 3. Vấn đề khó nhất không phải adapter — mà là chuẩn hóa

Có adapter rồi vẫn chưa xong. Bốn nhóm thị trường khác nhau về **mọi thứ**:

### 3.1. Lịch giao dịch

| Thị trường  | Session                                   |
| -------------- | ----------------------------------------- |
| Crypto         | 24/7, không nghỉ                        |
| Forex          | 24/5                                      |
| US stocks      | 9:30–16:00 ET                            |
| **HOSE** | 9:00–15:00 ICT,**có nghỉ trưa** |

Căn bar sai giữa các session → **sinh look-ahead mà không hề hay biết**. Đây là lỗi âm thầm và chết người.

### 3.2. Đơn vị vị thế

1 BTC ≠ 1 lot EURUSD ≠ 100 cổ phiếu HOSE ≠ 1 hợp đồng VN30F1M (multiplier 100.000 VND/điểm).

> 🔑 **Nguyên tắc thiết kế cốt lõi: strategy KHÔNG BAO GIỜ nói bằng lot/share/contract. Strategy chỉ nói bằng đơn vị RỦI RO.**
>
> ```python
> # ❌ SAI — gắn chặt vào venue
> buy(size=0.5)  # 0.5 cái gì?
>
> # ✅ ĐÚNG — venue-agnostic
> buy(risk_pct=0.5, stop_distance=2*atr)
> # → lớp Position Sizer quy đổi sang lot/share/contract theo từng venue
> ```
>
> ⚠️ *Cập nhật 21/9/2026:* ví dụ này nếu kết hợp với vol targeting ở §3.3 sẽ **chia volatility hai lần** (stop = k×ATR và vol scalar cùng co theo biến động). Hợp đồng đã sửa trong [Architecture_Design.md](Architecture_Design.md) §3.3–3.4: `Signal(strength, stop_distance)` — vol targeting là nơi **duy nhất** volatility đi vào size, stop chỉ là trần rủi ro (`min`, không nhân).

Đây là lớp trừu tượng **bắt buộc** nếu muốn cùng một strategy chạy trên 4 thị trường.

### 3.3. Vol scaling — thứ quyết định bạn có hưởng được breadth không

[[04-TRUONG-PHAI-B-HIEU-QUA]] cho thấy Sharpe đi từ 0.4 → 1.60 nhờ breadth. Nhưng **chỉ khi mọi vị thế được scale về cùng mục tiêu volatility** (Hurst-Ooi-Pedersen dùng 10%/năm).

Không làm thế thì BTC (vol ~60%/năm) sẽ nuốt toàn bộ rủi ro danh mục, và VN30F1M (vol ~20%) coi như không tồn tại → **bạn có 4 thị trường nhưng vẫn chỉ có 1 bet**.

### 3.4. Tiền tệ

USD (crypto, US stocks) · USD/EUR/JPY (forex) · **VND** (Việt Nam). Cần lớp quy đổi và hạch toán PnL thống nhất.

---

## 4. Kiến trúc đề xuất

```
┌──────────────────────────────────────────────────────────┐
│ TẦNG STRATEGY  — venue-agnostic, nói bằng đơn vị rủi ro  │
│ • Agent sinh code tại đây                                │
│ • Chỉ dùng: return, ATR, z-score (KHÔNG giá tuyệt đối)   │
└───────────────────────┬──────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│ TẦNG RISK & SIZING                                       │
│ • Vol targeting → scale mọi market về 10%/năm            │
│ • strength + stop_distance → số lot/share/contract       │
│ • Quy đổi tiền tệ, hạch toán PnL chung                   │
└───────────────────────┬──────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│ NAUTILUS CORE — backtest ≡ live, cùng một code           │
└───────────────────────┬──────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│ TẦNG ADAPTER                                             │
│ Binance/Bybit ✅ │ IB ✅ │ Databento ✅ │ SSI 🔴 tự viết │
└──────────────────────────────────────────────────────────┘

        ┌─────────────────────────────────────────┐
        │ SONG SONG — không phụ thuộc venue       │
        │ • Trial ledger (SQLite/Postgres)        │
        │ • Validation: DSR/PBO/CPCV (purgedcv)   │
        │ • Guardrail: AST similarity, lookahead  │
        │ • Leaky oracle test                     │
        └─────────────────────────────────────────┘
```

Ba tầng trên cùng **không biết gì về venue**. Đó là điều kiện để platform thật sự đa thị trường.

---

## 5. Thứ tự xây — quan trọng hơn kiến trúc

⚠️ **Đừng xây cả 4 thị trường cùng lúc.** Xây theo thứ tự tăng dần độ khó, mỗi bước validate xong mới đi tiếp.

| GĐ         | Thị trường                     | Adapter             | Vì sao thứ tự này                                                                                                                  | Ước lượng |
| ----------- | --------------------------------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| **1** | **Crypto**                  | Binance ✅ có sẵn | Dữ liệu free, 24/7 (không lo session), không lo roll, adapter sẵn.**Debug toàn bộ kiến trúc ở đây với chi phí $0** | 4–6 tuần    |
| **2** | **Forex + stock quốc tế** | IB ✅ có sẵn      | Một adapter cho cả hai. Gặp bài toán session/calendar lần đầu                                                                  | 3–4 tuần    |
| **3** | **Futures đa tài sản**   | Databento / IB ✅   | Đây mới là chỗ lấy breadth thật (Sharpe → 1.6). Gặp bài toán continuous contract roll                                       | 3–4 tuần    |
| **4** | **Việt Nam (VN30F1M)**     | SSI 🔴 tự viết    | Khó nhất, giá trị marginal thấp nhất.**Để sau cùng**                                                                    | 3–6 tuần    |

**Tổng thực tế: 4–6 tháng** nếu làm nghiêm túc, chưa kể validation layer và agent-loop.

### Vì sao Việt Nam để cuối

Không phải vì nó không quan trọng, mà vì:

1. Là adapter **duy nhất phải tự viết** → rủi ro kỹ thuật cao nhất
2. Chỉ VN30F1M dùng được → **1 instrument**, đóng góp breadth gần như bằng 0
3. Cho tới khi có short-selling và T+0 (lộ trình 2026–2028) thì tiềm năng còn bị chặn
4. Nhưng: **nếu bạn có edge thông tin về thị trường VN**, đây lại là lợi thế mà người nước ngoài không có. Đó là lý do chính đáng duy nhất để ưu tiên nó sớm hơn

---

## 6. Trả lời thẳng: có nên làm không?

**Có, kiến trúc khả thi.** NautilusTrader được thiết kế đúng cho multi-venue, và 3/4 thị trường đã có adapter sẵn.

**Nhưng ba điều cần thành thật với bản thân:**

**1. Đây là dự án 4–6 tháng, không phải 4–6 tuần.** Và đó là phần *hạ tầng* — chưa tính agent-loop sinh strategy và validation layer, vốn mới là phần tạo ra giá trị.

**2. Đa thị trường không tự động cho bạn breadth.** Nếu không có vol targeting và position sizing theo đơn vị rủi ro, bạn sẽ có 4 adapter nhưng vẫn chỉ 1 bet hiệu dụng. **Tầng Risk & Sizing quan trọng hơn tầng Adapter.**

**3. Câu hỏi thật là: bạn cần platform, hay cần một strategy có edge?** [[04-TRUONG-PHAI-B-HIEU-QUA]] cho thấy quỹ CTA chuyên nghiệp với 60+ market vừa lỗ 3 năm liên tiếp. Platform đẹp không tạo ra edge — nó chỉ giúp bạn triển khai edge nếu có.

> 💡 **Đề xuất cân bằng:** xây kiến trúc **sẵn sàng cho đa thị trường ngay từ đầu** (3 tầng trên venue-agnostic), nhưng **chỉ kết nối crypto ở giai đoạn 1**. Khi đã chứng minh được có strategy sống sót qua DSR/PBO + OOS + dry-run, lúc đó mới mở adapter tiếp. Thêm một adapter vào kiến trúc đã đúng chỉ mất vài tuần — nhưng sửa kiến trúc sai sau khi đã có 4 adapter thì mất vài tháng.

---

## 7. Nguồn

- [NautilusTrader — Integrations (danh sách adapter)](https://nautilustrader.io/docs/latest/integrations/)
- [NautilusTrader — Adapters (concepts)](https://nautilustrader.io/docs/latest/concepts/adapters/) · [Developer guide: viết adapter](https://nautilustrader.io/docs/nightly/developer_guide/adapters/)
- [NautilusTrader — Interactive Brokers integration](https://nautilustrader.io/docs/nightly/integrations/ib/) · [Databento](https://nautilustrader.io/docs/latest/integrations/databento/) · [Binance](https://nautilustrader.io/docs/latest/integrations/binance/)
- [SSI FastConnect — Introduction](https://guide.ssi.com.vn/ssi-products) · [API Specs](https://guide.ssi.com.vn/ssi-products/fastconnect-data/api-specs) · [FCData Specs v2.0 (PDF)](<https://www.ssi.com.vn/upload/files/KHCN/fast%20connect/FastConnectData_Specs_v2_0.pdf>)
- [vnstock — tích hợp SSI FastConnect](https://docs.vnstock.site/integrate/ssi_fast_connect_api/)
- [AlgoTrade VN — Stock Trading API in Vietnam Market](https://hub.algotrade.vn/knowledge-hub/api-in-vietnam-stock-market/)

---

## Một câu tóm tắt

> NautilusTrader + IB adapter cho bạn crypto, forex và cổ phiếu quốc tế gần như miễn phí công sức; Việt Nam phải tự viết adapter và chỉ nên làm VN30F1M. Nhưng **tầng Risk & Sizing quan trọng hơn tầng Adapter** — không có vol targeting thì 4 thị trường vẫn chỉ là 1 bet.
