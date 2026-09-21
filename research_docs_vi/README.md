# Crucible Quant — Tài liệu nghiên cứu

> *Crucible Quant — phòng nghiên cứu định lượng dùng AI để tiến hóa chiến lược giao dịch hệ thống, và bắt mọi chiến lược qua lửa thử trước khi được giao dịch thật.*

Mục tiêu dự án: xây hệ thống AI hỗ trợ phát triển chiến lược trading (agent-loop sinh strategy → backtest → optimize → validate), với strategy đầu ra **deterministic, không gọi LLM lúc runtime**.

---

## 🏗️ Blueprint triển khai

| File | Nội dung |
|---|---|
| **[Architecture_Design.md](Architecture_Design.md)** | **Thiết kế hệ thống.** Giả định, 6 nguyên tắc, đặc tả từng tầng, data model, tech stack, lộ trình 7 giai đoạn, rủi ro. **Đọc mục 0 và mục 10 trước** — có giả định cần bạn xác nhận |
| **[../implement_docs_vi/](../implement_docs_vi/README.md)** | **Cách xây dựng** (bản dịch tiếng Việt; bản gốc tiếng Anh ở [../implement_docs/](../implement_docs/README.md)): danh sách task kèm test nghiệm thu, quy ước code, bảng bất biến → test, sơ đồ module, ADR. Hướng dẫn cho coding agent: [../CLAUDE.md](../CLAUDE.md) |

## Tài liệu tổng hợp & phân tích

| File | Nội dung | Đọc khi nào |
|---|---|---|
| [00-TONG-HOP-NGHIEN-CUU.md](00-TONG-HOP-NGHIEN-CUU.md) | **Bắt đầu từ đây.** Tổng hợp 4 báo cáo gốc: kết luận hội tụ, kiến trúc 5 thành phần, stack khuyến nghị, bảng ngưỡng gate cứng | Trước khi làm bất cứ gì |
| [03-KHA-THI-CHO-CA-NHAN.md](03-KHA-THI-CHO-CA-NHAN.md) | Đánh giá khả thi 2 trường phái cho nhà đầu tư cá nhân: rào cản vốn, chi phí, dữ liệu, quy định | Khi chọn hướng đi |
| [04-TRUONG-PHAI-B-HIEU-QUA.md](04-TRUONG-PHAI-B-HIEU-QUA.md) | Bằng chứng về hiệu quả của rule-based TA (136 năm, 67 market) + ngành CTA + kết quả cá nhân | Khi đặt kỳ vọng Sharpe |
| [05-SMOOTH-FLOW.md](05-SMOOTH-FLOW.md) | Pipeline seam-free với data $0. 8 bước gate, ledger, validation. ⚠️ Khuyến nghị Freqtrade ở đây **chỉ đúng nếu làm crypto-only** | Khi bắt đầu code (crypto) |
| [06-PLATFORM-DA-THI-TRUONG.md](06-PLATFORM-DA-THI-TRUONG.md) | **Kiến trúc platform đa thị trường.** NautilusTrader + adapter cho crypto/forex/stock quốc tế/Việt Nam. Thứ tự xây, ước lượng công sức | Khi phạm vi vượt crypto |
| [07-VALIDATION-LAYER.md](07-VALIDATION-LAYER.md) | **Lớp quan trọng nhất.** Purging/embargo, CPCV, PBO, DSR, trial ledger, leaky oracle test. API `purgedcv` thật + thứ tự đặt gate | Trước khi tin bất kỳ backtest nào |
| [08-LLM-QUANT-RESEARCHER.md](08-LLM-QUANT-RESEARCHER.md) | **Toàn cảnh ngành (9/2026).** Khảo sát 22 paper/repo LLM sinh strategy. 5 kiến trúc phân theo *tri thức tích lũy ở đâu*, bằng chứng về khoảng trống validation, ablation backbone model, quan điểm + việc cần sửa | Khi cần biết ngành đang ở đâu, hoặc trước khi chốt kiến trúc agent |

## Báo cáo gốc (nguồn thô)

Bốn báo cáo research ban đầu — **nội dung gốc giữ nguyên**, mỗi file gắn một banner đính chính ở đầu. Chúng là *đầu vào* của [00](00-TONG-HOP-NGHIEN-CUU.md), giữ lại để truy vết chứ không phải để đọc trực tiếp.

