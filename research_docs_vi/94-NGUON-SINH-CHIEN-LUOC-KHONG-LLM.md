# Sinh chiến lược giao dịch KHÔNG dùng LLM: paper, phần mềm, repo và cách tối ưu tham số

> Ngày: 22/9/2026 · Phạm vi: các cách **sinh luật giao dịch tự động không dùng LLM** (liệt kê, ngẫu nhiên, tiến hóa GP/GE, học tăng cường) và cách **tối ưu, kiểm định tham số** đi kèm. Mục đích: làm nền cho việc chuyển trọng tâm sang **Engine C** (§3.1.11).
>
> ⚠️ **Mức kiểm chứng:** các con số trong báo cáo lấy từ abstract, trang tài liệu và kết quả tìm kiếm, **chưa đọc toàn văn** từng paper. License của thư viện ghi theo hiểu biết hiện có, **phải kiểm tra lại** trước khi thêm dependency (xem 02-CONVENTIONS). Mục 6 là nhận định của người viết, không phải quyết định.

---

## TL;DR

1. **Có 5 cách sinh chiến lược không dùng LLM:**
   - (a) **Liệt kê cả "vũ trụ luật"** rồi kiểm định đa giả thuyết (Sullivan-Timmermann-White: 7.846 luật; Hudson-Urquhart: khoảng 15.000 luật cho crypto).
   - (b) **Sinh ngẫu nhiên rồi lọc** (EA Studio, StrategyQuant chế độ random, Build Alpha).
   - (c) **Tiến hóa GP/GE/GA** (Allen-Karjalainen, Neely và cộng sự, Brabazon-O'Neill, Adaptrade, StrategyQuant chế độ genetic).
   - (d) **Alpha mining dạng công thức** bằng GP hoặc RL (AutoAlpha, AlphaGen, AlphaForge). Nhóm này thuộc trường phái A (factor cắt ngang), khác bài toán của dự án.
   - (e) **Quality-diversity** (MAP-Elites). Mình **không tìm thấy** paper nào áp dụng MAP-Elites thuần cho luật giao dịch. Gần nhất là PCA-QD trong AutoAlpha.
2. **Bằng chứng hiệu quả nhất quán về hướng:**
   - Lợi nhuận của luật kỹ thuật **có thật ở giai đoạn đầu** (trước thập niên 1990), rồi **suy giảm** (Neely-Weller-Ulrich 2009).
   - Lợi nhuận **biến mất khi tính phí giao dịch** hoặc khi **kiểm soát data-snooping** (STW 1999; Bajgrowicz-Scaillet 2012; Aronson: 6.402 luật, không luật nào có ý nghĩa thống kê).
   - Crypto là ngoại lệ một phần: nhiều luật vẫn có ý nghĩa sau khi kiểm soát FWER/FDR, nhưng **BTC không dự báo được ở giai đoạn out-of-sample** (Hudson-Urquhart 2021).
3. **Phần mềm thương mại** (StrategyQuant X, Adaptrade Builder, Build Alpha, EA Studio) dùng cùng một khuôn:
   - Sinh (ngẫu nhiên hoặc GP) trên dữ liệu IS.
   - Lọc bằng acceptance criteria (số lệnh tối thiểu, profit factor…).
   - Chạy một loạt robustness test: OOS, walk-forward (matrix), Monte Carlo trên lệnh/tham số/dữ liệu, noise test, **"Vs Random"**, đa thị trường.
   - **Không có công cụ nào đếm số lần thử để giảm phát (deflate) kết quả** như DSR của dự án.
4. **Tối ưu tham số có 4 họ chính:**
   - Lưới/vét cạn (MT5 slow complete, grid của backtesting.py, vectorbt).
   - Thuật toán di truyền (MT5 fast genetic).
   - Tối ưu dựa trên mô hình, kiểu Bayesian (Optuna TPE/GP/CMA-ES/NSGA-II/III trong freqtrade và Jesse; SAMBO trong backtesting.py).
   - Walk-forward optimization (Pardo, chỉ số WFE).
   - Hai hướng **chống overfit ngay trong hàm mục tiêu**: System Parameter Permutation (lấy **trung vị** của toàn bộ lưới làm ước lượng OOS) và GT-Score (hàm mục tiêu tổng hợp).
5. **Hàm ý chính cho Engine C:** kiến trúc hiện tại đã có phần lọc mạnh hơn mọi công cụ thương mại (DSR theo `N_eff`, PBO, holdout mở một lần). Phần còn thiếu nằm ở **bộ sinh**:
   - Văn phạm có kiểu (typed). Bằng chứng: bản strongly-typed VGP tốt hơn GP thường.
   - **Không** thiên lệch bộ sinh theo tần suất giao dịch (quyết định của người dùng, 22/9/2026): phí được backtest tính, việc đánh giá do các gate xử lý.
   - Tùy chọn một **kiểm định cấp "vũ trụ"** (Reality Check/SPA/FDR), khả thi vì C lấy mẫu từ một văn phạm đã biết trước.

---

## 1. Năm cách sinh chiến lược không dùng LLM

### 1a. Liệt kê cả vũ trụ luật và kiểm định đa giả thuyết

| Nghiên cứu | Vũ trụ luật | Kiểm định | Kết quả |
|---|---|---|---|
| Brock, Lakonishok, LeBaron (1992), *J. Finance* | MA và trading-range break, DJIA 1897–1986 | Bootstrap với 4 mô hình null (random walk, AR(1), GARCH-M, EGARCH) | Có lợi nhuận vượt trội có ý nghĩa. Đây là nghiên cứu **mà các bài sau cho là bị data-snooping** |
| Sullivan, Timmermann, White (1999), *J. Finance* | **7.846 luật** thuộc 5 họ: filter, MA, support/resistance, channel breakout, OBV | **White's Reality Check** (bootstrap) trên toàn vũ trụ | Luật tốt nhất có vẻ tốt trong mẫu, nhưng **10 năm out-of-sample không có lợi nhuận** |
| Bajgrowicz, Scaillet (2012), *JFE* | DJIA 1897–2011 | **False Discovery Rate** cùng persistence test | Không có cách nào chọn trước được luật tốt nhất cho tương lai. **Chỉ cần phí thấp cũng xóa hết lợi nhuận, kể cả trong mẫu**, vì các luật được chọn giao dịch quá dày |
| Aronson (2006), *Evidence-Based Technical Analysis* | **6.402 luật** trên S&P 500 | Reality Check cải tiến và **Monte Carlo permutation** | **Không luật nào có ý nghĩa thống kê**. Luật tốt nhất đạt hơn 10%/năm trong backtest, hoàn toàn do data-mining bias |
| Hsu, Hsu, Kuan (2010) | Luật kỹ thuật trên chỉ số tăng trưởng và mới nổi, ETF | **Stepwise SPA** (mạnh hơn Step-RC của Romano-Wolf) | Công cụ để **tìm ra nhiều luật có ý nghĩa nhất có thể** mà vẫn kiểm soát FWER |
| Hudson, Urquhart (2021), *Ann. Oper. Res.* | **Khoảng 15.000 luật**, BTC (2 sàn) và 3 coin khác | p-value bootstrap cho từng luật, rồi kiểm soát FWER và FDR | Nhiều luật vẫn có ý nghĩa, và lợi nhuận điều chỉnh rủi ro hơn buy-and-hold. **Nhưng BTC không dự báo được ở giai đoạn OOS** |

**Đặc điểm:** không gian luật **được định nghĩa trước** và **đếm được**, nên có thể kiểm định cả họ luật cùng lúc. Đây là điểm mạnh về thống kê mà cách tiến hóa không có.

### 1b. Sinh ngẫu nhiên rồi lọc (phần mềm bán lẻ)

- **EA Studio / Forex Strategy Builder:**
  - Generator ghép ngẫu nhiên indicator và điều kiện.
  - **Acceptance criteria** đóng vai trò bộ lọc, ví dụ tối thiểu khoảng 200 lệnh và profit factor ≥ 1.1.
  - **Reactor** nối các bước sinh, tối ưu và robustness test thành một dây chuyền chạy qua đêm.
  - Các robustness test: Monte Carlo (làm nhiễu giá, entry/exit, dữ liệu), multi-market, ngẫu nhiên hóa bar bắt đầu.
- **StrategyQuant X:**
  - Có hai chế độ: *random generation* (ghép ngẫu nhiên các building block: indicator, giá, toán tử) và *genetic evolution*. Tiến hóa chỉ chạy trên phần IS.
  - Bộ robustness test: Monte Carlo (bỏ lệnh ngẫu nhiên, làm nhiễu tham số và dữ liệu), **Walk-Forward Matrix** (xem cụm tham số ổn định), **System Parameter Permutation**, what-if.
- **Build Alpha:**
  - **Noise Test**: tạo khoảng 1.000 chuỗi giá bị cộng/trừ nhiễu rồi chạy lại chiến lược.
  - **Vs Random**: dựng các chiến lược "tốt nhất có thể" từ **tín hiệu ngẫu nhiên** trong cùng ràng buộc. Nếu chiến lược của bạn không thắng được chúng thì bỏ.

### 1c. Tiến hóa (GA / GP / GE)

| Nghiên cứu hoặc công cụ | Biểu diễn | Kết quả hoặc ghi chú |
|---|---|---|
| Allen, Karjalainen (1999), *JFE* | GP dạng cây luật trên S&P 500 | **Không** vượt buy-and-hold sau phí và điều chỉnh rủi ro. Có chút khả năng dự báo |
| Neely, Weller, Dittmar (1997), *JFQA* | GP trên 6 cặp FX, 1981–1995 | Lợi nhuận vượt trội OOS khoảng **1–7%/năm** |
| Neely, Weller, Ulrich (2009), *JFQA* | Kiểm tra OOS thật trên các luật FX đã công bố | Lợi nhuận thập niên 1970–80 là thật, nhưng **biến mất từ đầu thập niên 1990** với luật filter và MA. Luật phức tạp sống lâu hơn (phù hợp Adaptive Markets Hypothesis) |
| Brabazon, O'Neill (2004) | **Grammatical Evolution**: văn phạm BNF sinh luật | FX 1992–1997, lợi nhuận dương ở hold-out sau phí và trượt giá |
| Hu và cộng sự (2015), *Applied Soft Computing* (review) | 51 bài từ 650 bài trước năm 2013 | Tổng quan EC cho khám phá luật. Chỉ ra lỗ hổng trong cách đánh giá |
| Menoita, Silva (2025), GECCO (VGP) | Vectorial GP, có bản **strongly-typed** | **GP thường luôn thuộc nhóm tệ nhất, VGP strongly-typed luôn thuộc nhóm tốt nhất** |
| Adaptrade Builder | GP trên toàn bộ chiến lược: entry/exit, loại lệnh, stop | Giữ riêng đoạn validation, theo dõi đoạn test trong lúc build để phát hiện overfit, phạt độ phức tạp. Nhà phát triển tự nhận các biện pháp này chỉ **giảm** overfit chứ không loại bỏ được |
| Park, Irwin (2007), *J. Econ. Surveys* (review) | 95 nghiên cứu "hiện đại" | 56 tích cực, 20 tiêu cực, 19 hỗn hợp. Phần lớn **mắc lỗi data-snooping, chọn luật hậu nghiệm, ước lượng phí và rủi ro kém** |

### 1d. Alpha mining dạng công thức (GP, RL): trường phái A, chỉ để tham khảo kỹ thuật

- **AutoAlpha** (2020): GP phân tầng cộng **PCA-QD** (quality-diversity trên không gian PCA) và trần tương đồng AST, nhằm tránh các alpha dư thừa.
- **AlphaGen** (KDD 2023): RL sinh công thức dạng chuỗi token. Phần thưởng là **mức đóng góp vào mô hình kết hợp**, tức là tối ưu *tập* alpha chứ không từng alpha riêng.
- **AlphaForge** (AAAI 2025) và **Warm-start GP** (2024): cải tiến theo hướng sinh và kết hợp động.
- **Liên hệ với dự án:**
  - Ý tưởng "tối ưu cho cả tập" và "trần tương đồng AST" tương ứng với bước lọc tương quan |ρ| ≤ 0.5 khi xây danh mục (§3.2.1) và luật loại trùng.
  - Tuy vậy đây là bài toán factor cắt ngang, còn dự án thuộc trường phái B (luật TA tất định), nên **không chuyển nguyên được**.

### 1e. Quality-diversity (MAP-Elites) thuần

Mình không tìm thấy paper nào dùng MAP-Elites thuần, không có LLM, để sinh luật giao dịch. Nếu Engine C (hoặc một biến thể GP của C) dùng feature map §3.1.3, thì đó là **đóng góp mới** chứ chưa có tiền lệ để tham chiếu.

---

## 2. Bằng chứng hiệu quả: những điểm các nguồn đồng thuận

1. **Luật đơn giản (MA, filter) trên thị trường lớn đã "chết" từ khoảng đầu thập niên 1990** (Neely 2009; STW 1999; Park-Irwin 2007).
2. **Phí giao dịch là bộ lọc khắc nghiệt nhất.** Luật được chọn khi chưa tính phí thường giao dịch quá dày (Bajgrowicz-Scaillet 2012).
3. **Chọn "luật tốt nhất" trong một vũ trụ lớn gần như luôn là ảo giác** (Aronson; STW). Harvey-Liu-Zhu (2016) đề xuất ngưỡng **t > 3.0** thay cho 2.0, cùng cách "cắt bớt" (haircut) Sharpe khi kiểm định nhiều lần.
4. **Crypto còn chỗ cho luật kỹ thuật** trong mẫu (Hudson-Urquhart), nhưng bằng chứng OOS yếu, đặc biệt với BTC.
5. **Luật phức tạp hơn sống lâu hơn** (dự đoán của AMH, có hỗ trợ trong Neely 2009), nhưng luật phức tạp cũng dễ overfit hơn. Đây là mâu thuẫn mà các bộ lọc thống kê phải xử lý.

---

## 3. Tối ưu tham số trong thực tế

| Họ | Ví dụ công cụ | Ưu | Nhược |
|---|---|---|---|
| Lưới / vét cạn | MT5 *slow complete*; backtesting.py `method="grid"` (kèm heatmap, `constraint`); vectorbt (quét tham số dạng vector hóa) | Thấy được toàn bộ mặt hiệu suất, nhìn ra "cao nguyên" (plateau) | Bùng nổ tổ hợp. Mỗi điểm trên lưới là một lần thử |
| Di truyền (GA) | MT5 *fast genetic* | Nhanh trên không gian lớn | Chỉ trả về điểm tốt nhất, dễ rơi vào đỉnh nhọn |
| Dựa trên mô hình / Bayesian | Optuna (TPE, GP, CMA-ES, NSGA-II/III, QMC) trong **freqtrade hyperopt** (mặc định NSGA-III) và **Jesse** (Optuna + Ray, mặc định tối ưu Sharpe); **SAMBO** trong backtesting.py | Ít lần đánh giá hơn nhiều | Cộng đồng freqtrade tự nhận hyperopt dễ tạo "đỉnh" trong mẫu rồi thua lỗ ngoài mẫu |
| Walk-forward optimization | Pardo (1992, 2008); SQX Walk-Forward Matrix | Kiểm tra lặp lại trên nhiều cửa sổ OOS. **WFE = lợi nhuận OOS / lợi nhuận IS**, khuyến nghị > 0.5 và ≥ 8–10 cửa sổ | Mỗi lần tái tối ưu lại là thêm lần thử. Kết quả phụ thuộc cách chọn độ dài cửa sổ |

**Chống overfit ngay trong bước tối ưu:**

- **System Parameter Permutation** (Walton 2014, giải Wagner):
  - Dùng **toàn bộ** kết quả của lượt tối ưu vét cạn, lấy **trung vị** làm ước lượng hiệu suất tương lai, thay vì lấy điểm tốt nhất.
  - Chính tác giả chỉ ra hai điểm yếu: dải tham số chọn trước vẫn gây selection bias, và dùng lặp lại nhiều lần thì cũng bị data-snooping.
- **GT-Score** (Sheppert, arXiv 2602.00080, 1/2026): hàm mục tiêu tổng hợp gồm hiệu suất, ý nghĩa thống kê, độ ổn định và rủi ro giảm. Kiểm tra bằng walk-forward 9 lần chia và Monte Carlo 15 seed trên 50 cổ phiếu S&P 500 giai đoạn 2010–2024.
- **Phạt độ phức tạp** (Adaptrade) và **dừng build khi đoạn test xấu đi** (Adaptrade build-termination).

---

## 4. Bộ kiểm định đi kèm: công nghiệp và học thuật

| Nhóm | Kiểm định | Có trong công cụ nào | Tương ứng ở Crucible |
|---|---|---|---|
| Tách dữ liệu | IS/OOS, walk-forward, WF matrix | SQX, Adaptrade, EA Studio, Build Alpha | CPCV (gate ④), holdout mở một lần |
| Làm nhiễu | Monte Carlo trên lệnh (bỏ lệnh, đổi thứ tự), tham số, dữ liệu; Noise Test; ngẫu nhiên hóa bar bắt đầu | SQX, EA Studio, Build Alpha | Gate ⑥′ (chi phí ×2, nguồn dữ liệu thứ hai). Chưa có Monte Carlo trên lệnh hay dữ liệu |
| So với ngẫu nhiên | **Vs Random** (Build Alpha); Monte Carlo permutation (Aronson, Masters) | Build Alpha; sách của Masters | **Engine C** chính là baseline "vs random" ở cấp dự án |
| Đa thị trường | Multi-market test | EA Studio, SQX | Universe 5 symbol (D18) |
| Ổn định tham số | SPP, WF matrix, heatmap plateau | SQX, backtesting.py | Gate ④ PBO trên lưới ±30% |
| Đa giả thuyết | White RC, Hansen SPA, Romano-Wolf StepM, Step-SPA, FDR, ngưỡng t > 3 | Học thuật. Thư viện `arch` (Python) có SPA và StepM | DSR theo `N_eff` + `V[SR]`, MinBTL (gate ②) |
| Đếm lần thử và giảm phát | DSR, PBO, MinBTL | **Không có công cụ thương mại nào** | Có (P2, §4.1). Đây là điểm khác biệt cốt lõi của dự án |

---

## 5. Repo và thư viện liên quan

| Tên | Dùng cho | License (⚠️ cần kiểm tra lại) | Ghi chú cho dự án |
|---|---|---|---|
| **DEAP** | GA/GP tổng quát, có **strongly-typed GP** | LGPL-3.0 | Có thể dùng làm lõi cho một biến thể GP của C. Cần kiểm tra LGPL có hợp chính sách không |
| **gplearn** | GP symbolic regression và SymbolicTransformer, API kiểu scikit-learn | BSD-3 | Thiên về công thức số, không phải luật vào/ra |
| **PonyGE2** | Grammatical Evolution | GPL-3.0 | License GPL, chỉ nên tham khảo thiết kế |
| **Optuna** | TPE, GP, CMA-ES, NSGA-II/III | MIT | Đã dùng cho calibration 5b |
| **backtesting.py** | Grid, SAMBO, heatmap | **AGPL-3.0** | ❌ **Cấm dùng** theo chính sách không AGPL (CLAUDE.md) |
| **vectorbt** | Quét tham số dạng vector hóa | Apache-2.0 + Commons Clause | Chỉ dùng để sàng lọc thô, không thay Nautilus (P5) |
| **freqtrade** | Hyperopt với Optuna, nhiều loss function | GPL-3.0 | Tham khảo các loss function |
| **Jesse** | Optimize với Optuna + Ray | MIT | Tham khảo |
| **arch** (K. Sheppard) | **SPA, StepM, MCS** | NCSA | Ứng viên cho kiểm định cấp vũ trụ trên các trial của C |
| **alphagen** | RL alpha mining | Chưa kiểm tra | Trường phái A, chỉ tham khảo |

---

## 6. Hàm ý cho Engine C của Crucible (nhận định, không phải quyết định)

1. **Vai trò của C sẽ thay đổi, và đây là thay đổi kiến trúc.** Theo §3.1.11, C là **đối chứng** ("LLM có thắng may rủi không"), với 10% ngân sách, **lấy mẫu độc lập, không chọn lọc**.
   - Nếu C thành trọng tâm, cần chọn một trong ba hướng:
     - (i) **C thuần ngẫu nhiên**, giống EA Studio random.
     - (ii) **C cộng tiến hóa không LLM** (GP/GE trên văn phạm có kiểu, có thể cả MAP-Elites), giống StrategyQuant genetic hay Adaptrade.
     - (iii) **Liệt kê có hệ thống một văn phạm nhỏ**, giống STW hay Hudson-Urquhart.
   - Hướng (ii) là một **engine mới**. Khi đó C thuần ngẫu nhiên vẫn nên được giữ làm đối chứng cho chính GP.
   - Việc này đụng §3.1.11, D14 và vai trò của agent layer. Theo CLAUDE.md, đây là **quyết định của bạn**, và cần sửa `research_docs_vi/` trước.
2. **Văn phạm có kiểu là yêu cầu bắt buộc, không phải tùy chọn.**
   - Bằng chứng từ VGP: strongly-typed luôn thuộc nhóm tốt nhất.
   - Văn phạm có kiểu cũng thực thi luôn tính bất biến theo thang giá (§3.1.6), điều mà gate ①a hiện chưa chặn.
   - Việc này khớp với P2-04 trong plan phase 2.
3. **Không thiên lệch theo tần suất giao dịch** (người dùng quyết định, 22/9/2026). Bài học của Bajgrowicz-Scaillet (phí xóa lợi nhuận của luật giao dịch dày) đã được xử lý ở phía đánh giá: backtest Nautilus tính phí và trượt giá, gate ⑥′ chạy lại với chi phí ×2, DSR/PBO lo phần chọn lọc. Bộ sinh giữ trung lập để C vẫn là mẫu ngẫu nhiên không định kiến.
4. **C mở ra khả năng kiểm định cấp vũ trụ.** Vì văn phạm và phân phối lấy mẫu của C được biết trước, có thể chạy thêm SPA/StepM/FDR (thư viện `arch`) trên toàn bộ trial của C, bổ sung cho DSR ở cấp danh mục. Nên ghi thành open item, chưa phải gate.
5. **Ngân sách trial là giới hạn cứng.**
   - Vũ trụ kiểu STW (khoảng 7.800 luật) hay Hudson-Urquhart (khoảng 15.000) **vượt xa** số trial mà MinBTL cho phép với khoảng 7 năm IS ở `minbtl_target_sharpe` = 1.5.
   - Vì vậy "liệt kê hết" không khả thi. C phải **lấy mẫu có chủ đích** trong ngân sách đã khóa.
   - Tương tự, cấu hình của các phần mềm thương mại (hàng triệu chiến lược mỗi đêm) **không tương thích** với nguyên tắc P2 và DSR.
6. **Tối ưu tham số giữ nguyên nguyên tắc v0.5.** Mọi công cụ trên đều tối ưu **bên trong** vòng sinh (StrategyQuant genetic, MT5, hyperopt). Kiến trúc của dự án cố ý **không** làm vậy (§3.3.1). Có hai ý đáng lấy:
   - **SPP:** trung vị của lưới gate ④ có thể là một chỉ số `private` bổ sung cho PBO.
   - **Plateau:** đo độ phẳng quanh tham số làm tiêu chí xếp hạng, không dùng làm bộ tối ưu.
7. **Robustness test còn thiếu so với công nghiệp:** Monte Carlo trên thứ tự và việc bỏ bớt lệnh, noise test trên dữ liệu, ngẫu nhiên hóa bar bắt đầu. Các test này rẻ và có thể đặt ở gate ⑥′ hoặc thêm vào `private`. Mỗi test cần chốt xem có tính là trial không; theo §4.1 thì **không**, vì không có cấu hình mới nào được chọn.
8. **Kỳ vọng thực tế:** gộp mọi nguồn lại, luật TA đơn giản trên BTC/ETH khung ngày nhiều khả năng **không** vượt được DSR ≥ 0.95 ở cấp danh mục, nếu thiếu độ rộng (nhiều thị trường, tương quan yếu). Điều này khớp với cảnh báo ở §11 của kiến trúc.

---

## 7. Giới hạn của báo cáo

- Chưa đọc toàn văn các paper. Con số lấy từ abstract và tóm tắt.
- Không có benchmark độc lập nào so sánh các phần mềm thương mại. Mô tả tính năng lấy từ tài liệu của nhà phát triển.
- Không tìm thấy nghiên cứu MAP-Elites thuần cho luật giao dịch. Có thể có mà mình bỏ sót.
- License cần kiểm tra lại từ repo gốc.

---

## Nguồn

**Kiểm định vũ trụ luật và data-snooping**
- Brock, Lakonishok, LeBaron (1992). [Simple Technical Trading Rules and the Stochastic Properties of Stock Returns](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04681.x)
- Sullivan, Timmermann, White (1999). [Data-Snooping, Technical Trading Rule Performance, and the Bootstrap](https://onlinelibrary.wiley.com/doi/10.1111/0022-1082.00163)
- Bajgrowicz, Scaillet (2012). [Technical trading revisited: False discoveries, persistence tests, and transaction costs](https://www.sciencedirect.com/science/article/abs/pii/S0304405X1200116X)
- Hansen SPA, Romano-Wolf StepM: [Romano & Wolf (2005)](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1468-0262.2005.00615.x), [Hsu, Hsu, Kuan (2010) Step-SPA](https://www.sciencedirect.com/science/article/abs/pii/S0927539810000022), [thư viện arch: multiple comparison](https://arch.readthedocs.io/en/latest/multiple-comparison/multiple-comparison-reference.html)
- Harvey, Liu, Zhu (2016). [… and the Cross-Section of Expected Returns](https://academic.oup.com/rfs/article-abstract/29/1/5/1843824)
- Aronson (2006). [Evidence-Based Technical Analysis](https://onlinelibrary.wiley.com/doi/book/10.1002/9781118268315), [tóm tắt 6.402 luật](https://www.earnforex.com/guides/book-review-evidence-based-technical-analysis-by-david-aronson/)
- Masters. [Permutation and Randomization Tests for Trading System Development](http://www.timothymasters.info/market-trading.html)
- Hudson, Urquhart (2021). [Technical trading and cryptocurrencies](https://centaur.reading.ac.uk/85715/8/Hudson-Urquhart2019_Article_TechnicalTradingAndCryptocurre.pdf)
- Park, Irwin (2007). [What do we know about the profitability of technical analysis?](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-6419.2007.00519.x)

**Tiến hóa (GA/GP/GE)**
- Allen, Karjalainen (1999). [Using genetic algorithms to find technical trading rules](https://www.cs.montana.edu/courses/spring2007/536/materials/Lopez/genetic.pdf)
- Neely, Weller, Dittmar (1997). [Is Technical Analysis in the FX Market Profitable? A GP Approach](https://www.researchgate.net/publication/227348993_Is_Technical_Analysis_in_the_Foreign_Exchange_Market_Profitable_A_Genetic_Programming_Approach)
- Neely, Weller, Ulrich (2009). [The Adaptive Markets Hypothesis: Evidence from the FX Market](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1403345)
- Brabazon, O'Neill (2004). [Evolving technical trading rules for spot FX using grammatical evolution](https://link.springer.com/article/10.1007/s10287-004-0018-5)
- Hu và cộng sự (2015). [Application of evolutionary computation for rule discovery in stock algorithmic trading: A literature review](https://www.sciencedirect.com/science/article/abs/pii/S156849461500438X)
- Menoita, Silva (2025). [Evolving Financial Trading Strategies with Vectorial Genetic Programming](https://arxiv.org/abs/2504.05418)

**Alpha mining (GP/RL)**
- [AutoAlpha (arXiv 2002.08245)](https://arxiv.org/abs/2002.08245) · [AlphaGen (KDD 2023)](https://arxiv.org/abs/2306.12964), [repo](https://github.com/RL-MLDM/alphagen/) · [AlphaForge (AAAI 2025)](https://arxiv.org/html/2406.18394v1) · [Warm-start GP (arXiv 2412.00896)](https://arxiv.org/html/2412.00896v1)

**Phần mềm thương mại**
- StrategyQuant: [Robustness tests](https://strategyquant.com/blog/robustness-tests-and-analysis/), [Cross checks](https://strategyquant.com/doc/strategyquant/cross-checks-automated-strategy-robustness-tests/), [Features](https://strategyquant.com/features/)
- Adaptrade Builder: [How does it work](http://www.adaptrade.com/Builder/info1.htm), [Build algorithm](http://www.adaptrade.com/Builder/BuildAlgorithm.htm), [Preventing over-fitting](http://www.adaptrade.com/Newsletter/NL-OptTracking.htm)
- Build Alpha: [Vs Random test](https://www.buildalpha.com/vs-random-test/), [Robustness testing guide](https://www.buildalpha.com/robustness-testing-guide/)
- EA Studio: [Reactor](https://forexsb.com/wiki/eas-guide/reactor), [EA Studio](https://forexsb.com/expert-advisor-studio)
- Tổng quan: [Automated trading strategy generation: how it actually works](https://quanttradingtools.com/automated-trading-strategy-generation/)

**Tối ưu tham số**
- freqtrade: [Hyperopt](https://www.freqtrade.io/en/stable/hyperopt/), [Hyperopt and overfitting (issue #2472)](https://github.com/freqtrade/freqtrade/issues/2472)
- backtesting.py: [Parameter Heatmap & Optimization](https://kernc.github.io/backtesting.py/doc/examples/Parameter%20Heatmap%20&%20Optimization.html)
- Jesse: [Strategy Optimization](https://docs.jesse.trade/docs/optimize/)
- Pardo: [Walk-forward optimization (Wikipedia)](https://en.wikipedia.org/wiki/Walk_forward_optimization), [WFA chapter](https://www.oreilly.com/library/view/the-evaluation-and/9781118045053/pard_9781118045053_oeb_c11_r1.html)
- Walton (2014). [System Parameter Permutation](https://dx.doi.org/10.2139/ssrn.2423187), [ghi chú phê bình (QUSMA)](https://qusma.com/2014/05/01/notes-system-parameter-permutation/)
- Sheppert (2026). [The GT-Score](https://arxiv.org/html/2602.00080)

**Thư viện**
- [DEAP](https://github.com/DEAP/deap) · [gplearn](https://github.com/trevorstephens/gplearn) · [Quality-Diversity papers list](https://quality-diversity.github.io/papers.html)
