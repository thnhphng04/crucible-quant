# 08 — LLM làm Quant Researcher: toàn cảnh kiến trúc

> **Khảo sát 22 paper/repo, cập nhật 20/9/2026.**
> Phạm vi: các hệ thống trong đó **LLM sinh ra artifact** (factor, biểu thức, hoặc code strategy) rồi artifact đó được **backtest đánh giá**. Không bao gồm các hệ gọi LLM lúc ra quyết định giao dịch.

---

## 0. TL;DR

1. **Kiến trúc đã hội tụ, còn validation thì không.** Trong 18 tháng, cả ngành hội tụ về 5 kiểu kiến trúc. Chúng khác nhau ở đúng một câu hỏi: *tri thức tích lũy ở đâu*. Nhưng **0/19** nghiên cứu đạt mức tái lập cao nhất, **1/19** khai báo mô hình chi phí giao dịch.
2. **Chỉ 1 hệ duy nhất báo cáo DSR — và nó trượt.** Alpha-R1 (9/2026) trung thực công bố DSR **0.39–0.85**, dưới ngưỡng 0.95, với chỉ **34–158 trial**. Dự án của bạn dự kiến chạy 4.500–9.000 lời gọi LLM — tức hàng trăm đến hàng nghìn trial thống kê.
3. **Con số Sharpe 5.6–8.8 trong vài paper 2026 không nên được tin.** Xem mục 5.4 — có paper mà baseline đơn giản còn Sharpe *cao hơn* hệ đề xuất.
4. **Gần như toàn bộ literature là trường phái A.** Chỉ 2/22 hệ sinh strategy thực thi được có entry/exit (AlgoEvolve, MadEvolve). Bạn đang đi vào vùng ít tiền lệ.
5. **Dữ liệu mới đảo ngược trực giác về model size:** model reasoning (o3) **tệ nhất** trong 6 backbone được test; Llama3-8B cho **RankIC âm**. Kích thước không phải trục đúng.
6. **Thiết kế validation đáng tin nhất không phải là công thức thống kê, mà là *ai kiểm soát evaluator*.** AgonAlpha đẩy việc chấm điểm ra một bên thứ ba mà tác giả không đụng được.

---

## 1. Phạm vi và trục phân loại

### 1.1 Tiêu chí vào / ra

|                  |                                                                                     |
| ---------------- | ----------------------------------------------------------------------------------- |
| ✅**Vào** | LLM sinh factor/biểu thức/code → backtest chấm điểm → vòng lặp cải thiện |
| ❌**Ra**   | LLM đọc tin tức/biểu đồ rồi**quyết định mua bán trực tiếp**      |

Nhóm bị loại gồm những repo nổi tiếng nhất về số sao: **TradingAgents** (~80k★), **AI Hedge Fund** (~59k★), **Vibe-Trading** (~31.5k★, HKU Data Intelligence Lab, 4/2026), **FinMem**, **FinAgent**, **FinCon**, **FinRL**.

Lý do loại không phải vì chúng kém, mà vì chúng vi phạm **giả định A4** của dự án bạn (*LLM không bao giờ chạy lúc runtime*). Chúng cũng mang một rủi ro cấu trúc: mọi quyết định phụ thuộc vào một model có thể bị đổi hoặc khai tử bởi nhà cung cấp, và không thể backtest trung thực vì model hôm nay đã biết chuyện của 2023.

> ⚠️ **Đừng để số sao đánh lừa.** 80k★ của TradingAgents phản ánh độ hấp dẫn của ý tưởng "mô phỏng một quỹ đầu tư bằng LLM", không phản ánh lợi nhuận. Các repo đi kèm paper nghiêm túc trong báo cáo này thường chỉ có vài trăm sao.

### 1.2 Trục phân loại: tri thức tích lũy ở đâu?

Đây là câu hỏi phân biệt tốt hơn mọi cách phân loại khác (theo số agent, theo output, theo thị trường). Sau 200 vòng lặp, **cái gì trong hệ đã khá lên?**

```
K1  Human-in-the-loop   →  trong đầu con người
K2  Vòng lặp R&D        →  trong prompt / memory store
K3  Tìm kiếm trên cây   →  trong thống kê node của cây
K4  Tiến hóa quần thể   →  trong archive / population
K5  Tinh chỉnh trọng số →  trong tham số của model
```

Mỗi bậc đẩy tri thức xuống sâu hơn một tầng — và tốn hơn một bậc. Đây cũng đúng là trình tự thời gian ngành đã đi qua: 2023 → 2026.

---

## 2. Năm kiến trúc

### Bảng tổng quan

|                             | **K1** Interactive | **K2** Vòng lặp R&D                | **K3** Cây (MCTS)       | **K4** Quần thể                       | **K5** Trọng số          |
| --------------------------- | ------------------------ | ------------------------------------------ | ------------------------------ | --------------------------------------------- | -------------------------------- |
| Tri thức nằm ở           | Con người              | Prompt/memory                              | Node cây                      | Archive                                       | Tham số model                   |
| Ứng viên sống cùng lúc | 1                        | 1                                          | 1 nhánh                       | Hàng trăm                                   | 1 (model)                        |
| Cơ chế đa dạng          | Con người              | ❌ hoặc yếu                              | UCB exploration                | ✅ MAP-Elites + island                        | ✅ diversity reward              |
| Chi phí                    | Rất thấp               | Thấp                                      | Trung bình                    | **Cao** (~10×)                         | **Rất cao** (GPU cluster) |
| Điểm yếu chính          | Không scale             | **Hội tụ sớm**, context explosion | Cây phình, vẫn một hướng | Đắt, phức tạp                             | Không khả thi cho cá nhân    |
| Đại diện                 | Alpha-GPT                | RD-Agent(Q), AlphaAgent, XALPHA            | Alpha Jungle, AgonAlpha        | **QuantEvolve**, MadEvolve, QuantaAlpha | Alpha-R1, QuantEvolver           |
| Xuất hiện                 | 2023                     | 2025                                       | 2025–26                       | 2025–26                                      | **2026**                   |

---

### K1 — Human-in-the-loop (Alpha-GPT, 7/2023)

LLM đóng vai **phiên dịch**: người dùng nói "tôi nghĩ cổ phiếu có volume tăng đột biến sau chuỗi giảm sẽ hồi", LLM dịch thành biểu thức toán học, backtest chạy, người xem kết quả rồi chỉnh ý tưởng.

Luận điểm gốc của Alpha-GPT: **không nên tự động hóa hoàn toàn**. Con người giữ phần trực giác kinh tế, AI giữ phần kiểm định có kỷ luật.

> **Tại sao vẫn đáng nhắc năm 2026:** đây là kiến trúc duy nhất mà số lượng trial bị giới hạn tự nhiên bởi sức người. Mọi kiến trúc sau đều gỡ bỏ giới hạn đó — và đó chính là nguồn gốc của toàn bộ vấn đề selection bias ở mục 5.

---

### K2 — Vòng lặp R&D tuyến tính

**Thành viên:** RD-Agent(Q), AlphaAgent, Chain-of-Alpha, AlphaMemo, XALPHA, Crypto Constrained Agents.

Một chuỗi: `giả thuyết → code → backtest → phản hồi → giả thuyết mới`. Đây chính là kiến trúc "Generator/Critic/Scheduler" đã bàn.

