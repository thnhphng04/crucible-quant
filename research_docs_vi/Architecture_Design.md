# Crucible Quant — Architecture Design

> *Crucible Quant — phòng nghiên cứu định lượng dùng AI để tiến hóa chiến lược giao dịch hệ thống, và bắt mọi chiến lược qua lửa thử trước khi được giao dịch thật.*
>
> Thiết kế hệ thống AI sinh & kiểm định chiến lược trading deterministic, đa thị trường.
> Phiên bản: 0.5 (draft) · Ngày: 2026-09-21
> Nền tảng nghiên cứu: [[00-TONG-HOP-NGHIEN-CUU]] · [[04-TRUONG-PHAI-B-HIEU-QUA]] · [[06-PLATFORM-DA-THI-TRUONG]] · [[07-VALIDATION-LAYER]] · [[08-LLM-QUANT-RESEARCHER]]
>
> **Thay đổi 0.1 → 0.2** (nguồn: khảo sát 22 hệ trong [[08-LLM-QUANT-RESEARCHER]]): kiến trúc QuantEvolve được xác nhận độc lập bởi MadEvolve (§3.1) · thêm thay đổi bắt buộc **#5 DSL đóng** và **#6 reviewer ngữ cảnh mới tinh** (§3.1.6) · **DSR tính trên danh mục hợp nhất, không phải từng cell** (§3.1.6 — điểm dễ sai nhất) · thêm **gate ⓪ chống trôi spec** (§3.1.7) · thay khuyến nghị "dùng model nhỏ" bằng **bảng định tuyến dị thể + cảnh báo tránh reasoning model** (§3.1.9) · ledger thêm `model_used`/`gen_attempts`/`gen_failures`/`drift_delta` + view `starved_cells` (§4.1) · **P6 nâng lên cấp OS, evaluator chạy process riêng** (§4.2) · loại bỏ meta-evolution và RFT khỏi phạm vi (§9) · cảnh báo không lấy con số paper làm mốc (§11)
>
> **Thay đổi 0.2 → 0.3** (nguồn: review thiết kế 21/9/2026, 8 phát hiện — đều đã xác minh là đúng): holdout khóa theo **đợt nghiên cứu + danh mục đóng băng**, không theo từng strategy (§4.2) · tách **audit log** (mọi lời gọi LLM, mọi lỗi compile) khỏi **trial thống kê** (chỉ cấu hình đã đo hiệu suất); DSR dùng `N_eff` + phương sai Sharpe giữa các trial (§4.1) · thêm **§3.2.1 đặc tả xây dựng & chọn danh mục** · **đính chính PBO** — không tự tiến tới 1 khi N tăng; định nghĩa rõ tập cấu hình của CSCV (§3.2) · **sửa lỗi sizing chia volatility hai lần**; `Signal.risk_pct` → `Signal.strength` (§3.3, §3.4) · gate ⓪ có chuẩn hóa Levenshtein, trace neo cố định, chạy **sau** kiểm tra AST tĩnh (§3.1.7) · hạ các kết luận từ benchmark ngoại lai (reasoning model, MadEvolve) xuống mức **giả thuyết cần benchmark nội bộ** (§3.1, §3.1.9) · **§10 thành sổ quyết định duy nhất** — gộp 4 câu hỏi của [[00-TONG-HOP-NGHIEN-CUU]] mục 9, phân biệt *đã chốt / mặc định tạm / mở*; API `purgedcv` đánh dấu chưa kiểm chứng (§6)
>
> **Thay đổi 0.3 → 0.4** (nguồn: đọc repo MadEvolve, §3.1.10): **template strategy có vùng cố định + vùng tiến hóa**, cưỡng chế bằng hash (§3.3.1) · **tham số khai báo `TUNABLE`** — nguồn của lưới PBO và luật ≤ 6 tham số (§3.3.1, §3.2) · **hợp đồng evaluator tách metric public/private** (§3.3.2) · **đặc tả sandbox** chạy code sinh ra (§3.3.3) · Coding Team dùng **patch diff/viết lại block, strict, retry kèm lỗi** (§3.1.2) · **pipeline bất đồng bộ**, migration/curation tính theo số candidate (§3.1.8) · **biên bin feature map cố định** (§3.1.3) · test tích hợp island (§3.1.5, GĐ 2) · lần đánh giá của optimizer tham số **là trial** (§4.1)
>
> **Thay đổi 0.4 → 0.5** (nguồn: đọc toàn văn paper MadEvolve arXiv 2605.23007): **kiến trúc đa engine** — QuantEvolve 4 agent + vòng lặp đơn giản kiểu MadEvolve + random search chạy song song, chế độ cô lập/cộng tác (§3.1.11) · **tắt bộ tối ưu tham số trong vòng tiến hóa**, chỉ hiệu chỉnh một lần trước khi đóng băng (§3.3.1, §3.2.1) · **tiến hóa theo module**: vùng entry / exit+stop / regime (§3.3.1) · **ràng buộc số lệnh + thời gian nắm giữ tối thiểu** (gate ③) · **ràng buộc tương quan giữa indicator** (§3.3.1) · **đường cong suy giảm IS→OOS** trên CPCV (§3.2) · **fill model bi quan + kiểm tra độ nhạy chi phí** (§3.5, gate ⑥′) · **chạy nhiều seed** (§3.1.8)

---

## 0. Giả định — sửa nếu sai

Tài liệu này phải ra quyết định, nên mình chốt các giả định sau dựa trên toàn bộ cuộc thảo luận. **Nếu giả định nào sai, kiến trúc thay đổi đáng kể** — cột cuối nói rõ thay đổi gì.

| #            | Giả định                                                                                | Căn cứ                                    | Nếu sai thì sao                                                                                |
| ------------ | ------------------------------------------------------------------------------------------ | ------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **A1** | Output là**rule-based TA deterministic** (entry/exit/SL/TP), không phải ML factor | Xuyên suốt thảo luận                    | Nếu là ML factor → chuyển sang RD-Agent(Q) + Qlib, kiến trúc khác hẳn                    |
| **A2** | Phạm vi cuối:**crypto + forex + stock quốc tế + Việt Nam**                      | Yêu cầu trực tiếp                       | Nếu chỉ crypto → dùng Freqtrade, đơn giản hơn nhiều ([[05-SMOOTH-FLOW]])                                  |
| **A3** | **Không cần EA MQL5 — live trade qua NautilusTrader** | ✅ Người dùng xác nhận 21/9/2026 | Điều kiện: mọi venue đích có adapter live trên Nautilus (§3.5). Nếu sau này phải dùng broker chỉ hỗ trợ MT5 → viết **adapter cầu nối MT5** cho Nautilus, vẫn không cần EA |
| **A4** | LLM chỉ ở tầng**sinh code**, không chạy lúc runtime                            | Yêu cầu gốc của dự án                 | —                                                                                               |
| **A5** | Vốn giai đoạn đầu**< $10k**, mở rộng sau                                            | Suy từ bối cảnh cá nhân                | Nếu > $50k → mở thêm cross-sectional equity                                                  |
| **A6** | Chạy**local**, không phụ thuộc cloud                                             | Agent-loop cần hàng nghìn backtest       | Nếu chấp nhận cloud → QuantConnect đơn giản hơn                                          |
| **A7** | **Toàn bộ dữ liệu MIỄN PHÍ** — ràng buộc cứng                              | Yêu cầu trực tiếp                       | Nếu chi $270/năm (Norgate) → bỏ được module roll tự xây, tiết kiệm 2–3 tuần ở GĐ5 |

---

## 1. Nguyên tắc thiết kế — không thương lượng

Sáu nguyên tắc rút từ nghiên cứu. Mọi quyết định kỹ thuật phải phục tùng chúng.

| #            | Nguyên tắc                                                                     | Vì sao                                                                                       |
| ------------ | -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **P1** | **Harness trước, agent sau**                                             | Agent sinh nhanh hơn bạn kiểm tra. Không có harness = sản xuất rác ở tốc độ cao   |
| **P2** | **Mọi backtest đi qua ledger, không có đường vòng**                | `N` quyết định DSR. Nếu bypass được, bạn sẽ bypass ([[07-VALIDATION-LAYER]] mục 6)                       |
| **P3** | **Strategy nói bằng đơn vị TƯƠNG ĐỐI (`strength` + `stop_distance`), không phải lot/share/contract** | Điều kiện để cùng một code chạy trên 4 thị trường. Size là việc của tầng Risk (§3.4) |
| **P4** | **Vol targeting bắt buộc ở mọi vị thế**                              | Không có nó → 4 thị trường vẫn chỉ 1 bet, mất lợi ích breadth (Sharpe 0.4 → 1.6) |
| **P5** | **Backtest ≡ Live, cùng một code**                                      | Mỗi lần viết lại là một nguồn bug                                                      |
| **P6** | **Holdout ghi một lần, mở một lần cho mỗi đợt nghiên cứu — cho một danh mục đã đóng băng** | Nhìn rồi là cháy vĩnh viễn. Mở cho từng strategy = thêm một vòng chọn lọc (§4.2) |

---

## 2. Tổng quan hệ thống

```
╔═══════════════════════════════════════════════════════════════╗
║  AGENT LAYER — QuantEvolve       (LLM — chỉ ở đây)            ║
║                                                               ║
║   Island 1 ─┐                      ┌── Evolutionary DB ──┐   ║
║   Island 2 ─┤  migrate top 10%     │  Feature map (MAP-  │   ║
║   Island 3 ─┤  mỗi M gen           │  Elites, 16 bin×6D) │   ║
║      …    ─┘                       │  + Archive          │   ║
║                                    └──────────┬──────────┘   ║
║   mỗi generation, mỗi island:                 │              ║
║   SampleParent(α=.5) + Cousins(2best/3div/2rnd)              ║
║        ↓                                      │              ║
║   ② Research Agent  → hypothesis (6 tag XML)  │              ║
║        ↓                                      │              ║
║   ③ Coding Team     → code → backtest → refine│              ║
║        ↓            (⓪ drift check mỗi vòng)  │              ║
║   ④ Evaluation Team → analysis + insight ─────┘              ║
║      (reviewer ngữ cảnh MỚI TINH, có quyền phủ quyết)        ║
║                          ↓                                    ║
║                    Insight Repository (curate mỗi 50 gen)     ║
║   (① Data Agent chạy 1 lần lúc khởi tạo → N = C+1 island)    ║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓ strategy.py (deterministic)
╔═══════════════════════════════════════════════════════════════╗
║  VALIDATION LAYER        (gate rẻ → đắt, mọi kết quả → ledger)║
║  ①a AST/DSL  ⓪ Spec-drift  ①b Oracle  ② MinBTL  ③ BT IS     ║
║  ④ CPCV+PBO  ⑤ DSR(danh mục, N_eff trial thống kê)           ║
║  ⑥′ Data-source robustness  ⑥ Holdout(1 lần)  ⑦ Dry-run ⑧Live║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓
╔═══════════════════════════════════════════════════════════════╗
║  STRATEGY RUNTIME          (venue-agnostic)                   ║
║  Strategy → Signal(strength, stop_distance)                   ║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓
╔═══════════════════════════════════════════════════════════════╗
║  RISK & SIZING LAYER       ★ quan trọng hơn Adapter ★         ║
║  Vol targeting → Position sizing → FX conversion              ║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓
╔═══════════════════════════════════════════════════════════════╗
║  EXECUTION CORE            NautilusTrader (backtest ≡ live)   ║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓
╔═══════════════════════════════════════════════════════════════╗
║  ADAPTERS   Binance✅ Bybit✅ IB✅ Databento✅ SSI🔴tự viết   ║
╚═══════════════════════════════════════════════════════════════╝

        ┌──────────────────────────────────────────┐
        │  LEDGER (SQLite→Postgres)                │
        │  Mọi trial. Không reset. Không bypass.   │
        └──────────────────────────────────────────┘
```

---

## 3. Đặc tả từng tầng

### 3.1. Agent Layer — theo kiến trúc QuantEvolve (arXiv 2510.18569)

Thay cho thiết kế Generator/Critic/Scheduler trước đó, tầng agent dùng kiến trúc **quality-diversity evolutionary** của QuantEvolve: **feature map (MAP-Elites) + island model + 4 agent**.

> 📖 Nguồn: Yun, Lee & Jeon — *QuantEvolve: Automating Quantitative Strategy Discovery through Multi-Agent Evolutionary Framework*, AI Tech Lab, Qraft Technologies. Đọc trực tiếp từ PDF gốc. Đánh giá phê phán: [[00-TONG-HOP-NGHIEN-CUU]].