| File | Chủ đề |
|---|---|
| [90-NGUON-KIEN-TRUC-VA-REPO.md](90-NGUON-KIEN-TRUC-VA-REPO.md) | **Kiến trúc & repo** — có project nào làm sẵn 5 thành phần chưa, lắp từ đâu |
| [91-NGUON-AI-AGENT-BACKTESTING.md](91-NGUON-AI-AGENT-BACKTESTING.md) | **AI Agents cho backtesting (9/2025–9/2026)** — audit, look-ahead bias, benchmark |
| [92-NGUON-THI-TRUONG-CHIEN-LUOC.md](92-NGUON-THI-TRUONG-CHIEN-LUOC.md) | **Thị trường & chiến lược** — crypto/forex/stocks + Việt Nam |
| [93-NGUON-AI-SINH-STRATEGY-MT5.md](93-NGUON-AI-SINH-STRATEGY-MT5.md) | **AI xây dựng strategy rule-based** — MT5, LLM sinh code |

> Trong các tài liệu khác, bốn file này được nhắc bằng số ngắn: `90`, `91`, `92`, `93`.

---

## Hai trường phái đang cân nhắc

| | **A — Cross-sectional factor + ML** | **B — Rule-based TA** |
|---|---|---|
| Output | Alpha factor + ML model | Rule entry/exit/SL/TP |
| Đơn vị quyết định | Cross-sectional ranking | Time-series per instrument |
| Cần universe | Rổ lớn (50–500 mã) | 1 → 50 instrument |
| Sharpe kỳ vọng | 1.0–1.7 | 0.4 (1 symbol) → 1.6 (50 market) |
| Vốn tối thiểu | ~$50k (equity) / $2–5k (crypto perp) | $1k |
| Export EA MQL5 | ❌ | ✅ |
| Paper tham chiếu | RD-Agent(Q), AlphaAgent, QuantaAlpha — xem [08](08-LLM-QUANT-RESEARCHER.md) | [04](04-TRUONG-PHAI-B-HIEU-QUA.md) |

---

## Ba điều quan trọng nhất rút ra

**1. Validation layer là khoảng trống của cả ngành.** Khảo sát 22 hệ ([08](08-LLM-QUANT-RESEARCHER.md)): **1/22 báo cáo DSR**, và survey độc lập cho thấy **0/19** nghiên cứu đạt mức tái lập cao nhất, **1/19** khai báo chi phí giao dịch. Đây là chỗ phải tự xây.
→ ⚠️ **Nhưng ngưỡng khó hơn ta tưởng:** hệ duy nhất báo cáo DSR (Alpha-R1, 9/2026) chỉ đạt **0.39–0.85 với 34–158 trial** — **trượt ngưỡng 0.95**. Kế hoạch của ta là 4.500–9.000 *lời gọi LLM* — tức hàng trăm đến hàng nghìn trial thống kê. Xem [08 mục 5.2](08-LLM-QUANT-RESEARCHER.md).

**2. DSR và PBO không đủ.** Một "leaky oracle" rò rỉ dữ liệu tương lai với Sharpe 34.7 **vẫn qua được DSR = 1.00**. Cần 3 lớp: structural guardrail (chống leakage) + statistical correction (chống selection bias) + temporal isolation (chống memorization).

**3. Breadth quan trọng hơn chất lượng rule.** Cùng một luật giao dịch: Sharpe 0.4 trên 1 market → 1.60 khi đa dạng hóa qua 50 market. Đừng tinh chỉnh rule khi chỉ chạy 1 symbol.

---

## Đính chính đã áp dụng (20/9/2026)

Các báo cáo gốc **giữ nguyên nội dung**, chỉ gắn banner đính chính ở đầu file.

- [x] **Ngưỡng DSR** — `DSR > 0` **sai**, DSR là xác suất 0–1. Ngưỡng đúng **DSR > 0.95**. Đã sửa trong [00](00-TONG-HOP-NGHIEN-CUU.md), [05](05-SMOOTH-FLOW.md); banner ở `90`, `91`
- [x] **Code QuantEvolve** — `tarsyang/quantevolve` **không phải** code của paper arXiv 2510.18569 (Gemini vs Qwen3, không MAP-Elites/Zipline, không trích dẫn paper). Đã thêm bảng đối chiếu vào [00](00-TONG-HOP-NGHIEN-CUU.md) mục 4; banner ở `93`
- [x] **IC/IR AlphaAgent** — không phải mâu thuẫn số học mà là câu hỏi về *effective breadth* (`IC 0.0056 × √125000 ≈ 1.98`, nên IR 1.05 vẫn nằm dưới trần của Fundamental Law)
- [x] **PDT rule** — đã bị xóa bỏ từ **4/6/2026**; dữ liệu PIT giờ **$29–49/tháng**. Banner ở `92`
- [x] **Sharpe 0.3–0.8** — đúng ở cấp danh mục; cấp market đơn lẻ là **~0.4** (Hurst-Ooi-Pedersen). Banner ở `92`