Hướng tiến hóa của nhóm này trong 2026 là **làm memory ngày càng có cấu trúc**:

| Hệ         | Ngày        | Memory là gì                                                                                                                                                                                        |
| ----------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RD-Agent(Q) | NeurIPS 2025 | Knowledge base + bandit scheduler (Thompson sampling)                                                                                                                                                 |
| AlphaMemo   | 6/2026       | **Search-process memory** — lưu cả *quỹ đạo tìm kiếm*, không chỉ kết quả: bước nào dẫn tới bước nào, thất bại nào vì sao                                              |
| XALPHA      | 7/2026       | **Ba brain**: Macro (kế hoạch chu kỳ) / Micro (vòng tìm factor) / Cross (quy kết factor về "archetype" cơ chế). Nạp báo cáo phân tích tài chính thành memory phân tầng A/B/C |

**XALPHA đáng chú ý nhất về mặt thiết kế gate.** Nó không dùng một ngưỡng mà dùng hai tầng:

```
Bình thường:  RankIC ≥ 0.005,  positive-ratio ≥ 0.50,  percentile 60%
Elite:        RankIC ≥ 0.01,   positive-ratio ≥ 0.55,  percentile 80%
Điểm chọn:    70% train alpha + 30% OOS evolution score
Lọc trùng:    tương quan tối đa 0.60 khi nạp vào thư viện (40 factor)
```

Kết quả CSI300 (train 2011–2020 / valid 2021 / test 2022–2025, mục tiêu lợi suất open-to-open 10 ngày): IC **0.0619**, RankIC 0.0748, ICIR 0.3703, IR **1.5368** — vượt AlphaAgent (IR 0.9516) và CogAlpha (IC 0.0366). Backbone: **gpt-oss-120b**.

**Crypto Constrained Agents** (4/2026) là hệ duy nhất trong nhóm làm crypto, và cách ràng buộc của nó rất đáng học:

> LLM đề xuất giả thuyết, nhưng **một engine tất định** giữ mọi thứ còn lại: dữ liệu point-in-time, một **DSL bị giới hạn** (chỉ cho phép cross-sectional rank, time-series transform, hàm phi tuyến), gate chọn lọc **đặt trước**, và trace thí nghiệm **append-only**. LLM **không được sửa rule đánh giá hay data split trong phiên**.

Kết quả: long-short equal-weight, OOS thuần 2024–2026, sau phí 5bp một chiều → **AR 44.55%, Sharpe 1.55**. Factor đơn tốt nhất Sharpe > 2.4. Universe crypto daily lọc theo thanh khoản và lịch sử giao dịch; train 2020–22 / valid 2023 / OOS 2024–26; độ trễ thực thi 1 ngày.

**Điểm yếu cố hữu của K2** được paper RFT (mục K5) gọi tên chính xác: *"context explosion and feedback drift"* và *"search stagnation"*. Prompt dài dần, model bắt đầu trôi khỏi phản hồi cũ, và toàn bộ vòng lặp kẹt ở một hướng.

---

### K3 — Tìm kiếm trên cây (MCTS)

**Thành viên:** Alpha Jungle (AAAI 2026), Tree-structured Thoughts (8/2025), AgonAlpha (8/2026).

Thay vì một chuỗi tuyến tính, không gian tìm kiếm là **cây**: mỗi node là một biểu thức factor, mỗi nhánh là một phép tinh chỉnh. UCB cân bằng khai thác/khám phá, backtest đóng vai reward.

**Alpha Jungle** bổ sung *frequent-subtree avoidance* — phát hiện các cây con lặp lại quá nhiều và né chúng, một cách chống hội tụ sớm rẻ tiền.

**AgonAlpha (8/2026) có thiết kế validation tốt nhất trong toàn bộ khảo sát này** (chi tiết mục 5.3). Kiến trúc ba phần:

| Thành phần                           | Vai trò                                                                                                                                                  |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Proposer**                     | Sinh 16 công thức ứng viên, chạy giải đấu loại trực tiếp 16→8→4→2→1, nộp cái sống sót                                                  |
| **Fresh-Context Reviewer**       | Audit độc lập trên artifact đã đóng băng,**có quyền phủ quyết** nếu phát hiện bịa số, và được phép chạy lại đánh giá    |
| **Pending-Aware MCTS Scheduler** | Phân bổ ngân sách đánh giá qua các dòng nghiên cứu,**có tính đến công việc đang chạy dở** để chạy song song không bị đói |

Triết lý "prompt economy": **toàn bộ hệ chỉ có 101 dòng prompt** (57 proposer + 44 reviewer), dùng lại cho mọi node. Kết quả: 60 submission qua 5 lần triển khai → **17 alpha hạng SPECTACULAR**, Sharpe tốt nhất 3.48, Fitness tốt nhất 9.50 — chấm bởi WorldQuant BRAIN trên US TOP3000, 2019–2023, độ trễ dữ liệu 1 ngày.

> 🔑 **Chi tiết quan trọng nhất:** *Reviewer có ngữ cảnh mới tinh*. Nó không thấy quá trình tìm kiếm, không thấy các lần thử thất bại — nên không thể bị thuyết phục bởi câu chuyện mà proposer tự kể. Đây là **temporal isolation áp dụng cho chính agent**, không chỉ cho dữ liệu.

---

### K4 — Tiến hóa quần thể / quality-diversity

**Thành viên:** QuantEvolve, MadEvolve, AlgoEvolve, QuantaAlpha, CogAlpha, AlphaPROBE, EFS.

Đây là kiến trúc bạn đã chọn. **Tin tốt: nhiều nhóm độc lập hội tụ về thiết kế này trong 2026** — bằng chứng về tính khả thi, chưa phải về hiệu quả (xem §7.1).

**MadEvolve** (5/2026, Kvasiuk–Li–Colegrove–Münchmeyer) dựng gần như y hệt QuantEvolve mà không trích dẫn lẫn nhau:

```
Parent sampling từ population database
  → Inspiration retrieval (global best + recent top + diverse neighbors)   ← chính là "cousins"
  → LLM mutation (~70% differential patch, ~30% viết lại toàn bộ)
  → Backtest fitness
  → Update database
```

Kèm **MAP-Elites grid** (phân hoạch theo code complexity, diversity, performance) + **island model 5 đảo, migration mỗi 5 generation, tỉ lệ 10%** + global elite archive. Con số migration 10% trùng khít với QuantEvolve.