> ℹ️ **Hội tụ thiết kế độc lập (cập nhật 21/9/2026).** **MadEvolve** (arXiv 2605.23007, 5/2026) dựng lại gần như y hệt — MAP-Elites grid + **island 5 đảo** + **migration 10% mỗi 5 generation** + inspiration retrieval (chính là "cousins") — trên một domain hoàn toàn khác (thực thi lệnh Bitcoin), **mà không trích dẫn QuantEvolve**. Con số migration 10% trùng khít.
>
> ⚠️ **Giới hạn của bằng chứng này (v0.3):** hai nhóm độc lập chọn cùng kiến trúc cho thấy nó **khả thi về kỹ thuật và là lựa chọn tự nhiên** — **không** chứng minh nó hiệu quả cho bài toán của ta. Cả hai paper đều self-reported, chưa qua audit, và không có đối chứng cho rule-based TA đa thị trường. Coi đây là **giả thuyết làm việc**; tiêu chí xác nhận nội bộ nằm ở GĐ 2 của roadmap (so với baseline random-search cùng ngân sách trial). Toàn cảnh 5 kiến trúc của ngành: [[08-LLM-QUANT-RESEARCHER]].
>
> 🔍 **Đối chiếu repo (21/9/2026):** code công khai [`tianyi-stack/MadEvolve`](https://github.com/tianyi-stack/MadEvolve) **không chạy island model** (parent luôn lấy từ top-20 toàn cục; code island tồn tại nhưng không được gọi) và không có code domain trading. Bằng chứng hội tụ vì vậy **yếu hơn** mức mô tả ở trên — nó chỉ nằm trong văn bản paper. Những gì đáng học (và không nên chép) từ repo: §3.1.10.

#### 3.1.1. Vòng lặp chính (Algorithm 1)

```
Require: N islands, G generations, M migration interval,
         K insight curation interval, feature dims 𝒟, bin sizes ℬ,
         exploitation-exploration balance α ∈ [0,1]
Notation: h (hypothesis), c (code), m (backtest results), a (analysis)

 1: Khởi tạo feature map ℱ với chiều 𝒟, bin ℬ
 2: Khởi tạo evolutionary database 𝒟ℬ (feature map + archive)
 3: Khởi tạo N island với seed strategy bằng DataAgent
 4: Khởi tạo insight repository ℐ ← ∅
 5: for generation g = 0 … G-1 do
 6:   for each island Iᵢ do
 7:     s_p ← SampleParent(Iᵢ, 𝒟ℬ, α)          # Eq. 1
 8:     C   ← SampleCousins(s_p, Iᵢ, 𝒟ℬ)       # Eq. 2
 9:     h   ← ResearchAgent(s_p, C, ℐ)
10:     (c, m) ← CodingTeam(h, 𝒟ℬ, C)
11:     a   ← EvaluationTeam(h, c, m)
12:     s_new ← (h, c, m, a)
13:     f   ← ComputeFeatures(s_new)
14:     UpdateDatabase(s_new, f, 𝒟ℬ, Iᵢ)
15:     ℐ   ← ℐ ∪ {a}
16:   end for
17:   if g mod M = 0 and g > 0 then
18:     MigrateStrategies(...)      # top 10% mỗi island sang island láng giềng
19:   if g mod K = 0 and g > 0 then
20:     ManageInsights(ℐ)           # lọc trùng, hợp nhất insight
21: end for
```

#### 3.1.2. Bốn agent

| Agent                        | Khi nào chạy              | Nhiệm vụ                                                                                                                                                                                                                                                                                                                                                               |
| ---------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **① Data Agent**      | Khởi tạo,**1 lần** | Phân tích cấu trúc data universe (format, schema, cột, metadata) → sinh**Data Schema Prompt**. Song song: xác định `C` **strategy category khả thi** từ data có sẵn (momentum, arbitrage, breakout, seasonality, mean-reversion…). Coding Team sinh seed strategy cho mỗi category + 1 buy-and-hold benchmark → **N = C + 1 island** |
| **② Research Agent**  | Mỗi generation             | Sinh hypothesis mới từ: (1) hypothesis/code/kết quả/analysis của**parent + cousins**, (2) Data Schema Prompt, (3) **insight tích lũy** từ các generation trước                                                                                                                                                                                    |
| **③ Coding Team**     | Mỗi generation             | Hypothesis → Python. 4 giai đoạn: Initial Implementation → Backtesting →**Iterative Refinement** → Performance Reporting. Chỉ viết vào **vùng tiến hóa** của template (§3.3.1). Refine bằng **hai chế độ patch**: SEARCH/REPLACE (mặc định ~70%) và viết lại cả vùng tiến hóa (~30%, tăng dần khi đình trệ); áp patch **strict** — một khối lỗi thì cả patch lỗi; lỗi → **retry nhiều lượt kèm thông báo lỗi**, tối đa 3. Chạy trong **sandbox** (§3.3.3) |
| **④ Evaluation Team** | Mỗi generation             | 5 chức năng: Hypothesis Analysis → Code Analysis → Backtest Analysis → Insight Extraction →**Insight Management** (mỗi 50 gen: lọc trùng, hợp nhất). 🆕 Chạy với **ngữ cảnh mới tinh** — xem chú thích dưới                                                                                                                             |

**Hypothesis phải có đúng 6 thành phần** (Research Agent xuất ra XML-like tag):

```xml
<hypothesis>       Phát biểu kiểm chứng được, có nền tảng lý thuyết tài chính
<rationale>        Vì sao hình thành hypothesis này — dẫn chiếu khái niệm/indicator cụ thể
<objectives>       Mục tiêu định lượng, dùng làm tiêu chí đánh giá
<expected_insights> Học được gì nếu hypothesis đúng/sai
<risks_limitations> Rủi ro: data bias, overfitting, điều kiện thị trường bất thường
<next_step_ideas>  Hướng biến thể/mở rộng cho generation sau
```

> 🔑 Đây là cơ chế chống **post-hoc rationalization**: hypothesis viết **trước** code, và Evaluation Team chấm xem code có thực sự triển khai đúng hypothesis không.

> 🆕 **Evaluation Team phải có ngữ cảnh mới tinh** (theo AgonAlpha — [[08-LLM-QUANT-RESEARCHER]] §5.3).
>
> Nó nhận **đúng ba thứ**: (1) hypothesis đã đóng băng, (2) code cuối cùng, (3) kết quả backtest thô.
>
> Nó **KHÔNG** nhận: lịch sử các vòng refine, các lần thử thất bại, hay bất kỳ lời giải thích nào từ Coding Team.
>
> **Vì sao:** một agent nhìn thấy quá trình tìm kiếm sẽ bị thuyết phục bởi câu chuyện mà Coding Team tự kể — *"đã thử 5 cách, cách này tốt nhất"* nghe như bằng chứng, nhưng đó chính là best-of-N. Đây là **temporal isolation áp dụng cho chính agent**, không chỉ cho dữ liệu.
>
> Kèm theo: Evaluation Team có **quyền phủ quyết tuyệt đối** — nếu phát hiện số liệu trong báo cáo không khớp khi chạy lại, verdict = `REJECT_FABRICATION`, ghi ledger, không có đường kháng cáo.

#### 3.1.3. Feature map — MAP-Elites

**6 chiều** (Table 1 của paper), mỗi ô giữ **strategy tốt nhất** cho vector đặc trưng đó:

| Chiều            | Mô tả                                                                                             |
| ----------------- | --------------------------------------------------------------------------------------------------- |
| Strategy Category | Momentum, mean-reversion, arbitrage… —**mã hóa nhị phân** (momentum+mean-rev = `101`) |
| Trading Frequency | Số giao dịch mỗi kỳ                                                                             |
| Maximum Drawdown  | Sụt giảm đỉnh-đáy lớn nhất                                                                  |
| Sharpe Ratio      | Return điều chỉnh rủi ro                                                                        |
| Sortino Ratio     | Chỉ phạt downside                                                                                 |
| Total Return      | Lợi nhuận tích lũy                                                                              |

> ⚠️ **Ablation quan trọng — dùng 16 bin, không ít hơn.** Cấu hình 16-bin cải thiện liên tục (SR 1.52, CR 256% ở gen 150); **1-bin và 4-bin hội tụ sớm rồi trì trệ** (SR tụt về 1.12 và 1.06). Ít bin → các strategy khác hẳn nhau bị ép cạnh tranh cùng một ô → mất diversity.
>
> 🆕 **Biên bin cố định (v0.4).** Biên của từng chiều liên tục (Sharpe, MDD, tần suất…) khai báo trong `evaluation.lock.yaml` **trước khi đợt bắt đầu**; giá trị vượt biên rơi vào bin đầu/cuối. **Không** để biên tự giãn theo giá trị quan sát như MadEvolve (§3.1.10, X4): khi biên đổi, elite cũ nằm sai ô mà không ai biết.
>
> ⚠️ **Bắt buộc giữ chiều Strategy Category.** Không có nó, bin chiếm ưu thế nuốt **46.3%** toàn bộ strategy; có nó thì phân bố cân bằng (top bin 17.9%).

**Mở rộng cho dự án đa thị trường** (paper nói rõ feature map là extensible):

| Chiều thêm                       | Lý do                                                                          |
| ---------------------------------- | ------------------------------------------------------------------------------- |
| **Asset class**              | crypto / forex / equity index / futures — tránh mọi strategy dồn về crypto |
| **Holding period**           | intraday / swing / position                                                     |
| **Roll yield** *(futures)* | paper gợi ý cho futures                                                       |

#### 3.1.4. Parent & Cousin sampling

**Parent** — chọn ngẫu nhiên một island, rồi một trong hai cách (Eq. 1):

```
P(s_p = s) = α / |M_I|     nếu s ∈ M_I   (best parent — từ feature map)
             (1-α) / |I|    nếu s ∈ I     (diverse parent — toàn bộ population)
```

α cao → sức ép chọn lọc mạnh; α thấp → đa dạng hơn. **Paper dùng α = 0.5.**

**Cousins** — 3 loại, để làm giàu ngữ cảnh cho Research Agent:

| Loại                     | Cách chọn                                      | Số lượng |
| ------------------------- | ------------------------------------------------ | ----------- |
| **Best Cousins**    | Strategy hiệu năng cao từ island của parent  | **2** |
| **Diverse Cousins** | Gần parent trong**feature space** (Eq. 2) | **3** |
| **Random Cousins**  | Ngẫu nhiên đều từ island của parent        | **2** |

Diverse cousin sinh bằng cách nhiễu loạn vector đặc trưng của parent (Eq. 2):

```
f_c^d = ⌊𝒩(f_p^d, σ_d²)⌋        nếu chiều d liên tục      (σ_d = 1.0)
        BitFlip(f_p^d, k_bf)      nếu d là strategy category (k_bf = n/4)
```

#### 3.1.5. Island model & migration

- `N = C + 1` island, mỗi island khởi tạo bằng **một seed strategy khác category**
- Tiến hóa **độc lập** ở giai đoạn đầu → phát triển chuyên môn sâu theo từng hướng
- Mỗi `M` generation: **migrate top 10% mỗi island sang island láng giềng**
- 🆕 **Phải chứng minh island thực sự chạy** (bài học MadEvolve, §3.1.10 X3 — code island tồn tại nhưng không được gọi): log island của mỗi parent; test tích hợp khẳng định (a) parent được lấy từ island đang xử lý, (b) migrant không bị nhân bản trùng ở island đích, (c) con của migrant thuộc island đích
- Hiệu ứng: trọng tâm dịch dần từ *chiều sâu trong từng category* sang *chiều rộng xuyên toàn bộ không gian*

#### 3.1.6. 🔴 Sáu thay đổi bắt buộc so với paper

QuantEvolve tự thừa nhận: *"strategies generated by the framework may be susceptible to data snooping bias. Future work will integrate more sophisticated and rigorous validation methodologies."* Đó chính là lỗ hổng dự án này tồn tại để vá.

| #           | Paper làm gì                                                                                                                     | Ta phải đổi thành gì                                                                                                                                                      | Vì sao                                                                                                                                                                                     |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1** | `Score = SR + IR + MDD` (trọng số 1:1:1), **không deflate** | `Score = DSR(returns, N_eff_hiện_tại) − λ₁·AST_sim − λ₂·n_params`, **loại nếu PBO ≥ 0.5** (PBO trên lưới tham số của chính ứng viên, §3.2). ⚠️ Đây là **điểm xếp hạng để tìm kiếm**, không phải gate — gate DSR thật nằm ở ⑤, trên danh mục | 150 generation × N island = hàng nghìn trial. Không deflate thì Score chỉ đo may mắn ([[07-VALIDATION-LAYER]]) |
| **2** | Chống look-ahead**chỉ bằng prompt**: *"The strategy must avoid Lookahead Bias… This is the most important constraint"* | **Structural guardrail**: AST scan chặn `.shift(-n)`, whitelist indicator, leaky-oracle test suite                                                                    | Prompt không phải cơ chế cưỡng chế. LLM vẫn sinh code nhìn tương lai                                                                                                             |
| **3** | Không đếm trial | **Hai sổ tách bạch:** (a) **audit log** ghi *mọi* hoạt động — lời gọi LLM, lỗi compile, AST reject; (b) **trial thống kê** = mỗi cấu hình đã được **đo hiệu suất** trên dữ liệu (kể cả bị feature map từ chối sau khi backtest) | `N` quyết định DSR. Strategy đã backtest rồi bị loại vẫn là trial (P2). Nhưng một lời gọi reviewer hay một file không compile **không phải** phép thử trên dữ liệu — đếm chúng vào `N` làm DSR sai theo hướng khác (§4.1) |
| **4** | Zipline:`initialize(context)` + `handle_data(context, data)`                                                                   | **`Signal(strength, stop_distance)`** trên NautilusTrader                                                                                                             | P3 + P5 — venue-agnostic, backtest ≡ live                                                                                                                                                 |
| **5** | Code Python tự do, LLM tự quyết dùng gì                                                                                       | **DSL bị ràng buộc** + engine tất định giữ rule đánh giá. Agent **không được sửa** data split, ngưỡng gate, hay định nghĩa metric trong phiên | Thu hẹp không gian tìm kiếm ⇒**tốn ít trial hơn** ⇒ ngưỡng DSR dễ thở hơn. Và là guardrail *cấu trúc*, không phải lời hứa (Crypto Constrained Agents, [[08-LLM-QUANT-RESEARCHER]] §7.3) |
| **6** | Evaluation Team thấy toàn bộ lịch sử tìm kiếm                                                                               | **Reviewer ngữ cảnh mới tinh** + **quyền phủ quyết**                                                                                                         | Agent thấy lịch sử sẽ bị thuyết phục bởi câu chuyện mà Coding Team tự kể. Đây là*temporal isolation áp dụng cho chính agent* (AgonAlpha, [[08-LLM-QUANT-RESEARCHER]] §5.3)                       |

> 🔴 **Điểm chết người về cách tính DSR — đọc kỹ.**
>
> Feature map sinh ra hàng trăm ô, mỗi ô một strategy. Cám dỗ tự nhiên là tính DSR **riêng cho từng ô** rồi giữ những ô qua ngưỡng. **Làm thế là selection bias quay lại qua cửa sau** — bạn vừa chọn best-of-N một lần nữa, đúng thứ mà DSR sinh ra để chặn.
>
> **Quy tắc đúng:** DSR tính trên **chuỗi lợi suất của danh mục hợp nhất** — danh mục được dựng theo **quy tắc đăng ký trước** ở §3.2.1, không phải chọn tay — với đầu vào thống kê lấy từ **sổ trial thống kê** (§4.1), không phải audit log:
>
> - `N_eff` = số trial **độc lập hiệu dụng**, ước lượng bằng gom cụm chuỗi returns của mọi trial đã đo hiệu suất (Bailey & López de Prado phân biệt rõ số trial *thực tế* với số trial *độc lập*). `N_raw` luôn được báo cáo kèm; nếu chưa ước lượng được `N_eff` thì dùng `N_raw` (bảo thủ).
> - `V[SR]` = phương sai Sharpe **giữa các trial** — DSR cần đại lượng này để tính Sharpe kỳ vọng tối đa dưới giả thuyết không.
> - **Cộng thêm số phương án danh mục đã thử** (`portfolio_variants`, §3.2.1). Đổi quy tắc chọn ô hay trọng số rồi chạy lại cũng là selection.
>
> Lý do sâu hơn: mục đích của feature map trong dự án này **không phải** tìm một strategy tốt nhất, mà là **lấp ~20 ô ít tương quan để lấy breadth** (Sharpe 0.4 → 1.6, [[04-TRUONG-PHAI-B-HIEU-QUA]]). Nếu output là một danh mục thì đối tượng kiểm định cũng phải là danh mục đó. ⚠️ Nhưng chỉ đổi chuỗi returns sang danh mục **chưa đủ** chứng minh đã kiểm soát selection bias — cần cả quy tắc dựng danh mục cố định lẫn việc đếm các phương án danh mục đã thử.
>
> **Hệ quả về ngân sách:** Alpha-R1 (9/2026) — hệ duy nhất trong 22 hệ khảo sát dám báo cáo DSR — chỉ đạt **DSR 0.39–0.85 với 34–158 trial**, tức **trượt ngưỡng 0.95**, dù train trên 64×H800 trong 120 giờ. Kế hoạch của ta là **4.500–9.000 *lời gọi LLM*** cho một lần chạy đầy đủ (§3.1.8). Số trial thống kê sẽ ít hơn (một strategy tốn nhiều lời gọi; nhiều lần sinh code thất bại không bao giờ tới backtest), và `N_eff` còn ít hơn nữa vì các trial trong cùng ô tương quan mạnh — nhưng vẫn ở cỡ **hàng trăm đến hàng nghìn**, gấp nhiều lần Alpha-R1.
>
> ⇒ **Số trial là ngân sách khan hiếm gắt nhất của dự án — gắt hơn tiền và thời gian.** Mọi cơ chế giảm số trial cần thiết (insight repository, DSL hạn chế, seed tốt) có giá trị **gấp đôi**: vừa tiết kiệm chi phí, vừa hạ ngưỡng Sharpe phải vượt.

**Giữ nguyên từ paper** (những ràng buộc này đã tốt, chuyển thẳng sang template của ta):

- *"When implementing trading logic, you can only use past and present data"*
- *"You have to order less than or equal to the available cash"*
- *"Properly handle exceptions and edge cases (e.g., insufficient data, contract rollover)"*
- Phí/slippage cài trong `initialize` chứ không để trong logic strategy
- Code chạy trong **subprocess cô lập**, lỗi feed ngược để patch (không rewrite toàn bộ) — 🆕 cô lập nghĩa là **sandbox** theo §3.3.3, không chỉ là một process riêng

**Ràng buộc bổ sung của ta:**

- Chỉ dùng indicator trong **whitelist** (đã kiểm tra không repaint)
- Chỉ đọc bar **đã đóng**
- Rule **scale-invariant** — return/ATR/z-score, **không dùng mức giá tuyệt đối** (để roll continuous contract không phá logic)
- **Số tham số tự do ≤ 6** (complexity control kiểu AlphaAgent) — 🆕 đếm bằng khai báo `TUNABLE` (§3.3.1); hằng số số học không khai báo trong vùng tiến hóa ⇒ AST reject
- 🆕 **DSL đóng:** strategy chỉ được ghép từ tập toán tử đã đăng ký (`indicator/*`, `compare`, `cross`, `and/or/not`, `atr_mult`). Mọi lời gọi ngoài DSL bị AST reject **trước khi chạy** — không đợi backtest
- 🆕 **Engine tất định giữ rule đánh giá:** data split, ngưỡng gate, định nghĩa metric nằm trong file config **đọc-chỉ, hash trước mỗi phiên**. Agent không có đường ghi vào đó
- 🆕 **Trace append-only:** mọi thí nghiệm ghi thêm, không bao giờ sửa/xóa

> ⚠️ **Rủi ro chưa xử lý trong thiết kế hiện tại: feature map sẽ lấp lệch về phía dễ.**
>
> SysTradeBench đo được tương quan **Spearman ρ = −0.68 (p < 0.05)** giữa độ phức tạp strategy và tỉ lệ sinh code hợp lệ: Double MA Crossover **88.2%**, Index Enhancement **35.3%**.
>
> Feature map của ta có chiều `strategy category`, và mục đích của nó là đẩy hệ về phía đa dạng — tức ngày càng phức tạp. Nhưng tỉ lệ sinh code hợp lệ **sụt theo độ phức tạp**. Hệ quả: các ô ứng với strategy phức tạp sẽ **rỗng lâu hơn nhiều, không phải vì chúng kém mà vì code không chạy được**. Feature map sẽ *trông như* đã khám phá đủ trong khi thực ra chỉ lấp được vùng dễ.
>
> **Cách xử lý:** ledger ghi `gen_attempts` và `gen_failures` theo từng cell (§4.1). Cell có tỉ lệ thất bại sinh code **> 50%** là tín hiệu **cấp thêm ngân sách refine cho cell đó**, không phải kết luận rằng vùng đó không có strategy tốt.

#### 3.1.7. Chống trôi spec giữa các vòng refine

Coding Team có 4 giai đoạn, trong đó **Iterative Refinement** chạy nhiều vòng. Rủi ro: agent lặng lẽ **sửa chính hypothesis** cho khớp với code đang chạy được, thay vì sửa code cho khớp hypothesis. Đó là post-hoc rationalization ở dạng tinh vi nhất — và 6 tag XML **không chặn được nó**, vì agent được phép viết lại tag.

Cơ chế hai tầng (ý tưởng theo SysTradeBench; v0.3 bổ sung phần đặc tả mà paper không nêu):

```
Tầng 1 — checksum tĩnh (KHÔNG chạy code)
    SHA256 trên các trường logic lõi đã chuẩn hóa: entry rule, exit rule, giá trị tham số
    Đổi ⇒ phải khai báo trong <change_log>, không được âm thầm

    ── ①a AST/DSL guardrail chạy Ở ĐÂY, trước tầng 2 ──
    Code chưa qua kiểm tra tĩnh thì KHÔNG được thực thi, kể cả trên micro-scenario

Tầng 2 — hồi quy trace (chạy trong sandbox, sau ①a)
    Bộ micro-scenario: cố định, versioned trong evaluation.lock.yaml (~20 chuỗi bar tổng hợp:
        trend lên/xuống, sideway, gap, spike, thiếu bar)
    Trace: mỗi bar → một token ∈ {L, S, F, X} (long / short / flat / exit), nối tất cả scenario
    Trace tham chiếu: NEO CỐ ĐỊNH = trace của bản cài đặt đầu tiên của hypothesis
        (không so với vòng trước — so với vòng trước cho phép trôi từng bước nhỏ,
         mỗi bước < 0.05 nhưng cộng dồn thành một strategy khác hẳn)
    Δ = Levenshtein(trace_mới, trace_neo) / max(len(trace_mới), len(trace_neo))   ∈ [0, 1]

    Δ < 0.05         → sửa lỗi kỹ thuật, cho phép
    0.05 ≤ Δ < 0.15  → gắn cờ, cần người xem
    Δ ≥ 0.15         → phân kỳ đáng ngờ → HỦY vòng lặp, ghi ledger verdict REJECT_DRIFT
```

> Đây là **gate ⓪** — chạy *trong* agent loop mỗi vòng refine. Thứ tự đúng: **⓪ tầng 1 → ①a AST/DSL tĩnh → ⓪ tầng 2 (sandbox) → ①b leaky-oracle test**. Bản 0.2 đặt ⓪ trước toàn bộ ① — sai, vì tầng 2 phải *chạy* code.
>
> ⚠️ Ngưỡng 0.05/0.15 là **mặc định tạm**. Chúng chỉ có nghĩa khi Δ đã chuẩn hóa như trên; phải hiệu chỉnh ở GĐ 2 trên một bộ sửa đổi đã gán nhãn tay (sửa bug thuần vs đổi logic) trước khi dùng để tự động hủy.

#### 3.1.8. Tham số vận hành

| Tham số                | Giá trị paper               | Ta dùng   | Ghi chú                                                  |
| ----------------------- | ----------------------------- | ---------- | --------------------------------------------------------- |
| Bin mỗi chiều         | **16**                  | 16         | Ablation: 1/4 bin hội tụ sớm                           |
| α (exploit/explore)    | 0.5                           | 0.5        |                                                           |
| σ_d (diverse cousin)   | 1.0                           | 1.0        |                                                           |
| k_bf (bit flip)         | n/4                           | n/4        | n = độ dài bit của category                           |
| Cousins/parent          | 2 best + 3 diverse + 2 random | như paper |                                                           |
| Migration interval`M` | —                            | 10 gen     | Paper không nêu rõ; 10 là điểm khởi đầu hợp lý. 🆕 Với pipeline bất đồng bộ, "1 gen" = **N island candidate đã đánh giá xong** — migration và curation (`K`) đếm theo số candidate, không theo đồng hồ |
| Insight curation`K`   | **50**                  | 50         |                                                           |
| Generations`G`        | 150 (equity), 100 (futures)   | ≥150      | Gen 150 mới đạt SR 1.52                                |
| LLM inference/cycle     | **5–10**               | —         | Định mức để ước chi phí                           |
| 🆕 Luồng sinh LLM song song | — | 1–2 (GPU local) | Pipeline bất đồng bộ: thread LLM đẩy candidate vào hàng đợi prefetch, slot backtest (CPU) chạy song song |
| 🆕 Slot backtest song song | — | = số core − 1 | |
| 🆕 Số seed mỗi cấu hình engine | — | ≥ 3 | Paper MadEvolve §6.3: chỉ đổi prompt một chút, cải thiện OOS từ +627% tụt còn +44%. Báo cáo **phân phối** qua các seed; mọi seed cộng vào `N` |

> ⚠️ **Chi phí:** 5–10 LLM inference × 150 generation × N island. Với N=6 island → **4.500–9.000 lần gọi LLM** cho một lần chạy đầy đủ. Paper tự nêu đây là giới hạn scalability.
>
> ⚠️ **Đây là số lời gọi LLM, không phải số trial.** Một strategy tốn nhiều lời gọi (research, code, refine, review); lời gọi nào không dẫn tới một cấu hình được đo hiệu suất thì chỉ vào audit log. `N` cho DSR lấy từ sổ trial thống kê (§4.1).

#### 3.1.9. Định tuyến model dị thể

> 🔴 **Đính chính (21/9/2026).** Phiên bản trước của mục này khuyên "dùng model nhỏ cho phần lớn lời gọi". **Khuyến nghị đó sai ở hai tầng** — xem [[08-LLM-QUANT-RESEARCHER]] mục 6.
>
> **Sai tầng 1:** ensemble của paper là **Qwen3-30B-A3B**, một model MoE chỉ kích hoạt **3.3B tham số/token**. Paper *vốn đã* chạy ở chi phí suy luận 3B. Không có khoản tiết kiệm nào để giành thêm.
>
> **Sai tầng 2 (nghiêm trọng hơn):** ablation của CogAlpha cho thấy **Llama3-8B sinh factor có RankIC = −0.0074** — sai dấu một cách hệ thống, tệ hơn không làm gì. Và `gpt-oss-20B` còn **kém hơn** Llama3-8B về IC. **Số tham số không phải trục đúng.**

**Phát hiện phản trực giác cần thiết kế theo:**

| Model                    | IC               | RankIC             | Nguồn   |
| ------------------------ | ---------------- | ------------------ | -------- |
| Llama3 8B                | 0.0121           | **−0.0074** | CogAlpha |
| gpt-oss-20B              | 0.0061           | 0.0075             | CogAlpha |
| **gpt-oss-120B**   | **0.0300** | **0.0318**   | CogAlpha |
| GPT-4.1                  | 0.0118           | 0.0114             | CogAlpha |
| **o3** (reasoning) | **0.0019** | **−0.0050** | CogAlpha |

> 🔑 **Model reasoning mạnh nhất lại tệ nhất — trên benchmark này.** Một cách giải thích *hợp lý* (nhất quán với lý lẽ của QuantEvolver về *"stable generation preferences"* gây đình trệ tìm kiếm):
>
> Nhiệm vụ của Research Agent **không phải** tìm câu trả lời đúng. Nó là **sinh nhiều giả thuyết đa dạng, phần lớn sẽ sai**, để cơ chế chọn lọc làm việc. Model reasoning được tối ưu để *hội tụ* — mà ở đây hội tụ **chính là** mode collapse. Model càng "chắc chắn", feature map càng rỗng.
>
> Đây cũng là lý do sâu xa MAP-Elites tồn tại: nó là **cơ chế chống lại xu hướng hội tụ tự nhiên của LLM**.
>
> ⚠️ **Giới hạn bằng chứng (v0.3):** CogAlpha là benchmark **sinh factor cross-sectional trên CSI300** (trường phái A), chỉ một lần chạy mỗi model. Nó **không** chứng minh nguyên nhân là mode collapse, và **không** chứng minh kết quả chuyển sang rule-based TA đa thị trường. Vì vậy các khuyến nghị dưới đây là **mặc định ban đầu + giả thuyết**, sẽ được A/B nội bộ ở GĐ 2 (đo: độ phủ feature map, tỉ lệ code hợp lệ, phân bố Sharpe IS trên cùng ngân sách trial).

**Bảng định tuyến:**

| Agent                        | Model                                                                                                                        | Lý do                                                      |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| **① Data Agent**      | Context window lớn nhất trong tầm tay                                                                                     | Chỉ chạy 1 lần, phải đọc toàn bộ schema             |
| **② Research Agent**  | Model mạnh, **mặc định non-reasoning**. `gpt-oss-120b` là điểm khởi đầu có bằng chứng (ngoại lai). Reasoning model là **nhánh A/B**, không bị cấm | Cần đa dạng, không cần hội tụ — giả thuyết từ CogAlpha, chờ kiểm chứng nội bộ       |
| **③ Coding Team**     | Model chuyên code,**tầng Mid** theo SysTradeBench (GLM-4.6, DeepSeek-V3, Claude Sonnet-class)                        | 90–95% chất lượng tầng Top với 40–50% chi phí       |
| **④ Evaluation Team** | Tầng Mid +**constrained decoding**                                                                                    | Chấm điểm theo schema cố định, không cần sáng tạo |

> ⚠️ **Mặc định: không dùng dense model < 10B chưa fine-tune** (RankIC âm ở CogAlpha). Cùng giới hạn bằng chứng như trên — mở lại nếu benchmark nội bộ cho kết quả khác.
> ⚠️ **Không làm RFT/RL fine-tuning.** Alpha-R1 tốn 64×H800 × 120 giờ — ngoài tầm A6 (chạy local).
>
> Lưu ý một mâu thuẫn hữu ích: **o3 nằm tầng Top ở SysTradeBench** (viết code đúng spec) nhưng **bét bảng ở CogAlpha** (nghĩ ra factor tốt). Không mâu thuẫn — hai kỹ năng khác nhau. Đó chính là lý do phải định tuyến dị thể thay vì chọn "model tốt nhất".

#### 3.1.10. 🆕 Bài học từ repo MadEvolve

> Nguồn: đọc trực tiếp [`tianyi-stack/MadEvolve`](https://github.com/tianyi-stack/MadEvolve) (commit `8b881d3`, 3/3/2026, ~12.6k dòng Python, MIT) ngày 21/9/2026. Đã đọc: `engine/orchestrator.py`, `repository/topology/*`, `repository/selection/*`, `synthesizer/composer.py`, `transformer/{blocks,patcher,parallel}.py`, `provider/strategy/allocation.py`, `executor/runners/native.py`, `engine/configuration.py`. Chưa đọc: `analyzer/*`, runner SLURM, prompt templates.

**Nên học — năm cơ chế, ✅ đã đưa vào đặc tả ở v0.4** (L1, L2 → §3.3.1 · L3 → §3.3.2 · L4 → §3.1.2 · L5 → §3.1.8):

| # | Cơ chế trong repo | Áp vào ta như thế nào |
|---|---|---|
| **L1** | **`EVOLVE-BLOCK-START/END`**: chỉ vùng giữa hai marker được sửa; phần hạ tầng bên ngoài giữ nguyên | Template strategy chia 2 vùng: **ngoài block** = hợp đồng `Signal`, phí/slippage, truy cập dữ liệu (cố định); **trong block** = logic tín hiệu. ⚠️ Repo chỉ *dặn* bằng prompt — ta **cưỡng chế**: hash(prefix + suffix) phải không đổi sau mỗi patch, lệch ⇒ `AST_REJECT` |
| **L2** | **`# TUNABLE: name = v, bounds=(a, b)`** — khai báo tham số tự do kèm biên | Chính là thứ §3.2 cần: **lưới PBO lấy thẳng từ khai báo TUNABLE**; `n_params` cho complexity penalty đếm từ đây; luật ≤ 6 tham số cưỡng chế ở đây. Tham số không khai báo mà là hằng số trong block ⇒ reject |
| **L3** | **`public_metrics` vs `private_metrics`**: evaluator trả hai nhóm; chỉ public vào prompt, private chỉ lưu | Mọi thứ ngoài IS (đường CPCV, PBO, kết quả robustness) là **private** — agent không bao giờ thấy. ⚠️ Kênh `text_feedback` của evaluator cũng đi thẳng vào prompt ⇒ phải sinh **chỉ từ public metrics** |
| **L4** | **Patch hai chế độ**: SEARCH/REPLACE (~70%) + viết lại cả block (~30%), tăng tỉ lệ viết lại khi đình trệ; lỗi patch → **retry nhiều lượt kèm thông báo lỗi**, tối đa 3 | Dùng cho Coding Team (Iterative Refinement). Chế độ diff tự nhiên giữ Δ drift nhỏ (§3.1.7). ⚠️ Repo cho áp **một phần** patch (block lỗi bị bỏ qua) — ta dùng `strict=True`: một block lỗi ⇒ cả patch lỗi |
| **L5** | **Pipeline bất đồng bộ**: thread sinh LLM đẩy candidate vào hàng đợi prefetch; slot đánh giá chạy song song; "generation" = bộ đếm ngân sách | Hợp với chạy local: LLM (GPU) và backtest (CPU) không chờ nhau. Khi đó migration/insight curation phải định nghĩa theo **số candidate đã đánh giá**, không theo generation đồng bộ |

**Không được chép — bảy điểm yếu, mỗi điểm vi phạm một nguyên tắc của ta** (✅ biện pháp chặn đã có trong đặc tả: X1, X2 → §4.1 · X3 → §3.1.5 + GĐ 2 · X4 → §3.1.3 · X5 → §3.3.3 · X6 → §3.1.9 · X7 → §3.2):

| # | Điểm yếu trong code | Vi phạm | Ta làm khác |
|---|---|---|---|
| **X1** | Candidate đánh giá **thất bại không được ghi** vào store — chỉ in ra màn hình | P2 | Mọi thất bại vào `generation_log` (§4.1) |
| **X2** | Inner-loop optimizer gọi evaluator hàng chục lần/candidate (`generation=-1`) mà **không ghi lại** — trial ẩn | P2, DSR | Mỗi lần optimizer đánh giá là một dòng `trials` — nó *là* trial thống kê |
| **X3** | **Island model là code chết**: `get_selection_pool()` trả `[]`, parent luôn từ top-20 toàn cục. Migration còn tích tụ bản sao trùng lặp, và con của migrant quay về đảo nguồn | — | Phải có **test tích hợp**: island rỗng ⇒ không sinh được con từ đảo đó; phân bố parent theo đảo được log và kiểm tra |
| **X4** | Chiều feature map = độ dài code (ký tự) + khoảng cách embedding + điểm fitness tổng hợp; biên bin **tự giãn theo giá trị quan sát** nên elite cũ nằm sai ô khi biên đổi | — | Giữ 6 chiều hành vi của QuantEvolve (§3.1.3) với **biên cố định** trong `evaluation.lock.yaml` |
| **X5** | Chạy code sinh ra bằng `subprocess` với **toàn bộ `os.environ`** ⇒ code LLM đọc được API key; không chặn mạng, không giới hạn file | A6, an toàn | Sandbox: env rỗng, không mạng, dữ liệu mount đọc-chỉ, timeout + giới hạn bộ nhớ |
| **X6** | Bandit chọn model: `record_outcome()` không bao giờ truyền `improvement` ⇒ với UCB mặc định (`use_improvement=True`) mean luôn bằng 0; thưởng = "vượt best toàn cục" — thưởng cho may mắn | §3.1.9 | A/B model bằng **phân bổ cố định**, đo độ phủ + tỉ lệ code hợp lệ, không đo Sharpe IS |
| **X7** | `best.py` = max `combined_score` trên cùng dữ liệu đánh giá; không deflate, không split nằm trong repo | P6, DSR | Như §3.2 — không có "best" nào rời khỏi pipeline gate |

> **Kết luận:** repo đáng dùng làm **nguồn mẫu kỹ thuật** (L1–L5 đều rẻ, đã chạy được), **không** đáng dùng làm nền code — phần quality-diversity (X3, X4) lỏng hơn chính paper QuantEvolve mà ta đang theo, và phần ghi chép trial (X1, X2) đi ngược P2. Cách dùng: viết lại theo thiết kế của ta, tham khảo `transformer/patcher.py`, `transformer/blocks.py` và `transformer/parallel.py` (MIT — được phép chép kèm ghi nguồn).

#### 3.1.11. 🆕 Đa engine sinh strategy

Hệ chạy song song **nhiều engine sinh candidate**, dùng chung toàn bộ phần sau: template, DSL, sandbox, gate, ledger, quy tắc dựng danh mục (§3.2.1), holdout. Chỉ khâu *sinh* là khác.

```
                    ┌─► Engine A: QuantEvolve 4 agent ──┐
config/user.yaml ───┤                                    ├─► bộ điều phối ─► sandbox ─► gate ⓪–④ ─► ledger
 (tỉ lệ ngân sách)  ├─► Engine B: vòng lặp đơn giản ────┤   (ép tỉ lệ)                                │
                    └─► Engine C: random search ────────┘                                              ▼
                                                                    dựng danh mục từ ứng viên qua ④ của MỌI engine
```

| Engine | Cách sinh | Lời gọi LLM / candidate | Vai trò |
|---|---|---|---|
| **A — QuantEvolve** | §3.1.1–3.1.9: Research Agent (hypothesis 6 tag) → Coding Team → Evaluation Team → insight repository | 5–10 | Hệ chính: sâu, có giả thuyết và tri thức tích lũy |
| **B — Vòng đơn giản** | Theo MadEvolve: chọn parent (power-law theo xếp hạng) + 2–3 inspiration → **một** lời gọi LLM trả diff hoặc viết lại vùng tiến hóa → đánh giá → archive. Không hypothesis, không Evaluation Team, không insight | 1 (+ retry) | Rẻ, sinh nhiều biến thể nhỏ; đối chứng cho các tầng agent |
| **C — Random search** | Không LLM: ghép ngẫu nhiên toán tử DSL, tham số ngẫu nhiên trong biên `TUNABLE` | 0 | Đối chứng "LLM có giúp gì so với may rủi" — giữ suốt dự án với tỉ lệ nhỏ |

**Hai chế độ:**

| Chế độ | Khi nào | Archive | Mục đích |
|---|---|---|---|
| `isolated` | GĐ 2 — đợt thử nghiệm harness | Mỗi engine một archive, **không trao đổi** | So sánh sạch |
| `collaborative` | Từ GĐ 3 | Archive riêng, **định kỳ chuyển top 10%** giữa engine A ↔ B (như migration island). C không nhận migrant | A đóng góp chiều sâu, B đóng góp số lượng biến thể |

**Chia ngân sách theo số trial thống kê**, không theo lời gọi LLM hay giờ GPU — nếu không, engine B (rẻ gấp 5–10 lần) sẽ chiếm hết `N`. Bộ điều phối trong pipeline bất đồng bộ (§3.1.8) ép đúng tỉ lệ khai báo trong `user.yaml` (§10.1). GĐ 2 dùng **tỉ lệ cố định**. Sau GĐ 2 có thể phân bổ thích ứng theo **hiệu suất trial** (số strategy qua ④ / 100 trial) — **không bao giờ** theo Sharpe IS.

**Phép so sánh ở GĐ 2** (chế độ `isolated`, cùng dữ liệu, cùng gate, cùng ngân sách trial, ≥ 3 seed mỗi engine). So bằng:

1. Hiệu suất trial: số strategy qua ④ trên mỗi 100 trial
2. Độ phủ: số ô feature map có strategy qua ④
3. DSR của danh mục dựng theo §3.2.1 từ riêng mỗi engine, ở cùng `N`
4. Chi phí: lời gọi LLM và giờ GPU cho mỗi strategy qua ④
5. Suy giảm IS→OOS trên CPCV (§3.2)

**Không** so Sharpe IS của strategy tốt nhất — đó là best-of-N.

**Quy tắc quyết định — chốt trước khi chạy:**

| Kết quả | Hành động |
|---|---|
| A thắng cả B và C, cách biệt vượt độ dao động giữa các seed | Giữ A làm engine chính, B chạy song song ở chế độ `collaborative` |
| A **hòa** B | **B thành engine chính** (rẻ hơn 5–10 lần). Chỉ thêm lại thành phần của A (vd hypothesis 6 tag) nếu ablation riêng cho thấy đáng |
| Cả A và B không thắng C | Vấn đề nằm ở DSL, dữ liệu hoặc gate — **dừng lại tìm nguyên nhân** trước khi xây tiếp |

> ⚠️ **Chạy song song không cho thêm trial miễn phí.** Mọi engine đánh giá trên cùng dữ liệu IS ⇒ mọi trial cộng vào cùng một `N`. Lợi ích là đa dạng + luôn có đối chứng, không phải số lượng. Strategy sinh ra ở đợt thử nghiệm harness (GĐ 2) **không được vào danh mục**.

### 3.2. Validation Layer

Chi tiết đầy đủ ở [[07-VALIDATION-LAYER]]. Tóm tắt hợp đồng:

```python
@dataclass(frozen=True)
class GateResult:
    passed:  bool
    gate:    str            # "drift" | "guardrail" | "minbtl" | "pbo" | "dsr" | ...
    value:   float | None
    reason:  str
  
class Gate(Protocol):
    cost: int               # 1=rẻ ... 5=đắt — dùng để sắp thứ tự
    def check(self, candidate: StrategyCandidate) -> GateResult: ...
```

**Quy tắc:** gate chạy theo thứ tự `cost` tăng dần. **Mọi `GateResult` đều ghi ledger**, kể cả pass.

**Thứ tự đầy đủ:**

| #            | Gate                                 | cost | Cấp áp dụng                   | Ghi chú                                                                                    |
| ------------ | ------------------------------------ | ---- | -------------------------------- | ------------------------------------------------------------------------------------------- |
| **⓪** tầng 1 | 🆕 Spec-drift: checksum tĩnh        | 1    | Từng strategy                   | Không chạy code (§3.1.7)                                                                  |
| ①a          | Guardrail tĩnh (AST + DSL + hash template) | 1    | Từng strategy                   | **Phải qua trước khi bất kỳ code nào được thực thi.** 🆕 Kiểm tra vùng cố định không đổi + khai báo `TUNABLE` hợp lệ (§3.3.1) |
| **⓪** tầng 2 | 🆕 Spec-drift: hồi quy trace        | 1    | Từng strategy                   | Sandbox, micro-scenario cố định, Δ chuẩn hóa (§3.1.7)                                 |
| ①b          | Guardrail động (leaky-oracle test)  | 2    | Từng strategy                   |                                                                                             |
| ②           | MinBTL                               | 1    | Từng strategy                   |                                                                                             |
| ③           | Backtest IS                          | 3    | Từng strategy                   | 🆕 Từ đây trở đi, strategy được tính là **một trial thống kê** (§4.1). 🆕 v0.5: loại nếu **số lệnh < `min_trades`** hoặc **thời gian nắm giữ trung bình < `min_holding`** (§10.1) — chặn kiểu thổi Sharpe bằng cách giao dịch rất ít (MadEvolve §3.4); loại nếu **vi phạm ràng buộc tương quan indicator** (§3.3.1) |
| ④           | CPCV + PBO                           | 4    | Từng strategy (lưới tham số)   | Loại nếu PBO ≥ 0.5. Tập cấu hình: xem bên dưới                                      |
| —           | 🆕 Dựng danh mục                    | 3    | Danh mục                        | Quy tắc đăng ký trước, §3.2.1                                                           |
| **⑤** | **DSR**                        | 4    | 🔴**DANH MỤC HỢP NHẤT** | `N_eff` + `V[SR]` từ sổ trial thống kê, cộng `portfolio_variants`. **KHÔNG tính riêng từng cell** — xem §3.1.6 |
| ⑥′         | Data-source robustness + 🆕 độ nhạy chi phí | 4    | Danh mục                        | 🆕 Chạy lại với **phí × 2** và **slippage × 2**: danh mục phải còn Sharpe > 0 và DSR không sụt quá ngưỡng khóa theo đợt |
| ⑥           | Holdout                              | 5    | Danh mục đóng băng             | Một lần **mỗi đợt nghiên cứu**, qua`evaluator_proc.py` (§4.2)                          |
| ⑦           | Dry-run                              | 5    | Danh mục                        |                                                                                             |
| ⑧           | Live                                 | —   | Danh mục                        |                                                                                             |

> Chú ý cột "cấp áp dụng": gate ⓪–④ lọc **từng ứng viên**, nhưng từ ⑤ trở đi đối tượng kiểm định là **danh mục** — vì output của hệ này là một rổ strategy ít tương quan, không phải một strategy. 🆕 **Thứ tự chạy theo bảng này, không theo `cost` tăng dần** — hai thứ mâu thuẫn nhau (② có cost 1 nhưng chạy sau ①b); xem [ADR-0002](../implement_docs_vi/adr/0002-bo-sung-ledger-va-config.md).

**🆕 PBO kiểm định cái gì (v0.3).** CSCV cần một **ma trận hiệu suất** `T × M` của `M` cấu hình được so sánh *và* một **quy tắc chọn winner**. PBO đo xác suất quy tắc chọn đó chọn phải cấu hình mà OOS nằm dưới trung vị. Nó không phải thuộc tính của một chuỗi returns đơn lẻ. Với gate ④, ta định nghĩa:

- **Tập cấu hình** = **lưới tham số đăng ký trước** quanh ứng viên, 🆕 dựng tự động từ khai báo `TUNABLE` (§3.3.1): mỗi tham số tự do (≤ 6) lấy 3–5 giá trị trong khoảng ±30% (bước cố định trong `evaluation.lock.yaml`), cộng các biến thể mà vòng refine *thực sự đã thử* cho hypothesis này (lấy từ ledger). `M` bị chặn trên (vd ≤ 200, lấy mẫu nếu vượt).
- **Quy tắc chọn** = Sharpe IS cao nhất — đúng quy tắc mà vòng tiến hóa dùng.
- **Ý nghĩa:** PBO thấp ⇒ quy tắc chọn tham số trong vùng này có tính ổn định OOS. PBO cao ⇒ tham số được chọn nhờ nhiễu. PBO **không** thay cho DSR: nó không phạt số hypothesis đã thử trên toàn dự án.
- Ở cấp danh mục, nếu thử nhiều quy tắc dựng danh mục (§3.2.1), chạy thêm CSCV với tập cấu hình = các phương án danh mục đó.

**🆕 Đường cong suy giảm IS→OOS (v0.5, theo MadEvolve Hình 11 — nhưng không dùng holdout).** Sau mỗi `K` candidate, lấy strategy đang giữ kỷ lục IS của từng engine và ghi Sharpe trung vị trên các **đường OOS của CPCV** (metric *private*, §3.3.2). Vẽ hai đường: kỷ lục IS và OOS-CPCV của chính strategy đó. Đường OOS phẳng hoặc đi xuống trong khi IS tăng = tín hiệu p-hacking ⇒ **dừng engine đó sớm**. MadEvolve đo đường này trên *tập test* cho mọi champion — với ta như vậy là mở holdout hàng nghìn lần, vi phạm P6.

#### 3.2.1. 🆕 Xây dựng & chọn danh mục

Bản 0.2 bắt DSR tính trên danh mục nhưng không nói danh mục được dựng thế nào — nghĩa là để ngỏ một bậc tự do lớn cho selection bias. Quy tắc dưới đây **đăng ký trước** trong `evaluation.lock.yaml`, hash cùng các ngưỡng khác:

```
Đầu vào: mọi ứng viên đã qua ④ (PBO < 0.5) tính đến thời điểm đóng băng

1. Chọn đại diện ô     Mỗi cell giữ đúng 1 ứng viên: DSR-rank cao nhất trong cell
2. Lọc tương quan      Xếp đại diện theo DSR-rank giảm dần; nhận lần lượt,
                       bỏ ứng viên có |ρ| > 0.5 (returns IS) với bất kỳ strategy đã nhận
3. Giới hạn kích thước Tối đa K = 20 strategy (mục tiêu breadth ~20 ô, [[04-TRUONG-PHAI-B-HIEU-QUA]])
4. Trọng số            Risk parity ngây thơ: mỗi strategy cùng ngân sách vol (§3.4) —
                       KHÔNG tối ưu trọng số theo Sharpe IS (thêm bậc tự do, overfit)
5. Tái cân bằng        Theo lịch cố định (mặc định: hàng tháng) + khi có strategy mới vào/ra
5b. Hiệu chỉnh (tùy chọn) Một lượt Optuna trên vùng TUNABLE của từng strategy đã chọn,
                       ngân sách cố định; MỌI lần đánh giá ghi trials (source = param_opt).
                       Sau bước này PBO và DSR được tính LẠI trên tham số mới
6. Đóng băng           Hash(tập strategy_hash + trọng số + quy tắc + tham số) → portfolio_hash
```

- **Mỗi lần đổi bất kỳ bước nào** (ngưỡng ρ, K, cách chọn đại diện, lịch tái cân bằng) rồi đánh giá lại = **một phương án danh mục mới**, ghi bảng `portfolio_variants` (§4.1) và **cộng vào `N`** ở gate ⑤.
- Các tham số 0.5 / 20 / hàng tháng là **mặc định tạm** — nhưng phải chốt *trước* lần đánh giá danh mục đầu tiên, không chỉnh sau khi đã thấy DSR.

### 3.3. Strategy Runtime — hợp đồng cốt lõi

Đây là interface quan trọng nhất của toàn hệ thống. Nó là thứ cho phép P3 + P5.

```python
@dataclass(frozen=True)
class Signal:
    direction:     Literal["long", "short", "flat"]
    strength:      float          # ∈ [0, 1] — mức độ tham gia; KHÔNG phải số lượng, KHÔNG phải % vốn
    stop_distance: float          # đơn vị giá (thường k × ATR) — dùng cho thoát lệnh + trần rủi ro
    take_profit:   float | None = None

class Strategy(Protocol):
    """Venue-agnostic. KHÔNG biết gì về lot, share, contract, tiền tệ."""
    params: dict
  
    def indicators(self, bars: Bars) -> Features: ...
    def signal(self, features: Features) -> Signal: ...
```

> 🔴 **Strategy không bao giờ được import bất cứ gì từ tầng adapter.** Đây là ranh giới kiến trúc cứng — nên enforce bằng lint rule, không chỉ bằng quy ước.

#### 3.3.1. 🆕 Template strategy: vùng cố định + vùng tiến hóa

Theo cơ chế `EVOLVE-BLOCK` của MadEvolve (§3.1.10 L1) và `TUNABLE` (L2) — nhưng **cưỡng chế bằng code**, không bằng prompt.

```python
# ═══ VÙNG CỐ ĐỊNH — agent KHÔNG được sửa. Hash cam kết trong evaluation.lock.yaml ═══
from core.strategy.base import Signal, Strategy, Bars, Features
from core.strategy.registry import ind            # chỉ indicator trong whitelist

class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START ═══
    # TUNABLE: fast = 20,  bounds=(5, 60)
    # TUNABLE: slow = 100, bounds=(40, 300)
    # TUNABLE: k_atr = 2.0, bounds=(1.0, 4.0)

    def indicators(self, bars: Bars) -> Features:
        return {"f": ind.ema(bars.close, self.p.fast),
                "s": ind.ema(bars.close, self.p.slow),
                "atr": ind.atr(bars, 14)}

    def signal(self, x: Features) -> Signal:
        if ind.cross_up(x["f"], x["s"]):
            return Signal("long", 1.0, self.p.k_atr * x["atr"])
        return Signal("flat", 0.0, self.p.k_atr * x["atr"])
    # ═══ EVOLVE-BLOCK-END ═══
```

**Luật cưỡng chế ở gate ①a** (mọi vi phạm ⇒ `AST_REJECT`, ghi `generation_log`):

1. `SHA256(prefix) ‖ SHA256(suffix)` — phần ngoài hai marker — phải **bằng đúng** giá trị trong `evaluation.lock.yaml`. Nếu LLM trả nguyên file thay vì chỉ vùng tiến hóa, file đó bị từ chối chứ không được ghép (MadEvolve lại chấp nhận file nguyên như vậy — §3.1.10).
2. Mỗi tham số tự do phải có dòng `TUNABLE` với `bounds` hữu hạn; tối đa **6** dòng. Hằng số số học dùng làm ngưỡng/chu kỳ mà không khai báo ⇒ reject (chống giấu tham số để lách luật ≤ 6). Ngoại lệ: hằng số trong whitelist (0, 1, chu kỳ ATR chuẩn 14…).
3. Chỉ gọi `ind.*` trong whitelist và toán tử DSL (§3.1.6).

4. 🆕 **Ràng buộc tương quan indicator** (kiểm ở gate ③, vì cần dữ liệu): hai chuỗi indicator bất kỳ trong vùng tiến hóa có |ρ| trên IS > `max_indicator_corr` (mặc định 0.9) ⇒ reject. Theo MadEvolve §6.3: ràng buộc số feature và tương quan cặp cho kết quả IS/OOS nhất quán hơn.

**Khai báo `TUNABLE` được dùng ở ba nơi:** lưới PBO (§3.2), số `n_params` trong complexity penalty (§3.1.6 #1), và không gian của bước hiệu chỉnh tham số.

> 🔴 **v0.5 — tắt bộ tối ưu tham số trong vòng tiến hóa.** Bản 0.4 cho phép Coding Team chạy Optuna mỗi candidate miễn là ghi trial. Paper MadEvolve (§3.4) **cố ý không làm vậy**: nếu mỗi candidate được fit lại tham số liên tục trên tập đánh giá, "số trial hiệu dụng mỗi candidate không còn là vài lần đột biến của LLM mà là cả một đợt quét tìm cực trị địa phương" — `N` phình nhanh và tìm kiếm bị lái về code nhiều tham số. Quy tắc mới: trong vòng tiến hóa, **tham số do LLM đặt**, như logic. Hiệu chỉnh bằng Optuna chỉ được chạy **một lần**, cho các strategy đã chọn vào danh mục, **trước khi đóng băng** (§3.2.1 bước 5b).

**🆕 Tiến hóa theo module (v0.5).** Vùng tiến hóa tách thành tối đa ba khối có tên — mỗi khối có marker riêng:

```python
    # ═══ EVOLVE-BLOCK-START: entry ═══      điều kiện vào lệnh
    # ═══ EVOLVE-BLOCK-START: exit ═══       thoát lệnh + stop_distance
    # ═══ EVOLVE-BLOCK-START: regime ═══     bộ lọc chế độ thị trường (bật/tắt giao dịch)
```

`evolve_scope` trong `user.yaml` chọn khối nào được sửa; khối còn lại bị khóa và tính vào phần cố định khi hash (luật 1). Dùng để: (a) debug — khóa entry, chỉ tiến hóa exit; (b) ablation — đo đóng góp của từng khối. Theo MadEvolve Run 1–5: tiến hóa chung (joint) **không** luôn thắng tiến hóa từng phần, và joint rộng nhất cho khoảng cách overfit lớn nhất. Sizing **không bao giờ** là một khối tiến hóa — nó thuộc tầng Risk (P3, P4). Mỗi phạm vi tiến hóa là một cấu hình run riêng; mọi trial của mọi phạm vi cộng vào `N`.

#### 3.3.2. 🆕 Hợp đồng evaluator: metric public / private

Theo MadEvolve (§3.1.10 L3). Mọi kết quả đánh giá trả về đúng một cấu trúc:

```python
@dataclass(frozen=True)
class EvaluationReport:
    public:   dict[str, float]   # IS: Sharpe, MDD, số lệnh, turnover… → ĐƯỢC vào prompt
    private:  dict[str, float]   # CPCV paths, PBO, robustness, mọi thứ ngoài IS → CHỈ ghi ledger
    feedback: str                # sinh TỰ ĐỘNG từ `public` — không bao giờ đọc `private`
    error:    str | None
```

- Prompt builder chỉ nhận `public` + `feedback`. Có test khẳng định không khóa nào của `private` xuất hiện trong bất kỳ prompt nào (so chuỗi trên log prompt).
- Evaluation Team (§3.1.2) cũng chỉ thấy `public` — nó chấm *code có đúng hypothesis không*, không chấm *strategy có qua validation không*.

#### 3.3.3. 🆕 Sandbox chạy code sinh ra

MadEvolve chạy code LLM bằng `subprocess` thường, kế thừa toàn bộ biến môi trường — code sinh ra đọc được API key (§3.1.10 X5). Ta chạy mọi code sinh ra (kể cả micro-scenario của gate ⓪) với:

| Giới hạn | Giá trị |
|---|---|
| Biến môi trường | **Rỗng**, chỉ truyền `PYTHONPATH` tối thiểu |
| Mạng | **Chặn hoàn toàn** |
| Hệ thống file | Dữ liệu IS mount **đọc-chỉ**; ghi chỉ vào thư mục tạm của lần chạy; **không thấy** `holdout/`, `config/`, `ledger/` |
| Tài nguyên | Timeout (mặc định 300 s), giới hạn RAM, giới hạn CPU |
| Kết quả | Chỉ trả qua file JSON theo `EvaluationReport`; stdout/stderr bị cắt ngắn trước khi feed ngược |

Trên Windows local (A6): dùng container (Docker với `--network none`, `--read-only`) — không tự viết sandbox bằng Python.

### 3.4. Risk & Sizing Layer ★

Tầng này **quan trọng hơn tầng Adapter** nhưng hay bị coi nhẹ.

> 🔴 **Đính chính v0.3 — bản 0.2 giảm exposure theo volatility HAI lần.** Bản cũ tính `risk_amount = equity × risk_pct × target_vol / vol_estimate` rồi **chia tiếp cho `stop_distance`** (= k × ATR). Cả `vol_estimate` lẫn ATR đều tỉ lệ với biến động, nên khi biến động tăng gấp đôi, size giảm còn **~¼** thay vì ½ — tức là không còn là vol targeting, và vol danh mục 10% không đạt được. Quy tắc mới: **volatility chỉ đi vào size đúng một lần**; stop chỉ là **trần**, không nhân dồn.

```python
class PositionSizer:
    def size(self, signal: Signal, instrument: Instrument,
             account: Account, vol_estimate: float,
             n_active: int, idm: float) -> Quantity:
        # 1. Vol targeting — NƠI DUY NHẤT volatility đi vào size
        #    Ngân sách vol mỗi instrument: target danh mục chia đều, nhân IDM
        #    (instrument diversification multiplier, bù cho tương quan < 1)
        inst_target_vol = self.target_vol * idm / n_active
        notional = account.equity * inst_target_vol / vol_estimate * signal.strength
        qty_vol = notional / (instrument.price * instrument.multiplier * fx_rate)

        # 2. Trần rủi ro theo stop — MIN, không nhân
        #    Lỗ khi chạm stop không được vượt max_risk_pct vốn
        qty_cap = account.equity * self.max_risk_pct / (
            signal.stop_distance * instrument.multiplier * fx_rate)

        qty = min(qty_vol, qty_cap)

        # 3. Quy đổi sang đơn vị của venue: làm tròn theo lot step, kiểm tra min/max
        return round_to_lot(qty, instrument)
```

Thêm một bước ở **cấp danh mục** (chạy mỗi lần tái cân bằng, §3.2.1): ước lượng vol danh mục từ ma trận hiệp phương sai của các vị thế; nếu lệch mục tiêu, **scale đồng đều** mọi vị thế về `target_vol`, có trần đòn bẩy. IDM được ước lượng lại ở bước này.

**Bốn việc tầng này phải làm:**

1. **Vol targeting** — scale mọi market về cùng mục tiêu volatility (Hurst-Ooi-Pedersen dùng 10%/năm). **Không có bước này thì BTC vol ~60% nuốt hết rủi ro danh mục và VN30F1M vol ~20% coi như không tồn tại**
2. **Trần rủi ro theo stop** — `stop_distance` chỉ giới hạn lỗ tối đa mỗi lệnh (`min`), không tham gia scale lần hai
3. **Quy đổi đơn vị** — notional → lot/share/contract theo multiplier từng venue
4. **FX conversion** — hạch toán PnL thống nhất về một đồng tiền base (USD, VND...)

> Kiểm thử bắt buộc ở GĐ 1: nhân đôi cả `vol_estimate` và ATR của một instrument tổng hợp ⇒ size phải giảm **đúng ½** (khi trần stop không ràng buộc).

### 3.5. Execution Core & Adapters

**NautilusTrader** — lý do chọn: *"The DataEngine guarantees 100% identical data handling in both backtesting and live trading"*, deploy *"with no code changes"*. Đây chính là P5.

| Adapter                       | Thị trường                                          | Trạng thái                        |
| ----------------------------- | ------------------------------------------------------ | ----------------------------------- |
| Binance, Bybit, OKX           | Crypto spot + perp                                     | ✅ chính thức                     |
| **Interactive Brokers** | **Forex + stock quốc tế + futures toàn cầu** | ✅ chính thức                     |
| Databento                     | Futures data chất lượng cao                         | ✅ chính thức                     |
| **SSI FastConnect**     | **HOSE/HNX, VN30F1M**                            | 🔴**tự viết** — 3–6 tuần |

**🆕 Fill model bi quan (v0.5).** Backtest dùng `FillModel` của NautilusTrader cấu hình theo hướng bất lợi, khóa trong `evaluation.lock.yaml`:

- Lệnh limit chỉ khớp khi giá **xuyên qua** mức limit (low < limit với lệnh mua), không khớp khi chỉ chạm
- Lệnh market/stop chịu slippage cố định theo tick, cộng spread
- Phí theo biểu phí thực của venue

Tham chiếu: MadEvolve khớp **toàn bộ** khối lượng đúng giá limit mỗi khi giá xuyên qua, không có hàng đợi, dữ liệu gộp nhiều sàn — chính tác giả viết *"still far from being realistic"*; baseline của họ có Sharpe 4.81 cho strategy passive BTC khung phút. Với strategy tần suất thấp của ta, market impact nhỏ ở quy mô retail; phí, spread và slippage là phần chi phối. Độ nhạy chi phí được kiểm ở gate ⑥′.

Adapter VN cần: `InstrumentProvider` + `DataClient` + `ExecutionClient`. Phần khó nhất là **reconciliation khi reconnect mà còn vị thế mở**.

> ⚠️ Adapter VN giai đoạn 1 **chỉ hỗ trợ phái sinh (VN30F1M)**, bỏ cash market. Lý do: price limit ±7% gây "lock" phá vỡ backtest realism, T+2 chặn intraday, chưa có short-selling. Giảm ~60% công sức. Chi tiết [[06-PLATFORM-DA-THI-TRUONG]] mục 2.3.

---

## 4. Data model

### 4.1. Trial Ledger — single source of truth

> 🆕 **v0.3 — hai sổ, hai mục đích.** Bản 0.2 đếm `COUNT(*)` mọi dòng làm `N`, trộn lẫn lời gọi reviewer, lỗi compile và cấu hình đã backtest. Chúng không tương đương: chỉ cấu hình **đã được đo hiệu suất trên dữ liệu** mới là một phép thử có thể sinh selection bias. Ghi *mọi thứ* vẫn đúng (P2) — nhưng audit log và đầu vào thống kê phải tách nhau.

```sql
-- ═══ SỔ 1: AUDIT LOG — mọi hoạt động, append-only, KHÔNG dùng cho thống kê ═══
CREATE TABLE generation_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    run_id         TEXT NOT NULL,
    campaign_id    TEXT NOT NULL,        -- đợt nghiên cứu (§4.2)
    engine         TEXT NOT NULL,        -- 🆕 quantevolve | simple_loop | random (§3.1.11)
    seed           INTEGER NOT NULL,     -- 🆕 B7
    evolve_scope   TEXT,                 -- 🆕 entry | exit | regime | joint (§3.3.1)
    agent          TEXT NOT NULL,        -- data | research | coding | eval
    model_used     TEXT NOT NULL,        -- truy vết model (§3.1.9)
    cell_id        TEXT,                 -- ô feature map đang nhắm tới
    event          TEXT NOT NULL,        -- LLM_CALL | PATCH_FAIL | COMPILE_FAIL | AST_REJECT | TEMPLATE_TAMPER | DRIFT_REJECT | SANDBOX_VIOLATION | ...
    strategy_hash  TEXT,                 -- NULL nếu chưa sinh được code
    drift_delta    REAL,                 -- Δ chuẩn hóa (gate ⓪), nếu có
    detail         JSON
);

-- ═══ SỔ 2: TRIAL THỐNG KÊ — chỉ cấu hình đã qua ③ Backtest IS ═══
CREATE TABLE trials (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    run_id         TEXT NOT NULL,
    campaign_id    TEXT NOT NULL,
    engine         TEXT NOT NULL,        -- 🆕 engine SINH RA strategy; giữ nguyên khi migrate
    seed           INTEGER NOT NULL,
    evolve_scope   TEXT,
    strategy_hash  TEXT NOT NULL,        -- hash code chuẩn hóa, bắt trùng lặp
    hypothesis     TEXT,                 -- giả thuyết LLM viết TRƯỚC khi code
    params         JSON NOT NULL,
    universe       TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    timerange      TEXT NOT NULL,
    cell_id        TEXT,
    source         TEXT NOT NULL,        -- 🆕 evolution | param_opt | manual (viết tay) — lần đánh giá của bộ tối ưu tham số CŨNG là trial
    sharpe_is      REAL NOT NULL,        -- cần cho V[SR] giữa các trial
    returns_path   TEXT NOT NULL,        -- cần cho gom cụm N_eff và DSR
    candidate_id   TEXT NOT NULL,        -- 🆕 ADR-0002: nối với gate_results; cụm N_eff nằm ở trial_clusters
    gate_failed    TEXT,                 -- NULL nếu pass hết
    verdict        TEXT NOT NULL         -- PASS | REJECT_<gate> | REJECT_FABRICATION (reviewer phủ quyết sau backtest)
);

-- Phương án danh mục đã đánh giá (§3.2.1) — mỗi dòng cũng là một phép chọn
CREATE TABLE portfolio_variants (
    portfolio_hash TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL,
    rule_config    JSON NOT NULL,        -- ngưỡng ρ, K, trọng số, lịch tái cân bằng
    members        JSON NOT NULL,        -- danh sách strategy_hash + trọng số
    sharpe_is      REAL,
    returns_path   TEXT,
    ts             TIMESTAMP NOT NULL
);

-- 🆕 ADR-0002: lịch sử gom cụm N_eff — thay cho trials.cluster_id (ledger không bao giờ UPDATE)
CREATE TABLE clustering_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    method         TEXT NOT NULL,
    n_trials       INTEGER NOT NULL
);
CREATE TABLE trial_clusters (
    clustering_run INTEGER NOT NULL REFERENCES clustering_runs(id),
    trial_id       INTEGER NOT NULL REFERENCES trials(id),
    cluster_id     INTEGER NOT NULL,
    PRIMARY KEY (clustering_run, trial_id)
);

-- 🆕 ADR-0002: mọi GateResult, pass hay fail — các gate sau ③ không sửa được dòng trials
CREATE TABLE gate_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    campaign_id    TEXT NOT NULL,
    candidate_id   TEXT NOT NULL,
    strategy_hash  TEXT,
    trial_id       INTEGER,              -- NULL trước gate ③
    gate           TEXT NOT NULL,
    passed         INTEGER NOT NULL,
    value          REAL,
    reason         TEXT NOT NULL,
    detail         JSON
);

CREATE INDEX idx_hash ON trials(strategy_hash);
CREATE INDEX idx_cell ON trials(cell_id);
CREATE INDEX idx_gen_cell ON generation_log(cell_id);

-- Đầu vào DSR: đếm trial thống kê, mọi verdict, mọi run, KHÔNG reset
CREATE VIEW trial_stats AS
WITH latest AS (SELECT MAX(id) AS run FROM clustering_runs),
     covered AS (SELECT trial_id, cluster_id FROM trial_clusters
                 JOIN latest ON clustering_run = latest.run)
SELECT (SELECT COUNT(*) FROM trials)                  AS n_raw,
       (SELECT COUNT(DISTINCT cluster_id) FROM covered)
         + (SELECT COUNT(*) FROM trials
            WHERE id NOT IN (SELECT trial_id FROM covered)) AS n_eff,  -- trial chưa gom = một cụm riêng
       (SELECT AVG(sharpe_is * sharpe_is) - AVG(sharpe_is) * AVG(sharpe_is) FROM trials) AS var_sr;

CREATE VIEW total_portfolio_variants AS SELECT COUNT(*) AS n FROM portfolio_variants;

-- Cảnh báo cell lấp lệch: tỉ lệ sinh code thất bại > 50% — đọc từ AUDIT LOG
--    ⇒ cấp THÊM ngân sách refine, KHÔNG kết luận vùng đó vô giá trị
CREATE VIEW starved_cells AS
SELECT cell_id,
       SUM(event IN ('COMPILE_FAIL','AST_REJECT')) * 1.0
         / NULLIF(SUM(event = 'LLM_CALL' AND agent = 'coding'), 0) AS fail_rate,
       SUM(event = 'LLM_CALL' AND agent = 'coding') AS attempts
FROM generation_log
WHERE cell_id IS NOT NULL
GROUP BY cell_id
HAVING fail_rate > 0.5;
```

> 🔴 **Đầu vào của gate ⑤ DSR** = `trial_stats` (`N_eff`, `V[SR]`) + `total_portfolio_variants`, áp lên chuỗi returns của **danh mục hợp nhất**. Xem hộp cảnh báo ở §3.1.6. Đây là chỗ dễ sai nhất trong toàn bộ thiết kế.
>
> 🆕 **Trial ẩn (bài học MadEvolve X2):** bước hiệu chỉnh tham số (§3.2.1 bước 5b) ghi **mỗi** cấu hình được đánh giá thành một dòng `trials` với `source = 'param_opt'`. Repo MadEvolve bỏ qua đúng những lần đánh giá này. (v0.5: bộ tối ưu không còn chạy trong vòng tiến hóa — §3.3.1.)
>
> **Ước lượng `N_eff`:** gom cụm chuỗi returns của mọi trial theo khoảng cách tương quan (`d = √(½(1−ρ))`), số cụm tối ưu chọn bằng silhouette — cách của López de Prado (ONC). Chạy lại mỗi lần đánh giá danh mục. `N_eff` chỉ được *giảm* ngưỡng một cách có căn cứ; luôn báo cáo DSR ở cả `N_raw` và `N_eff`.

### 4.2. Holdout Registry — chống P6 bị vi phạm

> 🔴 **Đính chính v0.3.** Bản 0.2 khóa `holdout_access` theo `strategy_hash`. Như vậy strategy A và B **vẫn cùng đọc được holdout, mỗi cái một lần** — rồi ta giữ cái nào có `sharpe_oos` tốt hơn. Holdout đã thành một vòng chọn lọc nữa. Yêu cầu đúng: holdout được dùng **một lần cho cả quá trình lựa chọn**, không phải một lần cho mỗi ứng viên.

```sql
-- Đợt nghiên cứu: gom mọi run, mọi trial trước một lần mở holdout
CREATE TABLE campaigns (
    campaign_id     TEXT PRIMARY KEY,
    started_at      TIMESTAMP NOT NULL,
    holdout_range   TEXT NOT NULL,        -- khoảng thời gian holdout của đợt này
    lock_hash       TEXT NOT NULL,        -- 🆕 ADR-0002: SHA256 của evaluation.lock.yaml (§10.1)
    holdout_lock_hash TEXT,               -- 🆕 ADR-0002: SHA256 của holdout.lock
    status          TEXT NOT NULL CHECK (status IN ('OPEN','FROZEN','BURNED'))
);

CREATE TABLE holdout_access (
    campaign_id     TEXT PRIMARY KEY REFERENCES campaigns,  -- mỗi ĐỢT chỉ 1 lần mở
    portfolio_hash  TEXT NOT NULL REFERENCES portfolio_variants,  -- danh mục đã đóng băng
    frozen_at       TIMESTAMP NOT NULL,   -- phải < accessed_at
    accessed_at     TIMESTAMP NOT NULL,
    timerange       TEXT NOT NULL,
    verdict         TEXT NOT NULL CHECK (verdict IN ('PASS','FAIL')),  -- chỉ 1 bit trả về vòng nghiên cứu
    sharpe_oos      REAL                  -- ghi cho người đọc báo cáo, KHÔNG trả về agent
);
```

**Quy trình:**

1. Trong trạng thái `OPEN`, agent tiến hóa, validation chạy ⓪–⑤, danh mục được dựng theo §3.2.1. Holdout **không tồn tại** với mọi process của đợt này.
2. `FROZEN`: ghi `portfolio_hash` (tập strategy + trọng số + quy tắc). Sau bước này **không được thêm/bớt/đổi trọng số**.
3. Mở holdout **đúng một lần** cho **đúng một danh mục** đã đóng băng. Evaluator trả về **PASS/FAIL** so với ngưỡng đăng ký trước — không trả số, để không có gradient nào chảy ngược.
4. Đợt chuyển sang `BURNED`. Nếu FAIL và muốn nghiên cứu tiếp: **mở đợt mới với holdout mới** (dữ liệu *sau* holdout cũ, hoặc chờ dữ liệu mới tích lũy). Holdout cũ được sáp nhập vào vùng IS của đợt mới và **không bao giờ được dùng làm holdout lần nữa**.

Cưỡng chế bằng `PRIMARY KEY (campaign_id)`: lần mở holdout thứ hai trong cùng đợt — dù cho strategy khác, danh mục khác — sẽ **ném lỗi ở tầng database**.

> ⚠️ **Hệ quả thực tế:** holdout là tài nguyên **tiêu hao**, không tái tạo. Với dữ liệu miễn phí có độ sâu giới hạn (A7), số đợt nghiên cứu có holdout sạch là **rất ít** (vài đợt trong cả dự án). Đây là lý do nữa để đừng mở holdout sớm.

> 🆕 **Nhưng `PRIMARY KEY` vẫn chưa đủ — nâng P6 lên cấp hệ điều hành.**
>
> AgonAlpha chỉ ra một điểm mù: trong nghiên cứu học thuật, "holdout" chỉ là lời hứa, vì **tác giả vẫn giữ quyền kiểm soát toàn bộ pipeline**. Ta đang ở đúng vị trí đó — ta vừa viết code tính DSR, vừa viết code quyết định khi nào đọc holdout.
>
> Ba lớp cưỡng chế, từ yếu đến mạnh:

| Lớp | Cơ chế                                                                                                                                                                      | Chặn được gì                                                                                            |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| 1    | `PRIMARY KEY (campaign_id)` trên `holdout_access`                                                                                                                                      | Mở holdout lần hai trong cùng đợt *qua đúng API*, dù cho strategy/danh mục khác                                                                    |
| 2    | 🆕**File holdout chmod `0400`, hash SHA256 cam kết trong `holdout.lock` trước khi bắt đầu mỗi đợt**                                                           | Sửa/thay dữ liệu holdout; phát hiện nếu file bị đụng                                                |
| 3    | 🆕**Evaluator chạy trong process riêng**, chỉ nhận `portfolio_hash` đã đóng băng, trả về **đúng một bit PASS/FAIL**. Không chia sẻ bộ nhớ với vòng tiến hóa | Vòng tiến hóa "vô tình" đọc được kết quả holdout rồi dùng nó để chọn generation tiếp theo. Trả 1 bit thay vì một số để giảm lượng thông tin rò ngược nếu có đợt sau |

> Lớp 3 là lớp quan trọng nhất và cũng dễ bị bỏ qua nhất: nếu evaluator nằm cùng process, chỉ cần một biến toàn cục hoặc một dòng log cũng đủ để thông tin holdout rò ngược vào vòng lặp. ⚠️ Process riêng **không tự nó** ngăn feedback — chính người vận hành vẫn thấy PASS/FAIL. Thứ ngăn feedback là quy tắc *đợt đã BURNED thì holdout đó không bao giờ dùng lại* ở trên.

---

## 5. Cấu trúc thư mục

```
TradingProject/                   ← Crucible Quant (package: quantcrucible)
├── research_docs/                ← nghiên cứu + tài liệu này
├── core/
│   ├── strategy/
│   │   ├── base.py                ← Strategy protocol, Signal
│   │   ├── template.py            ← template: vùng cố định + EVOLVE-BLOCK (§3.3.1)
│   │   ├── tunable.py             🆕 đọc khai báo TUNABLE → lưới PBO, n_params
│   │   └── registry.py            ← whitelist indicator
│   ├── sizing/
│   │   ├── vol_target.py          ★ vol targeting
│   │   └── position_sizer.py      ← risk → lot/share/contract
│   └── zoo/                       ← strategy kinh điển, để đo AST similarity
├── agent/                         ← kiến trúc QuantEvolve
│   ├── loop.py                    ← Algorithm 1: vòng generation
│   ├── data_agent.py              ← ① schema prompt + phát hiện category
│   ├── research_agent.py          ← ② hypothesis (6 tag XML)
│   ├── coding_team.py             ← ③ code → backtest → refine (subprocess)
│   ├── evaluation_team.py         ← ④ 5 chức năng phân tích
│   ├── evolution/
│   │   ├── feature_map.py         ← MAP-Elites, 16 bin × 6+ chiều
│   │   ├── islands.py             ← N island + migration top 10%
│   │   ├── sampling.py            ← SampleParent (Eq.1), SampleCousins (Eq.2)
│   │   └── archive.py             ← lưu cả strategy bị feature map từ chối
│   ├── insights.py                ← repository + curate mỗi K=50 gen
│   ├── dsl.py                     🆕 DSL đóng + AST reject toán tử ngoài whitelist
│   ├── drift.py                   🆕 gate ⓪: SHA256 logic + Levenshtein chuẩn hóa, trace neo
│   ├── patcher.py                 🆕 SEARCH/REPLACE strict + viết lại block + retry kèm lỗi
│   ├── pipeline.py                🆕 LLM producer → hàng đợi prefetch → slot backtest
│   ├── routing.py                 🆕 định tuyến model dị thể (§3.1.9)
│   ├── engines/                   🆕 §3.1.11
│   │   ├── quantevolve.py         ← engine A (4 agent)
│   │   ├── simple_loop.py         ← engine B (parent + inspirations → 1 lời gọi LLM)
│   │   └── random_search.py       ← engine C (không LLM)
│   ├── scheduler.py               🆕 ép tỉ lệ ngân sách trial giữa các engine
│   └── prompts/                   ← template theo Appendix A của paper
├── validation/
│   ├── gates.py                   ← Gate protocol, pipeline
│   ├── report.py                  🆕 EvaluationReport: public / private / feedback (§3.3.2)
│   ├── sandbox.py                 🆕 wrapper Docker --network none --read-only (§3.3.3)
│   ├── guardrail.py               ← AST similarity + lookahead scan
│   ├── statistical.py             ← CPCV, PBO, DSR, MinBTL (purgedcv)
│   ├── portfolio.py               🆕 dựng danh mục theo quy tắc đăng ký trước (§3.2.1)
│   ├── portfolio_dsr.py           🆕 DSR trên danh mục hợp nhất, N_eff + V[SR] (§4.1)
│   ├── n_eff.py                   🆕 gom cụm returns trial → N_eff
│   ├── temporal.py                ← holdout manager, decay
│   └── oracles/                   ← bộ leaky oracle 4 mức
├── holdout/                       🔴 chmod 0400 + holdout.lock (hash cam kết)
│   └── evaluator_proc.py          🆕 process riêng, nhận portfolio_hash, trả PASS/FAIL
├── config/
│   ├── user.yaml                  🆕 người dùng cấu hình (§10.1)
│   └── evaluation.lock.yaml       🆕 sinh từ user.yaml mỗi đợt — đọc-chỉ, hash, agent không ghi được
├── ledger/
│   └── db.py
├── execution/
│   ├── engine.py                  ← wrapper NautilusTrader
│   └── adapters/
│       └── ssi/                   🔴 tự viết
├── data/
└── tests/
```

> 🆕 **Triển khai:** code nằm dưới `src/quantcrucible/` (src layout); `holdout/` ở gốc chỉ chứa dữ liệu, còn `evaluator_proc.py` nằm ở `src/quantcrucible/holdout/`. Xem [ADR-0001](../implement_docs/adr/0001-src-layout-and-holdout-split.md) và [cây thư mục cập nhật](../implement_docs/04-MODULE-MAP.md).

---

## 6. Tech stack

| Thành phần        | Chọn                               | Lý do                                                                          |
| ------------------- | ----------------------------------- | ------------------------------------------------------------------------------- |
| Execution core      | **NautilusTrader**            | Backtest ≡ live có bảo đảm; multi-asset native; Rust core                  |
| Agent orchestration | **LangGraph**                 | Cần state + cycle; bạn đã quen                                              |
| Optimizer           | **Optuna**                    | TPE/GP, chuẩn de-facto                                                         |
| Validation          | **`purgedcv`** (PyPI) ⚠️ *API chưa kiểm chứng* | Đủ PBO + DSR + CPCV + WalkForward + MinBTL/MinTRL trong 1 lib, MIT            |
| Search nhanh        | **vectorbt** *(tùy chọn)* | Quét tham số; ⚠️*"lies about microstructure"* — chỉ dùng sàng lọc thô |
| Ledger              | SQLite → Postgres                  | Bắt đầu đơn giản                                                          |
| **Data**      | **Toàn bộ miễn phí**      | Xem mục 6.1 — đây là**ràng buộc cứng**                            |
| Đóng gói         | FastAPI + Docker                    | Bạn đã quen                                                                  |

⚠️ **License cần kiểm tra:** `vectorbt` có Commons Clause; `pypbo` là AGPL-3.0 (đã tránh bằng cách dùng `purgedcv` MIT).

> 🆕 **`purgedcv` đang là lõi của validation layer nhưng chưa được kiểm chứng.** Chữ ký `deflated_sharpe_ratio` và `probability_of_backtest_overfitting` chưa đối chiếu với mã nguồn, và chưa rõ nó có nhận `N_eff`/`V[SR]` tách riêng (§4.1) hay không. Việc đầu tiên của **GĐ 1**: (1) đọc mã nguồn, pin phiên bản; (2) viết test đối chiếu với ví dụ số trong paper gốc DSR (Bailey & López de Prado 2014) và PBO (Bailey et al. 2017); (3) nếu lệch hoặc thiếu tham số → tự cài đặt DSR/PBO (mỗi hàm ~50 dòng) và chỉ dùng `purgedcv` cho CPCV/purging. Kiến trúc không phụ thuộc vào thư viện này — `validation/statistical.py` là lớp bọc.

---

## 6.1. Data — ràng buộc: MIỄN PHÍ 100%

### Nguồn theo từng asset class

| Asset class               | Nguồn miễn phí                                                                                      | Chất lượng                  | Vấn đề                                                   |
| ------------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------ | ----------------------------------------------------------- |
| **Crypto**          | **ccxt** (Binance, Bybit, OKX)                                                                   | 🟢**Không thỏa hiệp** | Không có. Free = full quality                             |
| **Forex spot**      | Dukascopy, HistData,**Stooq** (68 major + 1.840 cặp khác)                                      | 🟢 Tốt ở khung daily         | Tick data broker chất lượng kém                         |
| **Stock quốc tế** | **Stooq** (21.000+ mã & ETF toàn cầu), yfinance                                               | 🟡 Khá                        | 🔴**Survivorship bias** — không có mã đã delist |
| **Futures**         | **TurtleTrader** (major US futures từ 1970s, có OHLCV + open interest), QuantConnect free tier | 🔴**Yếu nhất**         | 🔴**Continuous contract chưa xử lý roll**          |
| **Việt Nam**       | **vnstock** free tier                                                                            | 🟡 Khá                        | Lịch sử ngắn                                             |

### ⚠️ Ba cái giá phải trả

**1. 🔴 Roll continuous contract trở thành việc của bạn**

Đây là cái giá lớn nhất. Dữ liệu futures miễn phí chủ yếu là **front-month chưa điều chỉnh**.

> *"Introducing artificial jumps from roll dates can completely alter any form of data analysis or backtest performed on this time series... they show up in your results as false wins or losses that had nothing to do with your strategy."*

⚠️ **Lưu ý quan trọng:** nguyên tắc "rule phải scale-invariant" (mục 3.1) **KHÔNG cứu được** trường hợp này. Scale-invariance bảo vệ bạn khỏi *méo mó do back-adjustment*, nhưng roll gap chưa điều chỉnh tạo ra **return giả** — và rule dùng return sẽ ăn phải nó.

**Giải pháp (miễn phí, nhưng là việc thật):** tự dựng continuous contract từ dữ liệu từng hợp đồng riêng lẻ.

```
TurtleTrader cho bạn: OHLCV + OPEN INTEREST của từng hợp đồng
        ↓
Tự roll khi volume/OI của hợp đồng sau vượt hợp đồng trước
        ↓
Tự áp dụng ratio adjustment (KHÔNG dùng back-adjustment —
xem cảnh báo dưới)
        ↓
Continuous contract của riêng bạn, kiểm soát được
```

> 🔴 **Tránh back-adjustment (Panama method):** *"on sufficiently long crude oil series, backward-adjusted prices can turn negative — which has no economic meaning"*, và nó *"introduces a trend bias... a large drift to the prices"*. Dùng **ratio/proportional adjustment**.

**Ước lượng: 2–3 tuần** để xây + test module roll. Đây chính là thứ $270/năm của Norgate mua hộ bạn.

**2. 🔴 Survivorship bias trên cổ phiếu — KHÔNG sửa được bằng dữ liệu free**

Stooq/yfinance chỉ có công ty **còn sống**. Đây là yếu tố thổi phồng backtest số 1 với chiến lược cổ phiếu.

**Hệ quả kiến trúc:** với cổ phiếu quốc tế, **chỉ giao dịch chỉ số/ETF** (SPY, QQQ, EFA...) chứ **không giao dịch cổ phiếu đơn lẻ**. Chỉ số không có survivorship bias ở cấp instrument. Đây là ràng buộc bắt buộc, không phải gợi ý.

**3. 🟡 Thời gian kỹ thuật thay cho tiền**

Bạn đổi **$270/năm** lấy **2–3 tuần công sức + bảo trì liên tục**. Đây là đánh đổi hợp lý khi chưa biết dự án có ra edge hay không.

### Lấy data từ chính broker mình giao dịch?

Lập luận ủng hộ **đúng về nguyên tắc**: backtest và live trên cùng venue xóa được mismatch giữa nguồn dữ liệu và nơi khớp lệnh — cùng giá, spread, symbol spec, session. Đây là P5 mở rộng xuống tầng dữ liệu. Và nó **miễn phí** (có tài khoản).

**Nhưng có 5 vấn đề, 3 trong đó là chặn cứng:**

| # | Vấn đề                                                            | Mức     | Bằng chứng                                                                                                                                                                                                                            |
| - | -------------------------------------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | **Lịch sử quá nông**                                       | 🔴 Chặn | IB:*"Expired futures data older than two years counting from the future's expiration date"* không có → **không dựng được chuỗi futures dài**. Bar ≤30s cũ hơn 6 tháng cũng không có                            |
| 2 | **Survivorship bias tệ hơn public**                          | 🔴 Chặn | Broker chỉ list cái đang cung cấp — mã delist và hợp đồng hết hạn biến mất hoàn toàn                                                                                                                                    |
| 3 | **Chất lượng khác nhau theo broker, không audit được** | 🔴 Chặn | Cùng EURGBP 10 năm,**4 broker**: có nơi không đạt nổi 60% history quality, có nơi ~100%. **Cùng EA: 100% quality → lãi ~$1.300; 10% quality → lãi ~$2.500** — dữ liệu tệ làm strategy trông ĐẸP HƠN |
| 4 | **Pacing limit**                                               | 🟡       | IB: 60 req/10 phút, 6 req cùng contract/2 giây, BID_ASK tính gấp đôi. Daily OK (~50 phút cho 30 instrument × 10 năm);**intraday thảm** (~2.520 req cho 10 năm *một* instrument)                                    |
| 5 | **Lock-in**                                                    | 🟡       | Đổi broker → toàn bộ research corpus mất tính so sánh, ledger`N` xây trên dữ liệu không còn                                                                                                                             |

> *"High modeling quality on garbage history just gives you a very smooth, very confident lie."*

**✅ Với crypto, câu hỏi này đã tự giải quyết.** ccxt kéo dữ liệu **từ chính Binance**, và bạn cũng khớp lệnh trên Binance → free data **đã là** broker data, đã venue-exact. Giai đoạn 0–3 không dính vấn đề này. Nó chỉ cắn ở GĐ 4–5 (forex/stock/futures).

### 🎯 Quyết định: chiến lược dữ liệu HAI TẦNG

Không chọn một bên. Phân vai theo gate:

| Gate                                                 | Nguồn                                            | Yêu cầu                                                   |
| ---------------------------------------------------- | ------------------------------------------------- | ----------------------------------------------------------- |
| **①–⑤** nghiên cứu, hàng nghìn backtest | **Public free** (ccxt, Stooq, TurtleTrader) | Bề sâu lịch sử, bulk download,**tính tái lập** |
| **⑥–⑦** holdout + dry-run                   | **Broker** (IB, sàn crypto)                | **Venue-exact**                                       |

**Và đây là lý do nó hơn một sự thỏa hiệp — thêm gate mới:**

```
⑥′ DATA-SOURCE ROBUSTNESS  (gate mới, chèn trước holdout)
    Chạy lại strategy trên dữ liệu BROKER thay vì public.
    🔒 Gate: Sharpe không sụt quá 30% khi đổi nguồn dữ liệu
```

> 🔑 Nếu strategy sống trên EURUSD của Stooq nhưng chết trên EURUSD của broker bạn, đó là **thông tin thật**: edge chỉ là artifact từ cách một vendor làm sạch dữ liệu. Bạn muốn biết điều đó **trước khi** bỏ tiền thật.
>
> Đây là một dạng **robustness test mà không paper nào trong [[00-TONG-HOP-NGHIEN-CUU]] có** — vì họ đều chỉ dùng một nguồn dữ liệu duy nhất.

### 🎯 Vì sao ràng buộc này KHÔNG chặn bạn

**Giai đoạn 0–4 (khoảng 6 tháng đầu) chỉ cần crypto và forex/index — hai nguồn này miễn phí với chất lượng không thỏa hiệp.**

Vấn đề futures chỉ cắn ở **giai đoạn 5**. Tới lúc đó bạn đã biết dự án có đáng để bỏ $270 hay không.

> 💡 **Khuyến nghị:** giữ ràng buộc miễn phí, **thiết kế `DataSource` thành interface** để thay nguồn không phải sửa gì phía trên. Nếu sau này muốn mua Norgate thì chỉ cần viết một adapter mới.

```python
class DataSource(Protocol):
    def bars(self, symbol: str, timeframe: str,
             start: datetime, end: datetime) -> Bars: ...
    def is_continuous(self) -> bool: ...   # futures đã roll chưa?
    def adjustment(self) -> Literal["none", "ratio", "difference"]: ...
```

### Làm rõ: Futures ≠ Forex

Điểm hay gây nhầm. **Futures không phải một thị trường, nó là một LOẠI HỢP ĐỒNG.** Rổ futures đa tài sản chứa 4 nhóm:

| Nhóm                     | Ví dụ mã                                              |
| ------------------------- | -------------------------------------------------------- |
| Hàng hóa                | CL (dầu), GC (vàng), NG (gas), ZC (ngô)               |
| Chỉ số cổ phiếu       | ES (S&P 500), NQ (Nasdaq), FDAX, NK                      |
| Trái phiếu / lãi suất | ZN (T-note 10y), ZB (30y), Bund                          |
| **Tiền tệ**       | 6E (EUR), 6J (JPY), 6B (GBP) ←**đây là forex** |

Đây đúng là rổ Hurst-Ooi-Pedersen dùng (**29 hàng hóa + 11 chỉ số + 15 trái phiếu + 12 tiền tệ**) để ra Sharpe **1.60** — xem [[04-TRUONG-PHAI-B-HIEU-QUA]].

**Forex spot vs currency futures:**

|                | Forex spot (OTC)                  | Currency futures              |
| -------------- | --------------------------------- | ----------------------------- |
| Volume         | ❌ Không có volume thật        | ✅ Volume + OI thật          |
| Đối tác     | Broker → rủi ro**B-book** | Clearing house                |
| Hết hạn      | Không                            | Có → phải roll             |
| Dữ liệu free | 🟢 Stooq/Dukascopy tốt           | 🔴 Phải tự dựng continuous |

> 💡 Với trend following, currency futures thường tốt hơn spot (có volume thật, không B-book). **Nhưng với ràng buộc dữ liệu miễn phí, forex spot lại dễ hơn** — Stooq cho dữ liệu daily sạch, không cần roll. → **Giai đoạn đầu dùng forex spot, để currency futures cho giai đoạn 5.**

---

## 7. Lộ trình xây

| GĐ         | Nội dung                                                                                                          | Gate chuyển giai đoạn                                                                         | Ước lượng        |
| ----------- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ | -------------------- |
| **0** | **Harness + ledger + oracle suite**. Chưa có agent. Viết tay 1 strategy EMA crossover chạy hết 8 bước. 🆕 Template + sandbox + `EvaluationReport` | 🔒**Pipeline TỪ CHỐI được cả 4 mức leaky oracle**                                   | 2–3 tuần           |
| **1** | Validation layer đầy đủ (CPCV/PBO/DSR/MinBTL) + Risk & Sizing                                                  | 🔒 Strategy tay qua đủ gate, ledger tách audit/trial đúng, `N_eff` + `V[SR]` tính được; DSR/PBO khớp ví dụ số trong paper gốc; test sizing ½ (§3.4) pass                                            | 2–3 tuần           |
| **2** | Agent loop**QuantEvolve** (4 agent + feature map + island),**crypto-only**                             | 🔒 ≥150 generation tự động, mọi`s_new` vào ledger, feature map không sụp về 1 bin. 🆕 Test tích hợp island pass (§3.1.5). 🆕 **So sánh đa engine A/B/C ở chế độ `isolated`** theo §3.1.11 — quy tắc quyết định chốt trước khi chạy (kiểm chứng nội bộ cho K4, §3.1) + A/B model Research Agent (§3.1.9)     | **6–8 tuần** |
| **3** | Mở breadth: 15–30 instrument ít tương quan. 🆕 Chuyển đa engine sang `collaborative` | 🔒 Vol targeting hoạt động, không instrument nào chiếm >20% rủi ro | 2–3 tuần          |
| **4** | Adapter IB → forex + stock quốc tế                                                                              | 🔒 Session/calendar đúng, không sinh look-ahead                                               | 3–4 tuần           |
| **5** | Futures đa tài sản —**gồm tự xây module roll** từ dữ liệu free (xem 6.1)                           | 🔒 Continuous contract tự dựng khớp với nguồn tham chiếu; roll gap không sinh return giả | **5–7 tuần** |
| **6** | 🔴 Adapter SSI → VN30F1M                                                                                          | 🔒 Reconciliation đúng khi reconnect có vị thế mở                                          | 3–6 tuần           |

**Tổng: 6–9 tháng.**

> 🎯 **Giai đoạn 0 là giai đoạn quan trọng nhất và hay bị bỏ qua nhất.** Gate của nó không phải "strategy có lãi không" mà là **"harness có từ chối được gian lận không"**.

---

## 8. Rủi ro

| Rủi ro                                              | Mức   | Giảm thiểu                                                                                                       |
| ---------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------ |
| **Agent sinh rác ở tốc độ cao**           | 🔴 Cao | P1 + P2: harness trước, ledger không bypass                                                                     |
| **`N` bị đếm thiếu → DSR đẹp giả**   | 🔴 Cao | Ledger là đường duy nhất tới backtest; enforce bằng code                                                    |
| **Holdout bị nhìn nhiều lần**              | 🔴 Cao | `PRIMARY KEY (campaign_id)` trên holdout registry — mỗi đợt nghiên cứu chỉ mở 1 lần, cho 1 danh mục đã đóng băng; đợt đã BURNED không dùng lại holdout (§4.2) |
| **Roll gap tự dựng sai → return giả**      | 🔴 Cao | Ràng buộc data free (6.1). Đối chiếu continuous tự dựng với nguồn tham chiếu trước khi tin             |
| **Survivorship bias cổ phiếu**               | 🔴 Cao | Dữ liệu free không có mã delist →**chỉ giao dịch index/ETF, không giao dịch cổ phiếu đơn lẻ** |
| **Adapter VN reconciliation sai**              | 🟡 TB  | Để cuối cùng; test kỹ trên demo                                                                              |
| **Session/calendar lệch → look-ahead ngầm** | 🟡 TB  | Test riêng cho từng venue khi thêm adapter                                                                      |
| **Không tìm ra edge nào**                   | 🟡 TB  | **Đây là kết quả hợp lệ.** Quỹ CTA 60+ market vừa lỗ 3 năm liên tiếp                            |
| **Over-engineering trước khi có edge**      | 🔴 Cao | Dừng ở GĐ 3 cho tới khi có strategy qua được dry-run                                                       |

---

## 9. Điều kiến trúc này CỐ Ý không làm

Ghi rõ để tránh scope creep:

- ❌ **Không có LLM lúc runtime** — A4
- ❌ **Không làm cross-sectional factor + ML** — đó là trường phái A, kiến trúc khác ([[08-LLM-QUANT-RESEARCHER]] — trường phái A chiếm ~18/22 hệ khảo sát)
- ❌ **Không hỗ trợ EA MQL5** — A3 (đã chốt 21/9/2026): live trade qua NautilusTrader. Forex đi qua **Interactive Brokers**, không qua broker MT5
- ❌ **Không làm HFT / order book strategy** — tần suất thấp là điều kiện sống sót của retail
- ❌ **Không tự viết backtest engine** — NautilusTrader đã giải quyết
- ❌ **Không dự báo giá bằng LLM** — ngoài phạm vi dự án
- 🆕 ❌ **Không làm meta-evolution prompt genome** (AlgoEvolve). Nghe hay nhưng bằng chứng đang **âm**: bảng của chính paper cho thấy bỏ nó đi lại cho Sharpe cao hơn (5.71 > 5.60) và drawdown thấp hơn gần 4 lần. Quan trọng hơn — mỗi biến thể prompt là **một trục tìm kiếm mới**, làm phình `N` trong DSR. Ta đang trả trial để mua thứ chưa chứng minh được giá trị ([[08-LLM-QUANT-RESEARCHER]] §7.4)
- 🆕 ❌ **Không làm RFT / RL fine-tuning** (Alpha-R1, QuantEvolver). Hướng mới nhất 2026 và có lẽ là hướng đúng về dài hạn, nhưng Alpha-R1 tốn **64×H800 × 120 giờ** — vi phạm A6

---

## 10. Sổ quyết định — nguồn duy nhất

> 🆕 **v0.3.** Trước đây README trỏ tới 4 câu hỏi ở [[00-TONG-HOP-NGHIEN-CUU]] mục 9, còn mục này có 5 câu khác, và hai nơi nói khác nhau về câu nào chặn việc code. **Từ bản này, bảng dưới là nơi duy nhất ghi trạng thái quyết định.** Mọi tài liệu khác trỏ về đây.

Trạng thái: ✅ **Đã chốt** (đổi thì phải sửa kiến trúc) · 🟡 **Mặc định tạm** (dùng để code, chỉnh được sau không phá thiết kế) · 🔴 **Mở, chặn** (phải trả lời trước khi viết code ở phần bị ảnh hưởng)

| #  | Quyết định                                  | Trạng thái           | Giá trị hiện hành                                                           | Nguồn / ảnh hưởng                                                                        |
| -- | ------------------------------------------- | -------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| D1 | Thị trường (00 §9 câu 1)                  | ✅ Đã chốt (xác nhận 21/9/2026) | Crypto → forex + stock quốc tế → Việt Nam, theo thứ tự GĐ (A2, §7)        | Yêu cầu trực tiếp. Nếu chỉ crypto → Freqtrade ([[05-SMOOTH-FLOW]])                   |
| D2 | Output: rule TA hay ML factor (00 §9 câu 2) | ✅ Đã chốt         | Rule-based TA deterministic (A1)                                              | Đổi ⇒ kiến trúc khác hẳn (RD-Agent(Q) + Qlib)                                           |
| D3 | Có bắt buộc EA MQL5? (00 §9 câu 3)        | ✅ Đã chốt (21/9/2026) | **Không.** Live trade qua NautilusTrader                                   | Điều kiện: mọi venue có adapter live (§3.5) — crypto ✅, IB (forex/stock/futures) ✅, SSI tự viết (GĐ 6). Broker chỉ-MT5 ⇒ adapter cầu nối, không phải EA |
| D4 | Ngưỡng kinh tế để cấp vốn (00 §9 câu 4) | 🔴 **Mở, chặn holdout + live** | —                                                                   | **Câu duy nhất còn mở.** Không chặn việc xây GĐ 0–6. Phải chốt **trước khi mở holdout đợt đầu**, vì ngưỡng PASS/FAIL của evaluator (§4.2) phụ thuộc vào nó |
| D5 | Vốn thực tế giai đoạn live                  | 🟡 Mặc định tạm    | < $10k                                                                        | Số instrument tối đa                                                                      |
| D6 | Đồng tiền base                              | 🟡 Mặc định tạm    | USD                                                                           | Tầng FX conversion                                                                          |
| D7 | Mục tiêu vol danh mục                       | 🟡 Mặc định tạm    | 10%/năm (Hurst-Ooi-Pedersen)                                                  | Vol targeting (§3.4)                                                                        |
| D8 | Drawdown tối đa chịu được                  | 🟡 Mặc định tạm    | 20%                                                                           | Kill-switch; là một phần của D4                                                            |
| D9 | Tham số dựng danh mục (ρ, K, tái cân bằng) | 🟡 Mặc định tạm    | 0.5 / 20 / hàng tháng (§3.2.1)                                              | ⚠️ Phải đóng băng **trước lần đánh giá danh mục đầu tiên** — sau đó mỗi lần đổi là một `portfolio_variant` |
| D10 | Ngưỡng drift Δ                            | 🟡 Mặc định tạm    | 0.05 / 0.15, Δ chuẩn hóa (§3.1.7)                                          | Hiệu chỉnh ở GĐ 2 trước khi dùng để tự động hủy                                        |
| D11 | Model Research Agent                      | 🟡 Mặc định tạm    | Non-reasoning, `gpt-oss-120b` (§3.1.9)                                      | A/B nội bộ ở GĐ 2                                                                          |
| D12 | Lỗ tối đa mỗi lệnh khi chạm stop (`max_risk_pct`) | 🟡 Mặc định tạm | 1% vốn                                                               | Trần rủi ro trong sizing (§3.4)                                                            |
| D13 | Lưới tham số PBO; số micro-scenario drift | 🟡 Mặc định tạm    | 3–5 giá trị trong ±30%, `M` ≤ 200; ~20 scenario                              | §3.2, §3.1.7                                                                               |
| D14 | Tỉ lệ ngân sách engine; chế độ | 🟡 Mặc định tạm | A 0.5 / B 0.4 / C 0.1; `isolated` ở GĐ 2 | §3.1.11 |
| D15 | Số lệnh / thời gian nắm giữ tối thiểu; tương quan indicator tối đa; số seed | 🟡 Mặc định tạm | 30 lệnh trên IS / 1 bar; 0.9; 3 seed | Gate ③, §3.3.1, §3.1.8 |
| D16 | Phạm vi tiến hóa | 🟡 Mặc định tạm | `joint` (cả entry + exit + regime) | §3.3.1 |
| D17 | Sharpe mục tiêu của MinBTL (gate ②) | 🟡 Mặc định tạm | 1.5 năm hóa; chỉ được hạ | ADR-0002. Ở 1.0, ~7 năm dữ liệu IS miễn phí chỉ đủ cho ~100–200 trial |
| D18 | Dữ liệu nghiên cứu (GĐ 0) | 🟡 Mặc định tạm | Binance spot, 1d, BTC/ETH/SOL/BNB/XRP theo USDT từ 2018; holdout = 12 tháng cuối | ADR-0002, §6.1 |

> ✅ **Mọi dòng 🟡 đều do người dùng tự cấu hình** (quyết định 21/9/2026) — con số trong bảng chỉ là giá trị mặc định khi người dùng không đặt. Cách cấu hình và giới hạn: §10.1.

**Tách bạch ba loại nội dung trong tài liệu này:**

- **Quyết định** — chỉ những dòng ✅ ở bảng trên.
- **Mặc định tạm** — dòng 🟡, và mọi con số có ghi "mặc định tạm" trong thân tài liệu.
- **Ví dụ API chưa kiểm chứng** — mọi đoạn code gọi thư viện bên ngoài (đặc biệt `purgedcv`, §6) là **minh họa**, chưa đối chiếu với chữ ký thật. Xem mục kiểm chứng ở [[07-VALIDATION-LAYER]] mục 7.

### 10.1. Cấu hình người dùng

Người dùng sửa **một file duy nhất**: `config/user.yaml`. Không sửa `evaluation.lock.yaml` bằng tay — file đó được **sinh ra** từ `user.yaml` lúc bắt đầu mỗi đợt nghiên cứu (§4.2), rồi khóa đọc-chỉ và hash.

```yaml
# config/user.yaml — mọi khóa đều tùy chọn; bỏ trống ⇒ dùng mặc định ở bảng §10

operational:            # NHÓM A — đổi bất cứ lúc nào, không ảnh hưởng kết quả thống kê
  live_capital: 10000           # D5
  base_currency: USD            # D6
  kill_switch_drawdown: 0.20    # D8
  models:                       # D11 — ghi vào generation_log.model_used
    research: gpt-oss-120b
    coding: <mid-tier>
    eval: <mid-tier>

research:               # NHÓM B — khóa theo đợt; đổi giữa đợt bị từ chối
  target_vol: 0.10              # D7
  max_risk_pct: 0.01            # D12
  portfolio:                    # D9
    max_corr: 0.5
    max_strategies: 20
    rebalance: monthly
  pbo_grid: {values_per_param: 5, range: 0.30, max_configs: 200}   # D13
  drift: {allow_below: 0.05, reject_at: 0.15, n_scenarios: 20}      # D10, D13
  engines: {quantevolve: 0.5, simple_loop: 0.4, random: 0.1}       # D14
  engine_mode: isolated         # D14 — isolated | collaborative
  evolve_scope: joint           # D16 — entry | exit | regime | joint
  constraints: {min_trades: 30, min_holding_bars: 1, max_indicator_corr: 0.9}   # D15
  seeds: 3                      # D15
  minbtl_target_sharpe: 1.5     # D17 — chỉ được hạ (chặt hơn)
  data: {exchange: binance, symbols: [BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT], timeframe: 1d, start: 2018-01-01, holdout_months: 12}   # D18
  calibration: {enabled: true, budget_per_strategy: 50}   # §3.2.1 bước 5b
  gates:                        # chỉ được SIẾT, không được NỚI (xem bên dưới)
    dsr_min: 0.95
    pbo_max: 0.5
  holdout_pass: <bắt buộc — D4> # thiếu khóa này ⇒ không mở được holdout
```

**Ba quy tắc cưỡng chế — để "cho cấu hình" không thành cửa hậu cho overfitting:**

| Quy tắc | Cơ chế | Vì sao |
|---|---|---|
| **Nhóm B khóa theo đợt** | Lúc mở đợt, `research:` được chép vào `evaluation.lock.yaml` + hash vào bảng `campaigns`. Sửa `user.yaml` giữa đợt ⇒ hệ thống **từ chối chạy** tới khi người dùng hoặc hoàn tác, hoặc mở đợt mới | Đổi tham số sau khi đã thấy kết quả = chọn lọc thêm một vòng mà ledger không đếm |
| **Đổi tham số dựng danh mục = một phương án mới** | Trong cùng đợt, được phép thử cấu hình `portfolio:` khác **qua lệnh riêng**, mỗi lần ghi `portfolio_variants` và cộng vào `N` (§3.2.1) | Cho phép thử nghiệm nhưng bắt trả giá bằng DSR |
| **Ngưỡng gate có sàn cứng** | `dsr_min` không được < 0.95, `pbo_max` không được > 0.5, `minbtl_target_sharpe` không được > 1.5 — giá trị lỏng hơn bị từ chối lúc nạp config. Siết chặt hơn thì được | Đây là mức tối thiểu để kết quả có nghĩa thống kê; nới ra thì validation layer mất tác dụng |

> Nhóm A đổi tự do vì nó không đi vào việc *chọn* strategy: vốn, đồng tiền base, kill-switch chỉ ảnh hưởng lúc live. Model routing có ảnh hưởng tìm kiếm nhưng không làm sai thống kê — nó được truy vết trong `generation_log`.

---

## 11. Kỳ vọng thực tế

Để tránh thất vọng giữa chừng, nhắc lại từ [[04-TRUONG-PHAI-B-HIEU-QUA]]:

| Cấu hình                         | Sharpe kỳ vọng |
| ---------------------------------- | ---------------- |
| 1 instrument                       | **~0.4**   |
| 15–30 instrument ít tương quan | 0.6–1.3         |
| 50 market đa asset class (trần)  | **~1.6**   |

**Ba ngưỡng hoài nghi:** Sharpe > 1.0 trên 1 instrument → nghi ngờ · > 2.0 bất kỳ đâu → gần như chắc chắn in-sample · > 3.0 → nghi lỗi phương pháp.

> 🆕 **Đừng lấy con số trong paper LLM-quant làm mốc kỳ vọng.** Khảo sát 22 hệ ([[08-LLM-QUANT-RESEARCHER]]): QuantaAlpha báo IR **3.3251**, MadEvolve test Sharpe **5.65**, AlgoEvolve Sharpe **5.60**. Đặt cạnh survey độc lập cùng thời điểm: **1/19** nghiên cứu khai báo mô hình chi phí giao dịch, **0/19** đạt mức tái lập cao nhất. Và AlgoEvolve tự thừa nhận Sharpe 5.60 là của *elite prompt*, còn trung bình quần thể chỉ **~1.21** — tức con số headline là best-of-N.
>
> **Quy tắc đọc paper trong lĩnh vực này:** thấy Sharpe > 3 ở daily/intraday thì tìm đủ ba thứ trước khi tin — (a) có phải best-of-N không, (b) chi phí giao dịch bao nhiêu bp, (c) khoảng OOS dài bao lâu. Thiếu bất kỳ cái nào ⇒ con số vô nghĩa.
>
> Con số duy nhất trong cả khảo sát vừa hợp lý vừa có OOS tử tế: **Sharpe 1.55 sau phí 5bp, OOS thuần 2024–2026** (Crypto Constrained Agents) — và đó là cross-sectional long-short, không phải trường phái B. Mốc của ta vẫn là bảng trên.

Và: quỹ CTA chuyên nghiệp với 60+ market **vừa lỗ trung bình −2.3%/năm trong 3 năm tính tới 8/2025**. Chuẩn bị tâm lý cho chuỗi âm dài.

---

## Một câu tóm tắt

> Kiến trúc này đặt cược vào ba thứ mà cả ngành đang thiếu: **harness xây trước agent**, **ledger không có đường vòng**, và **tầng Risk & Sizing được coi trọng hơn tầng Adapter**. Phần LLM sinh code là phần dễ và đã có nhiều người làm — phần quyết định là lớp từ chối.