## Việc còn tồn đọng

- [ ] **Trả lời các quyết định 🔴 Mở trong [sổ quyết định](Architecture_Design.md) §10** — nguồn duy nhất về trạng thái quyết định (đã gộp 4 câu hỏi ở mục 9 của [00](00-TONG-HOP-NGHIEN-CUU.md)). Hiện chỉ còn **D4** — ngưỡng kinh tế để cấp vốn (chặn lần mở holdout đầu tiên + live). Đã chốt 21/9/2026: thị trường (D1), loại output (D2), **không cần EA MQL5 — live qua NautilusTrader** (D3). Các mặc định còn lại do người dùng tự cấu hình qua `config/user.yaml` (§10.1)
- [ ] Xác minh chữ ký `deflated_sharpe_ratio` và `probability_of_backtest_overfitting` trong `purgedcv` (xem [07](07-VALIDATION-LAYER.md) mục 7). ⚠️ Thư viện này đang là lõi validation nhưng API **chưa kiểm chứng** — là việc đầu tiên của GĐ 1, có phương án tự cài đặt dự phòng ([Architecture_Design.md](Architecture_Design.md) §6)
- [ ] Đọc code [`RndmVariableQ/AlphaAgent`](https://github.com/RndmVariableQ/AlphaAgent) — README có vẻ đã đi xa khỏi paper (Tushare/AgentScope/CSI 1000 vs Qlib/CSI 500+S&P 500)
- [x] **Áp 10 thay đổi ở [08 mục 8](08-LLM-QUANT-RESEARCHER.md) vào [Architecture_Design.md](Architecture_Design.md)** — xong 21/9/2026, bản 0.2. Quan trọng nhất: DSR tính trên **danh mục hợp nhất** (không phải từng cell), gate ⓪ drift detection, bảng định tuyến model dị thể thay cho "dùng model nhỏ", P6 nâng lên cấp OS
- [x] **Review thiết kế 8 phát hiện → [Architecture_Design.md](Architecture_Design.md) bản 0.3** — xong 21/9/2026. Holdout khóa theo đợt nghiên cứu + danh mục đóng băng; tách audit log khỏi trial thống kê (`N_eff`, `V[SR]`); đặc tả dựng danh mục; **đính chính PBO** (không có edge ⇒ PBO ≈ 0.5, không → 1; sửa cả [07](07-VALIDATION-LAYER.md)); **sửa lỗi sizing chia volatility hai lần**; chuẩn hóa gate drift; hạ kết luận reasoning-model/MadEvolve xuống giả thuyết; §10 thành sổ quyết định duy nhất
- [x] **Đọc repo MadEvolve → [Architecture_Design.md](Architecture_Design.md) bản 0.4** — xong 21/9/2026. Island model trong repo là code chết (bằng chứng hội tụ chỉ nằm trong paper). Đã đưa vào đặc tả: template vùng cố định + `EVOLVE-BLOCK` cưỡng chế bằng hash, khai báo `TUNABLE`, metric public/private, sandbox, patch strict, pipeline bất đồng bộ (§3.1.10, §3.3.1–3.3.3)
- [x] **Đọc toàn văn paper MadEvolve → [Architecture_Design.md](Architecture_Design.md) bản 0.5** — xong 21/9/2026. **Kiến trúc đa engine** (QuantEvolve + vòng đơn giản + random search, chế độ cô lập/cộng tác, §3.1.11); tắt optimizer tham số trong vòng tiến hóa; tiến hóa theo module; số lệnh tối thiểu; tương quan indicator; đường suy giảm IS→OOS trên CPCV; fill model bi quan; nhiều seed. Đánh giá phê phán paper ghi ở [08](08-LLM-QUANT-RESEARCHER.md) §5
- [ ] Đối chiếu repo ↔ paper cho [`QuantaAlpha`](https://github.com/QuantaAlpha/QuantaAlpha), [`Alpha-R1`](https://github.com/FinStep-AI/Alpha-R1), [`QuantEvolver`](https://github.com/QuantLLM/QuantEvolver) — bài học từ vụ `tarsyang/quantevolve`