> 🔍 **Đối chiếu repo code (21/9/2026) — [`tianyi-stack/MadEvolve`](https://github.com/tianyi-stack/MadEvolve), commit `8b881d3`, 3/3/2026.** Repo là **framework tổng quát**, không có evaluator, dữ liệu hay code domain Bitcoin — **không tái lập được** con số Sharpe 5.65. Liên hệ repo ↔ paper chưa xác minh (README không trích dẫn paper). Quan trọng hơn: trong code công khai, **island model là code chết** — `HybridPopulationManager.get_selection_pool()` luôn trả `[]`, nên parent luôn được chọn từ **top-20 toàn cục** (`artifact_store.get_top_programs(20)`); `sample_from_island()` không được gọi ở đâu. MAP-Elites grid chỉ dùng để lấy "diverse inspirations". ⇒ Bằng chứng "hội tụ độc lập về island model" chỉ nằm ở **mô tả trong paper**, không ở code chạy được. Chi tiết: [Architecture_Design.md](Architecture_Design.md) §3.1.

> Hai nhóm độc lập, hai domain khác nhau (alpha factor vs Bitcoin execution), cùng ra một kiến trúc. **Đây là bằng chứng mạnh rằng MAP-Elites + island là lời giải đúng cho lớp bài toán này**, không phải lựa chọn tùy hứng của riêng paper QuantEvolve.

**QuantaAlpha** (2/2026, liên minh SUFE/Thanh Hoa/Bắc Đại/CMU/HKUST) — con số đẹp nhất trong toàn khảo sát:

| CSI 300, test 2022–2025, GPT-5.2 |                  |
| --------------------------------- | ---------------- |
| IC                                | **0.1501** |
| ICIR                              | 0.9110           |
| ARR                               | **27.75%** |
| MDD                               | −7.98%          |
| IR                                | **3.3251** |

Transfer zero-shot: CSI 500 **+160%**, S&P 500 **+137%** lợi nhuận vượt trội tích lũy trong 4 năm. Code: [`QuantaAlpha/QuantaAlpha`](https://github.com/QuantaAlpha/QuantaAlpha).

Cơ chế kiểm soát: AST làm biểu diễn trung gian, xác minh **nhất quán ngữ nghĩa** giữa giả thuyết ↔ mô tả ↔ biểu thức ↔ code; giới hạn độ dài ký hiệu ≤250 và ≤6 base feature; lọc trùng bằng AST isomorphism. Mutation = tự phản tỉnh để tìm node quyết định kém rồi **chỉ viết lại đoạn cục bộ** của quỹ đạo; crossover = ghép các đoạn hiệu quả từ nhiều parent.

Fitness có dạng phạt độ phức tạp:

```
R(τ) = ℒ(f_τ(X), y) − λ·ℛ(f_τ)
```

**AlgoEvolve** (6/2026, Sharma & Shroff) thêm một trục vuông góc — **meta-evolution**:

```
Vòng trong:  LLM (Gemini Flash) sinh strategy Python
             ← nhận top-2 tốt nhất + top-2 tệ nhất của generation trước
Vòng ngoài:  Meta-LLM (Gemini Pro) tiến hóa chính CÁI PROMPT
             "Prompt Genome" = 4 gene: mutation style / creative focus
                                       / constraints / reasoning framework
             + uniform crossover giữa các elite genome
```

Đây là ý tưởng **rẻ nhất để sao chép** trong cả báo cáo: khi đã có vòng tiến hóa, thêm một lớp tiến hóa prompt gần như không tốn thêm hạ tầng. Paper khẳng định prompt tiến hóa **luôn vượt** chỉ dẫn do người thiết kế. (Nhưng xem mục 5.4 — kết quả số của AlgoEvolve có vấn đề.)

**AlphaPROBE** (2/2026) thử một biến thể: thay lưới MAP-Elites bằng **đồ thị**, tiến hóa có thiên lệch trên đồ thị cộng truy xuất có nguyên tắc. Code: [`gta0804/AlphaPROBE`](https://github.com/gta0804/AlphaPROBE). Chưa đủ bằng chứng để kết luận nó hơn lưới.

**CogAlpha** (China Mobile JiuTian + HKU) đi hướng ngược — thay vì để feature map tạo đa dạng, nó **áp đặt đa dạng bằng cấu trúc tổ chức**: **21 agent xếp 7 tầng** theo chủ đề tài chính (cấu trúc thị trường → tail risk → price-volume → price-volatility → multi-scale complexity → regime-gating → geometric/fusion), cộng multi-agent quality checker (code quality / repair / judge / logic improvement) và **unit test rò rỉ thời gian nhúng thẳng vào checker**. Kết quả CSI300 10-ngày: IC 0.0591, ICIR 0.3410, IR **1.8999**. Chi phí ~5–9 giây/factor, ~1 giờ/generation trên một H100.

---

### K5 — Cập nhật trọng số (RFT/RL) — hướng mới nhất 2026

**Thành viên:** QuantEvolver (5/2026), Alpha-R1 (bản cập nhật 9/9/2026).

Đây là bước nhảy khái niệm lớn nhất trong năm. Thay vì nhồi phản hồi backtest vào prompt, **biến phản hồi backtest thành gradient cập nhật trọng số model**.

**QuantEvolver** — reward `DiCo` (Diversity-Complementarity):

| Thành phần reward | Nội dung                                                                               |
| ------------------- | --------------------------------------------------------------------------------------- |
| Dự báo            | Directional accuracy, IC, RankIC                                                        |
| Shaping             | Phạt lặp lại nguyên xi, thưởng đa dạng họ cấu trúc, điểm bổ trợ hành vi |

Base model: **Qwen3-14B**. Kết quả: RankIC 0.0586 trên benchmark cross-sectional theo giờ (**+109.5%** so với baseline mạnh nhất), 0.1923 trên daily equity — vượt AlphaBench, QuantaAlpha, R&D-Agent, Alpha-Jungle. Code: [`QuantLLM/QuantEvolver`](https://github.com/QuantLLM/QuantEvolver).

> 🔑 **Lý lẽ phản trực giác của họ, và nó quan trọng cho dự án bạn:** họ **cố ý chọn model nhỏ (≤30B)** vì model rất lớn có *"stable generation preferences"* — sở thích sinh ổn định — khiến tìm kiếm bị đình trệ. Model lớn quá tự tin vào câu trả lời kinh điển của nó.

**Alpha-R1** (Qwen3-8B, 64×H800 trong ~120 giờ) báo cáo AR 47.87% trên S&P 500 và 40.57% trên CSI 300, Sharpe 1.62 / 2.23 — so với baseline LLM mạnh nhất (DeepSeek-R1) chỉ 21.94% / 14.66%. Code: [`FinStep-AI/Alpha-R1`](https://github.com/FinStep-AI/Alpha-R1).

**Nhưng Alpha-R1 quan trọng vì lý do khác hẳn — xem mục 5.2.**

---

## 3. Bảng tổng hợp toàn bộ hệ thống

| Hệ thống                | Ngày           | K             | Output                         | Thị trường                | Kết quả chính                   | Code       |
| ------------------------- | --------------- | ------------- | ------------------------------ | ---------------------------- | ---------------------------------- | ---------- |
| Alpha-GPT                 | 7/2023          | K1            | Biểu thức                    | A-share                      | —                                 | ❌         |
| AlphaAgent                | KDD 2025        | K2            | Factor                         | CSI500, S&P500               | IR 1.488 / 1.0545                  | ✅         |
| RD-Agent(Q)               | NeurIPS 2025    | K2            | Factor + ML model              | CSI300                       | IC 0.0532, IR 1.7382               | ✅ 14.7k★ |
| Alpha Jungle              | AAAI 2026       | K3            | Biểu thức                    | A-share                      | —                                 | ✅         |
| EFS                       | 7/2025          | K4            | Factor (sparse portfolio)      | —                           | —                                 | —         |
| Chain-of-Alpha            | 8/2025          | K2            | Factor                         | A-share                      | —                                 | —         |
| Tree-structured Thoughts  | 8/2025          | K3            | Factor                         | A-share                      | —                                 | —         |
| **QuantEvolve**     | 10/2025         | **K4**  | **Strategy**             | Multi                        | SR 1.52, CR 256% @gen150           | ❌         |
| CogAlpha                  | 11/2025→4/2026 | K4            | Factor                         | CSI300/500, S&P500, HSI/HSCI | IC 0.0591, IR 1.8999               | ❌         |
| Alpha-R1                  | 12/2025→9/2026 | **K5**  | Factor screening               | S&P500, CSI300               | AR 47.87%,**DSR 0.39–0.85** | ✅         |
| QuantaAlpha               | 2/2026          | K4            | Factor                         | CSI300/500, S&P500           | IC 0.1501, IR 3.3251               | ✅         |
| AlphaPROBE                | 2/2026          | K4 (graph)    | Factor                         | A-share                      | —                                 | ✅         |
| **SysTradeBench**   | 4/2026          | *benchmark* | —                             | —                           | 17 LLM, 61.8% pass                 | ✅         |
| Crypto Constrained        | 4/2026          | K2 + DSL      | Factor                         | **Crypto**             | AR 44.55%, SR 1.55 OOS             | ❌         |
| QuantEvolver (RFT)        | 5/2026          | **K5**  | Factor                         | 3 benchmark                  | RankIC +109.5%                     | ✅         |
| **Agentic Trading** | 5/2026          | *survey*    | —                             | —                           | **0/19 đạt R3**            | —         |
| MadEvolve                 | 5/2026          | K4            | **Strategy + execution** | **Bitcoin**            | Test SR 5.65 ⚠️                  | ✅         |
| AlphaMemo                 | 6/2026          | K2 (memory)   | Factor                         | Qlib                         | vs AlphaGen/Forge/QCM              | ✅         |
| AlgoEvolve                | 6/2026          | K4 + meta     | **Strategy Python**      | Equity intraday 5m           | Sharpe 5.60 ⚠️                   | ❌         |
| XALPHA                    | 7/2026          | K2 (memory)   | Factor                         | CSI300                       | IC 0.0619, IR 1.5368               | ❌         |
| AgonAlpha                 | 8/2026          | K3            | Biểu thức                    | US TOP3000                   | 17 SPECTACULAR, SR 3.48            | ✅         |
| Survey FITEE              | 11/2025         | *survey*    | —                             | —                           | Taxonomy vai trò LLM              | —         |

---

## 4. Mất cân đối: literature gần như toàn trường phái A

Đếm theo output:

| Output                                                                 | Số hệ        | Ghi chú                                                                    |
| ---------------------------------------------------------------------- | -------------- | --------------------------------------------------------------------------- |
| **Factor / biểu thức** (trường phái A)                      | ~18/22         | Gần như luôn CSI300/CSI500/S&P500, Qlib, dự báo lợi suất 1–10 ngày |
| **Strategy thực thi được** có entry/exit (trường phái B) | **2/22** | AlgoEvolve (equity intraday), MadEvolve (Bitcoin)                           |
| Benchmark / survey                                                     | 3/22           |                                                                             |

**Điều này có ý nghĩa hai mặt cho bạn:**

✅ **Mặt tốt** — ít cạnh tranh. Nếu trường phái B thực sự hiệu quả với cá nhân như [04](04-TRUONG-PHAI-B-HIEU-QUA.md) chỉ ra, thì gần như không ai trong giới academic đang khai thác nó bằng LLM.

❌ **Mặt xấu** — ít vai để đứng lên. Mọi con số chuẩn (IC, ICIR, RankIC), mọi bộ baseline, mọi bài học về overfitting trong literature đều thuộc về cross-sectional factor. Khi bạn cần một điểm tham chiếu cho "strategy TA của tôi có tốt không", literature không cho bạn cái đó. Bạn phải quay về [04](04-TRUONG-PHAI-B-HIEU-QUA.md) (Hurst-Ooi-Pedersen: Sharpe ~0.4/market) làm mốc.

Cũng lưu ý: **QuantEvolve là hệ K4 duy nhất sinh strategy đầy đủ mà lại không công khai code.** Bạn đang tự dựng lại một thứ chưa ai công khai dựng lại.

---

## 5. Khoảng trống validation — bằng chứng cứng

Đây là phần quan trọng nhất của báo cáo, và nó **xác nhận luận điểm trung tâm** của [07-VALIDATION-LAYER.md](07-VALIDATION-LAYER.md) bằng số liệu, không phải bằng suy đoán.

### 5.1 Survey "Agentic Trading" (5/2026) — con số tàn nhẫn

Trong 19 nghiên cứu đạt tiêu chí tối thiểu (có Action Output + Closed-Loop Evaluation):

| Tiêu chí                                                       | Đạt             |
| ---------------------------------------------------------------- | ----------------- |
| Có data split nhất quán theo thời gian, trích xuất được | **2 / 19**  |
| Khai báo rõ mô hình chi phí giao dịch                      | **1 / 19**  |
| Xử lý universe / survivorship bias                             | **1 / 19**  |
| Mức tái lập thấp nhất (R0)                                  | **15 / 19** |
| Mức tái lập cao nhất (R3)                                    | **0 / 19**  |

Kết luận nguyên văn của survey: *"architectural experimentation is expanding rapidly, while comparable evaluation protocols, execution semantics, and reproducible artifacts remain the field's immediate bottlenecks."*

Và về đúng vấn đề của kiến trúc K3/K4:

> *"Search algorithms (MCTS, ToT) that evaluate thousands of candidate strategies inherently face the multiple testing problem — finding a profitable trajectory purely by chance."*

Survey kiến nghị bắt buộc báo cáo **"Search Budget"** — chính là **trial ledger** mà bạn đã thiết kế trong [Architecture_Design.md](Architecture_Design.md).

### 5.2 Alpha-R1 — hệ duy nhất báo cáo DSR, và nó trượt

Trong toàn bộ khảo sát, **chỉ một hệ công bố Deflated Sharpe Ratio**. Con số:

| Chỉ số                              | Giá trị              |
| ------------------------------------- | ---------------------- |
| PSR                                   | 0.96 – 0.996          |
| **DSR (N = 101–158)**          | **0.39 – 0.85** |
| DSR (N = 34, ước lượng hẹp hơn) | 0.84 – 0.89           |
| PBO (CSCV)                            | 0.016 – 0.133         |

Tác giả thừa nhận thẳng: DSR *"remains below the conventional 0.95 bar"*, và giải thích đó là *"an inherent property of a single-year window under conservative multiple-testing correction"*, kèm cảnh báo *"The current evidence is limited to a single 12-month test window."*

> 🔴 **Đây là con số quan trọng nhất trong cả báo cáo này.**
>
> Một hệ được train trên **64 GPU H800 trong 120 giờ**, báo cáo **AR 47.87%** và **Sharpe 1.62**, với chỉ **34–158 trial** — vẫn **không qua nổi ngưỡng DSR 0.95**.
>
> Kế hoạch của bạn là 4.500–9.000 *lời gọi LLM* — số trial thống kê ít hơn, và số trial độc lập (`N_eff`) còn ít hơn nữa, nhưng vẫn ở cỡ hàng trăm–hàng nghìn *(đính chính 21/9/2026: bản trước gọi nhầm lời gọi LLM là trial; xem [Architecture_Design.md](Architecture_Design.md) §4.1)*. DSR phạt theo số trial độc lập. Nếu Alpha-R1 với 158 trial còn trượt, thì ngưỡng Sharpe bạn cần để qua DSR ở 9.000 trial sẽ cao hơn **đáng kể**.
>
> Điều này không nói dự án bạn bất khả thi. Nó nói rằng **trial ledger không phải là thủ tục hành chính — nó là ràng buộc gắt nhất trong toàn hệ thống**, và bạn cần thiết kế để *tiết kiệm trial*, không phải để *chạy nhiều trial*.

Hệ quả thiết kế trực tiếp: mọi cơ chế làm giảm số trial cần thiết (insight repository, meta-evolution prompt, DSL hạn chế không gian tìm kiếm) có giá trị **gấp đôi** — vừa tiết kiệm tiền, vừa hạ ngưỡng DSR.

### 5.3 AgonAlpha — bài học không nằm ở thống kê

AgonAlpha không tính DSR. Nhưng thiết kế của nó đáng tin hơn, vì một lý do cấu trúc:

> **Evaluator nằm ngoài tầm với của tác giả.** WorldQuant BRAIN quyết định dữ liệu, simulator, chỉ số và thang điểm. Tác giả *không thể* tinh chỉnh gì ở đó.

Paper nói rõ điểm khác biệt so với holdout trong academic: ở holdout học thuật, **tác giả vẫn giữ quyền kiểm soát toàn bộ pipeline** — nên "holdout" chỉ là một lời hứa.

Cộng với Evidence Integrity Audit 5 chiều (khớp biểu thức–chỉ số, nhất quán logic dấu, lý do cho hằng số, ổn định theo năm, phát hiện trùng lặp do chọn lọc), self-correlation gate 0.85, và reviewer có **quyền zero hóa nếu phát hiện bịa số**.

> 🔑 **Rút ra cho bạn:** nguyên tắc **P6 (holdout write-once)** đang đúng hướng, nhưng nó yếu ở chỗ *bạn tự thực thi lời hứa với chính mình*. Cách làm nó thật hơn: khóa holdout ở **cấp hệ điều hành** (file chỉ-đọc, hash cam kết trước, `holdout_access` có `PRIMARY KEY` như bạn đã thiết kế), và cân nhắc một **evaluator process riêng** không chia sẻ trạng thái với vòng tiến hóa.

### 5.4 Cảnh báo: đọc kỹ bảng kết quả trước khi tin

**AlgoEvolve** báo Sharpe **5.60**. Nhưng bảng của chính paper:

| Phương pháp                   | Mean (%)          | Vol (%) | **Ann. Sharpe** | Max DD (%) |
| -------------------------------- | ----------------- | ------- | --------------------- | ---------- |
| AlgoEvolve (6 MG)                | 0.31              | 0.88    | **5.60**        | 1.59       |
| Standard Evol (chỉ vòng trong) | 0.10              | 0.29    | **5.71** ⬅     | 0.42       |
| RF baseline                      | **0.51** ⬅ | 1.52    | 5.24                  | 7.27       |
| LSTM baseline                    | −0.05            | 0.68    | −1.11                | 3.79       |
| Seed heuristic                   | −0.78            | 1.06    | −11.75               | 17.56      |

Hai quan sát:

1. **Meta-evolution KHÔNG cải thiện Sharpe** — bỏ nó đi (Standard Evol) cho Sharpe **cao hơn** (5.71 > 5.60) với drawdown thấp hơn gần 4 lần. Nó chỉ tăng mean return bằng cách tăng rủi ro.
2. **Random Forest cho mean return cao hơn** (0.51% > 0.31%).

Paper còn thừa nhận: elite prompt đạt Sharpe 5.60 nhưng **trung bình quần thể chỉ ~1.21** — tức con số headline là *best-of-N*, chính là selection bias ở dạng thuần khiết nhất.

**MadEvolve** báo test Sharpe 5.65 trên Bitcoin, nhưng có xử lý trung thực hơn: họ dành hẳn một mục *"Research or P-Hacking? The Central Question"*, dùng khung của Bailey et al. (2014), theo dõi **tỉ lệ suy giảm IS→OOS** so với mức mà lý thuyết multiple-testing dự đoán cho p-hacking thuần túy, và thừa nhận với bài toán dự báo thì *"kết quả nhiều sắc thái hơn... mô hình dự báo tối ưu cũng thể hiện một mức độ overfitting"*.

> 🔍 **Đọc toàn văn paper MadEvolve (21/9/2026) — điều chỉnh đánh giá "trung thực hơn" ở trên.** Mục §7 có thật, nhưng yếu hơn vẻ ngoài:
> - **Null quá yếu:** so với một quy trình "p-hacking quanh baseline" (PnL ~ Gaussian quanh hiệu suất baseline), không phải null "không có edge"; không tính DSR hay PBO, không đưa số trial vào.
> - **Tập test không sạch theo nghĩa P6:** test 2025 được đo cho **mọi** champion IS trong suốt quá trình tiến hóa (Hình 10–11) và dùng chung cho 5 run + các run Claude Code. Không dùng để *chọn*, nhưng tác giả đã nhìn nó hàng nghìn lần và chọn run nào để báo cáo.
> - **Backtest lạc quan:** lệnh limit khớp 100% đúng giá mỗi khi giá xuyên qua, không hàng đợi; dữ liệu phút gộp nhiều sàn; impact tính hậu kiểm. Baseline đã có Sharpe 4.81. Tác giả tự viết *"still far from being realistic"*.
> - **Joint không luôn thắng:** Run 3 (joint strategy) không vượt Run 2 (chỉ đặt lệnh); Run 5 (joint feature + strategy) có Sharpe OOS cao nhất nhưng chỉ giữ 39% PnL từ validation sang test và win rate tụt 68.6% → 49.8%.
> - **Độ nhạy prompt:** so sánh với Claude Code, một run cho +627% OOS, run khác chỉ đổi nhẹ prompt cho +44%.
> - **Điểm đáng học:** giới hạn tham số (15–20, khai báo UPPER_CASE, vượt thì phạt) được thêm sau khi pilot không giới hạn bị overfit; **cố ý không** dùng vòng tối ưu tham số bên trong vì làm phình số trial; chọn PnL thay Sharpe vì Sharpe bị thổi được bằng cách giao dịch ít. Đã đưa vào [Architecture_Design.md](Architecture_Design.md) bản 0.5.

> **Nguyên tắc đọc paper trong lĩnh vực này:** khi thấy Sharpe > 3 ở daily/intraday, hãy tìm 3 thứ trước khi tin — (a) có phải best-of-N không, (b) chi phí giao dịch bao nhiêu bp, (c) khoảng OOS dài bao lâu. Nếu thiếu bất kỳ cái nào, con số vô nghĩa.

### 5.5 SysTradeBench — LLM tự tay viết ra data leakage

Benchmark 4/2026, **17 LLM × 12 strategy**, chấm 4 chiều: D1 trung thành spec / D2 kỷ luật rủi ro / D3 tin cậy & audit / D4 chỉ báo OOS.

Tỉ lệ qua đủ mọi validity gate: **61.8%** (dù parse thành công 78.4%).

Các **failure mode** ghi nhận được — đọc kỹ cái thứ hai:

- Audit log thiếu/không đầy đủ dù test chức năng vẫn pass
- **Truy cập dữ liệu tương lai qua negative shift (`df.shift(-1)`)**
- Thực thi bất định do random không seed hoặc thứ tự dict
- Sai xử lý NaN ở cửa sổ khởi tạo của rolling indicator
- Ghi vi phạm ràng buộc sai, thiếu mức độ nghiêm trọng

> 🔴 **`df.shift(-1)` chính là leaky oracle trong [07](07-VALIDATION-LAYER.md) — nhưng LLM tự viết ra nó mà không ai bảo.**
>
> Bạn thiết kế `leaky_oracle.py` như một **test có chủ đích** để kiểm tra guardrail. SysTradeBench chứng minh rằng LLM **sinh ra nó một cách tự phát** khi được giao viết strategy. Nghĩa là guardrail chống leakage không phải là phòng xa — nó là thứ sẽ bị kích hoạt thường xuyên trong vận hành thực tế.

Phát hiện thứ hai rất quan trọng cho lộ trình của bạn — **tương quan giữa độ phức tạp strategy và tỉ lệ hợp lệ: Spearman ρ = −0.68 (p < 0.05)**:

| Strategy            | Tỉ lệ hợp lệ |
| ------------------- | ---------------- |
| Double MA Crossover | **88.2%**  |
| ...                 | ...              |
| Index Enhancement   | **35.3%**  |

Và cơ chế chống trôi spec (**rẻ, nên copy nguyên**):

```
Tầng 1 — checksum tĩnh:  SHA256 trên các trường logic lõi (entry/exit rule, tham số)
Tầng 2 — hồi quy trace:  khoảng cách Levenshtein Δ trên chuỗi hành động
                         (Δ chuẩn hóa = Levenshtein / max(len) ∈ [0,1]; so với trace neo
                          của bản cài đặt đầu tiên — đặc tả đầy đủ: Architecture_Design §3.1.7)

   Δ < 0.05         → sửa lỗi, cho phép
   0.05 ≤ Δ < 0.15  → gắn cờ, cần người xem
   Δ ≥ 0.15         → phân kỳ đáng ngờ, D1 = 0, hủy vòng lặp
```

---

## 6. Backbone model: dữ liệu mới đảo ngược trực giác

Đây là phần **sửa lại** khuyến nghị tôi đưa ra trước đó về model nhỏ.

### 6.1 Ablation của CogAlpha (CSI300)

| Model                    | IC               | RankIC             | ICIR             | RankICIR           | IR               |
| ------------------------ | ---------------- | ------------------ | ---------------- | ------------------ | ---------------- |
| Llama3 8B                | 0.0121           | **−0.0074** | 0.0972           | **−0.0540** | 0.5077           |
| Llama3 70B               | 0.0205           | 0.0229             | 0.1786           | 0.1915             | 0.6312           |
| gpt-oss-20B              | 0.0061           | 0.0075             | 0.0613           | 0.0680             | 0.4885           |
| **gpt-oss-120B**   | **0.0300** | **0.0318**   | **0.2501** | **0.2595**   | **0.8015** |
| GPT-4.1                  | 0.0118           | 0.0114             | 0.1069           | 0.1037             | 0.3628           |
| **o3** (reasoning) | **0.0019** | **−0.0050** | 0.0203           | −0.0475           | **0.2278** |

Ba điều bất ngờ:

1. **Llama3-8B cho RankIC ÂM.** Model nhỏ không chỉ yếu hơn — nó sinh ra factor *sai dấu một cách hệ thống*. Đây là tệ hơn việc không làm gì.
2. **gpt-oss-20B tệ hơn Llama3-8B về IC.** Tham số không phải trục đúng.
3. **o3 — model reasoning mạnh nhất trong danh sách — tệ nhất.** Nguyên văn paper: *"the reasoning-oriented model achieving the worst performance among all evaluated LLMs."*

### 6.2 Vì sao model reasoning lại tệ ở đây

Ghép với lý lẽ của QuantEvolver — model rất lớn có **"stable generation preferences"** gây đình trệ tìm kiếm — ta có một giải thích *hợp lý* (giả thuyết, chưa được kiểm chứng):

> Nhiệm vụ này **không phải** là tìm câu trả lời đúng. Nó là **sinh ra nhiều giả thuyết đa dạng, phần lớn sẽ sai**, để cơ chế chọn lọc làm việc của nó.
>
> Model reasoning được tối ưu để hội tụ về câu trả lời tốt nhất. Ở đây, hội tụ *chính là* thất bại — nó là mode collapse. Model càng "chắc chắn", feature map càng rỗng.

Điều này giải thích luôn vì sao K4 cần MAP-Elites và K5 cần diversity reward: **cả hai đều là cơ chế chống lại xu hướng hội tụ tự nhiên của LLM.**

> ⚠️ **Giới hạn bằng chứng** *(bổ sung 21/9/2026)*. CogAlpha là benchmark **sinh factor cross-sectional trên CSI300** (trường phái A), một lần chạy mỗi model. Paper **không** kiểm tra nguyên nhân — "mode collapse" là cách giải thích của tôi, không phải kết quả đo. Và chưa có gì cho thấy thứ hạng này giữ nguyên với rule-based TA đa thị trường. Vì vậy: *ưu tiên* non-reasoning làm mặc định, **không cấm** reasoning model — A/B nội bộ trước khi kết luận.

### 6.3 Tầng model theo SysTradeBench (sinh code strategy — sát việc của bạn)

| Tầng            | Validity         | Điểm     | Model                                                      |
| ---------------- | ---------------- | ---------- | ---------------------------------------------------------- |
| **Top**    | 91.7%            | 7.29–7.85 | GPT-5.2, GPT-5.1, o3, Grok-4 Fast                          |
| **Mid**    | 75–91.7%        | 6.94–7.44 | GLM-4.6, DeepSeek-V3, Claude variants                      |
| **Budget** | **8–58%** | 5.38–6.26 | GLM-4.7, Grok-4, Gemini Flash, Gemini-2.5 Pro, DeepSeek-R1 |

Tầng Mid đạt **90–95% chất lượng của tầng Top với 40–50% chi phí** — đây là điểm ngọt.

Lưu ý mâu thuẫn thú vị: **o3 nằm tầng Top ở SysTradeBench (viết code đúng spec) nhưng bét bảng ở CogAlpha (nghĩ ra factor tốt).** Không hề mâu thuẫn — đó là hai kỹ năng khác nhau, và nó xác nhận đúng chiến lược **định tuyến model dị thể** đã bàn.

### 6.4 Khuyến nghị đã hiệu chỉnh

| Vai trò                                      | Model                                                                                                              | Lý do                                                |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| **Research Agent** (nghĩ giả thuyết) | Model mạnh, **mặc định non-reasoning**. `gpt-oss-120b` là điểm khởi đầu có bằng chứng (trên benchmark trường phái A) | Cần đa dạng, không cần hội tụ. Tránh o3-class |
| **Coding Team** (viết code)            | Model chuyên code, tầng Mid theo SysTradeBench                                                                   | 90–95% chất lượng, 40–50% giá                   |
| **Evaluation Team**                     | Tầng Mid + constrained decoding                                                                                   | Chấm điểm theo schema cố định                   |
| **Data Agent**                          | Context lớn nhất                                                                                                 | Đọc schema                                          |

**Đừng** dùng dense model < 10B không fine-tune ở bất kỳ vai nào (RankIC âm). **Đừng** làm RFT (Alpha-R1: 64×H800 × 120h).

---

## 7. Quan điểm của tôi

### 7.1 Bạn đã chọn đúng kiến trúc, và giờ có bằng chứng độc lập

MadEvolve dựng lại MAP-Elites + island 5 đảo + migration 10% mà không biết đến QuantEvolve. Hai nhóm, hai domain, một thiết kế. Đó là bằng chứng K4 **khả thi và tự nhiên** — *không* phải bằng chứng nó hiệu quả cho rule-based TA đa thị trường (cả hai đều self-reported, không đối chứng với random search). Coi là giả thuyết làm việc, kiểm chứng nội bộ ở GĐ 2 *(chỉnh mức kết luận 21/9/2026)*.

Đồng thời, các nhược điểm của K2 mà chúng ta suy luận ra ("hội tụ sớm") nay đã có tên gọi chính thức trong literature — *context explosion*, *feedback drift*, *search stagnation* — và là động cơ khiến cả ngành chuyển sang K4/K5.

### 7.2 Nhưng bạn đang giải sai bài toán khó nhất

Bài toán khó nhất **không phải kiến trúc**. Ngành đã giải xong phần đó — 5 mẫu, công khai, chép được.

Bài toán khó nhất là: **bạn sẽ chạy 4.500–9.000 lời gọi LLM — tức hàng trăm đến hàng nghìn trial thống kê — và Alpha-R1 với 158 trial đã trượt DSR.**

Điều này nên đổi thứ tự ưu tiên của bạn. Trước đây khung nhìn là *"xây agent loop mạnh, rồi thêm validation"*. Khung nhìn đúng hơn:

> **Số trial là ngân sách bị khan hiếm gắt nhất của dự án. Kiến trúc agent nên được chọn theo tiêu chí "sinh ra strategy tốt trên mỗi trial", không phải "sinh ra nhiều strategy".**

Nghịch lý là K4 — kiến trúc bạn chọn — là kiến trúc **tốn trial nhất**. Nó đánh đổi trial lấy đa dạng.

Điều đó vẫn đúng đắn, nhưng chỉ vì lý do đã nói ở lần trước: **feature map chính là danh mục đa dạng hóa**, và breadth là biến quyết định (Sharpe 0.4 → 1.6). Bạn không dùng 9.000 trial để tìm một strategy — bạn dùng để lấp 20 ngăn ít tương quan. **Miễn là bạn kiểm định 20 ngăn đó như một danh mục, không phải như 20 ứng viên riêng lẻ.**

> Đây là chỗ mà một quyết định thiết kế cụ thể trở nên then chốt: **DSR nên tính trên lợi suất của danh mục hợp nhất, với N = tổng số trial của cả dự án.** Không phải tính DSR riêng cho từng cell rồi chọn cell tốt nhất — làm thế là selection bias tái xuất qua cửa sau.

### 7.3 Ba thứ nên chép ngay, chi phí thấp

| Chép gì                                                                                                                 | Từ đâu                 | Vì sao                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| **DSL bị ràng buộc + engine tất định giữ rule đánh giá; LLM không được sửa data split trong phiên** | Crypto Constrained Agents | Thu hẹp không gian tìm kiếm → tốn ít trial hơn → DSR dễ thở hơn. Và nó là guardrail cấu trúc, không phải hy vọng |
| **Drift detection 2 tầng: SHA256 trên logic lõi + Levenshtein trên action trace**                               | SysTradeBench             | Gần như miễn phí. Bắt được agent lén sửa spec giữa các vòng refine                                                      |
| **Reviewer ngữ cảnh mới tinh, có quyền phủ quyết**                                                           | AgonAlpha                 | Agent thứ hai không thấy lịch sử tìm kiếm → không bị thuyết phục bởi câu chuyện tự kể                               |

### 7.4 Một thứ KHÔNG nên chép dù nghe hay

**Meta-evolution prompt genome (AlgoEvolve).** Tôi đã định khuyến nghị nó vì ý tưởng đẹp và rẻ. Nhưng bảng ở mục 5.4 cho thấy **bỏ nó đi lại cho Sharpe cao hơn và drawdown thấp hơn 4 lần**. Bằng chứng ủng hộ nó hiện là âm.

Quan trọng hơn: mỗi biến thể prompt là một **trục tìm kiếm mới**, và mỗi trục mới làm phình `N` trong công thức DSR. Bạn đang trả trial để mua một thứ chưa chứng minh được giá trị.

### 7.5 Con số trong các paper này: đừng dùng làm mốc kỳ vọng

QuantaAlpha IR 3.3251, MadEvolve Sharpe 5.65, AlgoEvolve Sharpe 5.60. Đặt cạnh survey: **1/19 paper khai báo chi phí giao dịch**.

Mốc kỳ vọng đúng cho bạn vẫn là mốc trong [04](04-TRUONG-PHAI-B-HIEU-QUA.md): **Sharpe ~0.4 mỗi market, ~1.6 khi đa dạng hóa tốt.** Con số duy nhất trong khảo sát này gần với mức hợp lý và có kiểm định OOS tử tế là **Crypto Constrained Agents: Sharpe 1.55 sau phí 5bp trên OOS thuần 2024–2026** — và đó là cross-sectional long-short, không phải trường phái B.

### 7.6 Lợi thế cạnh tranh của bạn được xác nhận, nhưng bị thu hẹp

Trong [00](00-TONG-HOP-NGHIEN-CUU.md) chúng ta kết luận "không hệ nào có DSR/PBO — đây là lợi thế cạnh tranh". Sau khảo sát này, cần hiệu chỉnh:

- ✅ **Vẫn đúng về cơ bản**: 1/22 hệ báo cáo DSR, 0/19 đạt R3 tái lập.
- ⚠️ **Nhưng không còn là đất trống**: Alpha-R1 đã tính DSR+PBO+PSR (9/2026), AgonAlpha đã có external evaluator + audit 5 chiều (8/2026), Crypto Constrained Agents đã có engine tất định + gate đặt trước (4/2026), CogAlpha đã nhúng unit test rò rỉ thời gian.

Ngành đang đi đúng hướng bạn dự đoán, chỉ là chậm và rời rạc. Lợi thế thật của bạn giờ là **tổng hợp cả ba lớp phòng thủ vào một hệ** — chưa ai làm — chứ không phải "biết đến DSR".

### 7.7 Điều làm tôi lo nhất trong kế hoạch hiện tại

`ρ = −0.68` giữa độ phức tạp strategy và tỉ lệ sinh code hợp lệ. Double MA Crossover: 88.2%. Index Enhancement: 35.3%.

Feature map của bạn có một chiều là **strategy category**, và mục đích của nó là đẩy hệ khám phá các loại strategy ngày càng đa dạng — tức ngày càng phức tạp. Nhưng tỉ lệ sinh code hợp lệ **sụt theo độ phức tạp**.

Hệ quả: các ô ứng với strategy phức tạp sẽ **rỗng lâu hơn nhiều** so với các ô đơn giản, không phải vì chúng kém mà vì code không chạy được. Feature map sẽ *trông như* đã khám phá đầy đủ trong khi thực ra nó chỉ lấp được vùng dễ.

**Đề xuất:** ghi vào ledger cột `gen_attempts` và `gen_failures` cho từng cell. Nếu một ô có tỉ lệ thất bại sinh code > 50%, đó là tín hiệu cần **cấp thêm ngân sách refine cho ô đó**, không phải kết luận rằng vùng đó không có strategy tốt.

---

## 8. Tác động cụ thể lên Architecture_Design.md

| Mục                      | Thay đổi đề xuất                                                                                                                                   | Nguồn       |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| **3.1** Agent layer | Giữ nguyên kiến trúc QuantEvolve — MadEvolve hội tụ độc lập về cùng thiết kế (giả thuyết, chưa phải xác nhận hiệu quả)                                                                    | §7.1        |
| **3.1.7** Model     | Thay khuyến nghị "dùng model nhỏ" bằng bảng định tuyến dị thể ở §6.4.**Thêm cảnh báo: mặc định tránh reasoning model cho Research Agent** (giả thuyết, A/B ở GĐ 2) | §6          |
| **Gate mới ⑦**    | Drift detection 2 tầng (SHA256 + Levenshtein) giữa các vòng refine                                                                                  | §5.5        |
| **Guardrail**       | Thêm DSL hạn chế + engine tất định giữ rule đánh giá; agent không được sửa split                                                         | §7.3        |
| **Evaluation Team** | Tách thành**reviewer ngữ cảnh mới tinh**, không nhận lịch sử tìm kiếm, có quyền phủ quyết                                          | §5.3        |
| **Validation**      | **DSR tính trên danh mục hợp nhất với N = tổng trial toàn dự án**, không tính riêng từng cell                                       | §7.2        |
| **Ledger**          | Thêm cột`model_used`, `gen_attempts`, `gen_failures` theo cell                                                                                  | §6.4, §7.7 |
| **P6 holdout**      | Nâng lên cấp OS: file chỉ-đọc + hash cam kết trước + evaluator process riêng                                                                  | §5.3        |
| **Kỳ vọng**       | Không dùng IR 3.3 / Sharpe 5.6 của paper làm mốc. Giữ mốc Sharpe 0.4→1.6                                                                        | §7.5        |
| **KHÔNG làm**     | Meta-evolution prompt genome; RFT/RL fine-tuning                                                                                                        | §7.4, §6.4 |

---

## 9. Những gì tôi KHÔNG xác minh được

Ghi lại để không bị dùng nhầm như sự thật đã kiểm chứng:

- **Mọi con số trong báo cáo này lấy từ abstract/HTML của paper qua công cụ tóm tắt tự động.** Tôi không chạy lại bất kỳ backtest nào, không đọc code của bất kỳ repo nào.
- **Không kiểm tra repo có khớp paper không.** Đây đúng là lỗi đã bắt được với `tarsyang/quantevolve` (xem [00](00-TONG-HOP-NGHIEN-CUU.md) mục 4). Các repo QuantaAlpha, Alpha-R1, QuantEvolver, AlphaPROBE, AlphaMemo, SysTB **chưa được đối chiếu**.
- **Survey FITEE** ("A survey on large language model-based alpha mining", Springer, 11/2025) bị chặn sau đăng nhập. Chỉ đọc được phần tóm tắt: taxonomy vai trò LLM (miner / evaluator / interactive assistant) và 6 thách thức nêu tên — *đánh giá hiệu năng bị đơn giản hóa, hiểu số kém, thiếu đa dạng và độc đáo, động lực khám phá yếu, rò rỉ dữ liệu theo thời gian, rủi ro hộp đen và tuân thủ*.
- **Alpha Jungle, Tree-structured Thoughts, EFS, Chain-of-Alpha, AlphaMemo, AlphaPROBE** — chỉ lấy được kiến trúc ở mức khái niệm, không có bảng kết quả.
- **Số sao GitHub** lấy từ kết quả tìm kiếm, chưa kiểm chứng trực tiếp trên GitHub.
- **Chưa tìm thấy** hệ nào sinh strategy trường phái B cho **forex** hoặc **thị trường Việt Nam**. Có thể không tồn tại, cũng có thể tôi tìm chưa tới.

---

## Nguồn

**Khảo sát & benchmark**

- [Agentic Trading: When LLM Agents Meet Financial Markets](https://arxiv.org/abs/2605.19337) — arXiv 2605.19337, 5/2026
- [SysTradeBench](https://arxiv.org/pdf/2604.04812) — arXiv 2604.04812, 4/2026 · [`YgcCoder/SysTB`](https://github.com/YgcCoder/SysTB)
- [A survey on LLM-based alpha mining](https://link.springer.com/article/10.1631/FITEE.2500386) — FITEE, 11/2025

**Kiến trúc K1–K3**

- [Alpha-GPT](https://arxiv.org/pdf/2308.00016) — arXiv 2308.00016, 7/2023
- [Navigating the Alpha Jungle](https://arxiv.org/abs/2505.11122) — arXiv 2505.11122, AAAI 2026
- [From Flat to Hierarchical](https://arxiv.org/pdf/2508.16334) — arXiv 2508.16334, 8/2025
- [AgonAlpha](https://arxiv.org/html/2608.11250) — arXiv 2608.11250, 8/2026
- [Chain-of-Alpha](https://arxiv.org/pdf/2508.06312) — arXiv 2508.06312, 8/2025
- [AlphaMemo](https://arxiv.org/pdf/2606.20625) — arXiv 2606.20625, 6/2026 · [`jarrettyu/AlphaMemo`](https://github.com/jarrettyu/AlphaMemo)
- [XALPHA](https://arxiv.org/html/2607.08332v2) — arXiv 2607.08332, 7/2026
- [From Hypotheses to Factors: Constrained LLM Agents in Cryptocurrency Markets](https://arxiv.org/html/2604.26747v1) — arXiv 2604.26747, 4/2026

**Kiến trúc K4**

- [QuantaAlpha](https://arxiv.org/html/2602.07085v2) — arXiv 2602.07085, 2/2026 · [`QuantaAlpha/QuantaAlpha`](https://github.com/QuantaAlpha/QuantaAlpha)
- [MadEvolve](https://arxiv.org/html/2605.23007v1) — arXiv 2605.23007, 5/2026 · [madevolve.org](https://madevolve.org)
- [AlgoEvolve](https://arxiv.org/html/2606.26173) — arXiv 2606.26173, 6/2026
- [CogAlpha](https://arxiv.org/html/2511.18850v2) — arXiv 2511.18850, 11/2025→4/2026
- [AlphaPROBE](https://arxiv.org/pdf/2602.11917) — arXiv 2602.11917, 2/2026 · [`gta0804/AlphaPROBE`](https://github.com/gta0804/AlphaPROBE)
- [EFS](https://arxiv.org/pdf/2507.17211) — arXiv 2507.17211, 7/2025

**Kiến trúc K5**

- [Alpha-R1](https://arxiv.org/html/2512.23515) — arXiv 2512.23515, bản 9/9/2026 · [`FinStep-AI/Alpha-R1`](https://github.com/FinStep-AI/Alpha-R1)
- [QuantEvolver / RFT](https://arxiv.org/html/2605.15412v1) — arXiv 2605.15412, 5/2026 · [`QuantLLM/QuantEvolver`](https://github.com/QuantLLM/QuantEvolver)

**Nền tảng tiến hóa**

- [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) — DeepMind
- [CodeEvolve](https://arxiv.org/html/2510.14150v1) — arXiv 2510.14150, bản mở nguồn, xác nhận MAP-Elites (biến thể CVT) là cần thiết để vượt AlphaEvolve

**Đã loại khỏi phạm vi** (LLM ở runtime)

- [TradingAgents](https://github.com/tauricresearch/tradingagents) · [Vibe-Trading](https://www.coddykit.com/pages/blog-detail?id=512921) · AI Hedge Fund · FinMem · FinAgent · FinCon · FinRL
