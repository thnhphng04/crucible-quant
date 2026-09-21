# 01 — Lộ trình task

§7 của kiến trúc được chia thành các task. Mỗi task ghi: **mục tiêu**, mục **kiến trúc** liên quan, **file**, **nghiệm thu khi** (các test chứng minh), và **cần** (task phụ thuộc). Trạng thái: ☐ chưa làm · ◐ đang làm · ✅ xong.

Quy tắc:
- Không bắt đầu task khi các task trong `cần` chưa ✅.
- "Nghiệm thu khi" là định nghĩa của "xong". Khi một task cưỡng chế một bất biến, thêm tên test vào [03-BAN-DO-BAT-BIEN-TEST.md](03-BAN-DO-BAT-BIEN-TEST.md).
- Ước lượng thời gian lấy từ kiến trúc (§7); kích thước từng task chỉ là phỏng đoán — cứ điều chỉnh.

---

## Giai đoạn 0 — Harness + ledger + bộ oracle (2–3 tuần)

> **Cổng giai đoạn (kiến trúc §7):** pipeline **loại được cả 4 cấp leaky oracle**, và chiến lược EMA crossover viết tay vượt mọi cổng của GĐ 0 với mọi kết quả nằm trong ledger. Cổng này là *"harness có chặn được gian lận không"*, không phải *"có gì sinh lời không"*.

Không có code agent trong giai đoạn này (P1).

### ✅ P0-01 Khung dự án
- **Mục tiêu:** công cụ cho repo, để mọi task sau bắt đầu từ trạng thái kiểm tra xanh.
- **File:** `pyproject.toml`, `src/quantcrucible/**/__init__.py`, `tests/`, `.github/workflows/ci.yml`, `.claude/`, `scripts/check_doc_mirror.py`, `config/user.yaml`.
- **Nghiệm thu khi:** `ruff`, `mypy --strict`, `lint-imports` (7 contract), `pytest`, `check_doc_mirror.py` đều đạt trên máy và trên CI.

### ✅ P0-02 Schema ledger + API chỉ-ghi-thêm
- **Mục tiêu:** nguồn sự thật duy nhất cho mọi trial (P2), phải có trước mọi thứ sinh ra trial.
- **Kiến trúc:** §4.1 (`generation_log`, `trials`, `portfolio_variants`, các view), §4.2 (`campaigns`, `holdout_access`).
- **File:** `ledger/schema.sql`, `ledger/db.py`, `tests/ledger/`.
- **Chi tiết:**
  - SQLite, chế độ WAL. Schema lấy nguyên DDL của kiến trúc, cộng các bổ sung ở [05](05-VAN-DE-MO.md) O4/O5 (quyết định bằng ADR).
  - Chỉ-ghi-thêm được cưỡng chế **trong database**, không chỉ trong Python: trigger `BEFORE DELETE` báo lỗi trên mọi bảng; `BEFORE UPDATE` báo lỗi trên mọi bảng (cụm ghi vào `trial_clusters`, ADR-0002); `campaigns.status` chỉ được đi `OPEN → FROZEN → BURNED`.
  - API Python cung cấp `log_event(...)`, `record_trial(...)`, `record_portfolio_variant(...)`, `trial_stats()`, `starved_cells()`. Không có `execute()` tổng quát.
- **Nghiệm thu khi:** test cho thấy DELETE/UPDATE bị từ chối ở mức SQL (kết nối `sqlite3` thô, bỏ qua API); lần insert `holdout_access` thứ hai cho cùng campaign báo lỗi; chuyển trạng thái sai báo lỗi; `trial_stats` trả về `n_raw`, `n_eff` (quay về `n_raw` khi `cluster_id` là NULL), `var_sr` trên một fixture.

### ✅ P0-03 Nạp config + khóa campaign
- **Mục tiêu:** `config/user.yaml` → cấu hình đã kiểm tra; mở campaign sẽ sinh `evaluation.lock.yaml`.
- **Kiến trúc:** §10, §10.1, §4.2.
- **File:** `config/schema.py` (frozen dataclass), `config/loader.py`, `config/lock.py`, `tests/config/`.
- **Chi tiết:**
  - Thiếu key ⇒ lấy mặc định từ bảng §10. Key lạ ⇒ báo lỗi (bắt lỗi gõ sai vốn sẽ âm thầm dùng mặc định).
  - Kiểm tra: `dsr_min ≥ 0.95`, `pbo_max ≤ 0.5` (sàn cứng), tỷ lệ các engine cộng lại bằng 1, enum (`engine_mode`, `evolve_scope`, `rebalance`), số dương.
  - `open_campaign()` chép `research:` vào `evaluation.lock.yaml` (+ hash template + cấu hình fill bi quan về sau), đặt file chỉ-đọc, lưu SHA256 của nó vào `campaigns`.
  - Mọi điểm khởi chạy đều gọi `assert_lock_matches()` — Nhóm B trong `user.yaml` khác với lock ⇒ từ chối chạy, kèm thông báo rõ (hoàn tác hoặc mở campaign mới).
  - `holdout_pass: null` hợp lệ cho đến khi P1-10 cố mở holdout.
- **Nghiệm thu khi:** test cho từng sàn cứng, key lạ, giá trị mặc định, lock chỉ-đọc, sửa Nhóm B giữa campaign bị từ chối, sửa Nhóm A được phép.
- **Cần:** P0-02.

### ✅ P0-04 Hợp đồng strategy + whitelist indicator
- **Mục tiêu:** hợp đồng lõi mà mọi strategy dùng (P3).
- **Kiến trúc:** §3.3, §3.1.6 (DSL), §3.3.1.
- **File:** `core/strategy/base.py` (`Signal`, `Strategy`, `Bars`, `Features`), `core/strategy/registry.py` (`ind`), `tests/core/strategy/`.
- **Chi tiết:**
  - `Signal` tự kiểm tra khi tạo: `strength ∈ [0,1]`; `stop_distance > 0` trừ khi `direction == "flat"`; `flat` ⇒ `strength == 0`.
  - `ind.*` là whitelist mà cổng tĩnh đối chiếu. Bắt đầu nhỏ: `ema`, `sma`, `atr`, `rsi`, `rolling_max`, `rolling_min`, `cross_up`, `cross_down`, `zscore`.
  - **Mọi indicator đều nhân quả.** Một test chung: với chuỗi ngẫu nhiên và `t` ngẫu nhiên, thay đổi các bar sau `t` không được làm đổi giá trị indicator tại `≤ t`. Chỉ thêm indicator mới khi test này đạt.
- **Nghiệm thu khi:** `Signal` từ chối giá trị sai; test nhân quả đạt với mọi indicator đã đăng ký; viết được EMA crossover ở §3.3.1.

### ✅ P0-05 Template + bộ phân tích TUNABLE
- **Mục tiêu:** vùng cố định và `EVOLVE-BLOCK`, được cưỡng chế bằng code.
- **Kiến trúc:** §3.3.1 (quy tắc 1–3, block có tên, `evolve_scope`).
- **File:** `core/strategy/template.py`, `core/strategy/tunable.py`, `tests/core/strategy/`.
- **Chi tiết:** phân tích marker `EVOLVE-BLOCK-START[: name]` / `END`; tính `SHA256(prefix) ‖ SHA256(suffix)` (block bị khóa theo `evolve_scope` tính là vùng cố định); phân tích `# TUNABLE: name = v, bounds=(a,b)` → tham số có kiểu (≤ 6, bounds hữu hạn, giá trị mặc định nằm trong bounds); cung cấp bộ sinh lưới PBO (D13: `values_per_param`, `range`, `max_configs`).
- **Nghiệm thu khi:** test khứ hồi trên ví dụ §3.3.1; sửa một byte ở vùng cố định làm đổi hash; 7 dòng TUNABLE bị từ chối; vi phạm bounds bị từ chối; phát hiện được việc thay cả file (thiếu/trùng marker).
- **Cần:** P0-04.

### ✅ P0-06 Khung cổng + EvaluationReport
- **Mục tiêu:** bộ khung pipeline để mọi cổng cắm vào.
- **Kiến trúc:** §3.2 (hợp đồng, bảng thứ tự), §3.3.2.
- **File:** `validation/gates.py`, `validation/report.py`, `tests/validation/`.
- **Chi tiết:**
  - `GateResult`, protocol `Gate`, `StrategyCandidate`. Pipeline chạy các cổng theo thứ tự của kiến trúc và dừng ở lần trượt đầu tiên.
  - **Mọi** `GateResult` (đạt hay trượt) đều ghi vào ledger. Trước cổng ③ → `generation_log`; từ cổng ③ trở đi → một dòng `trials` (§4.1).
  - **Fail closed:** exception bên trong cổng ⇒ REJECT + dòng ledger có traceback trong `detail`.
  - `EvaluationReport(public, private, feedback, error)`; `feedback` chỉ được sinh từ `public`.
- **Nghiệm thu khi:** test thứ tự; exception ⇒ loại + có dòng ledger; một test chứng minh `feedback` không thể đổi khi chỉ `private` đổi.
- **Cần:** P0-02.

### ✅ P0-07 Cổng ①a — guardrail tĩnh
- **Mục tiêu:** loại code xấu trước khi nó được chạy.
- **Kiến trúc:** §3.2 dòng ①a, §3.3.1 quy tắc 1–3, §3.1.6 (DSL), 07-VALIDATION-LAYER §9.
- **File:** `validation/guardrail.py`, `tests/validation/test_guardrail.py`. Whitelist AST nằm ở `validation/`; `dsl.py` của agent (GĐ 2) dùng lại nó thay vì tự định nghĩa.
- **Chi tiết:** duyệt AST của vùng tiến hóa: chỉ tên/lời gọi trong whitelist (`ind.*`, số học, so sánh, `Signal`, `self.p.*`); cấm `import`, `exec`/`eval`/`compile`, `open`, truy cập dunder, `getattr`, biến toàn cục; quét look-ahead tĩnh (shift âm, `iloc[i+k]`, đánh chỉ số vào tương lai); hằng số chưa khai báo (quy tắc 2, whitelist 0, 1, 14…); hash template + TUNABLE hợp lệ từ P0-05.
- **Nghiệm thu khi:** test dạng bảng với ≥ 20 đoạn code độc hại/sai, mỗi đoạn bị loại với đúng lý do; EMA crossover đạt; leaky oracle cấp 1 (`shift(-1)`) bị loại **tại đây**.
- **Cần:** P0-05, P0-06.

### ✅ P0-08 Sandbox Docker
- **Mục tiêu:** mọi code được sinh ra đều chạy cô lập.
- **Kiến trúc:** §3.3.3.
- **File:** `validation/sandbox.py`, `docker/sandbox.Dockerfile`, `tests/validation/test_sandbox.py` (marker `docker`).
- **Chi tiết:** tương đương `docker run --rm --network none --read-only --tmpfs /tmp --memory … --cpus … --env-clear` (`env -i`: môi trường rỗng — [ADR-0004](adr/0004-sandbox-docker.md)); dữ liệu IS mount chỉ-đọc; đầu ra = một JSON `EvaluationReport`; stdout/stderr bị cắt ngắn; hết timeout thì container bị kill. Vi phạm → sự kiện `SANDBOX_VIOLATION`.
- **Nghiệm thu khi:** các test gắn marker docker chứng minh: không có mạng (kết nối socket thất bại), env rỗng, không thấy `holdout/`, `config/`, `ledger/`, ghi ra ngoài tmp thất bại, timeout thì bị kill, đầu ra quá lớn bị cắt.
- **Cần:** P0-06. Xem [05](05-VAN-DE-MO.md) O6 (đặc thù Docker trên Windows).

### ✅ P0-09 Tầng dữ liệu + tách holdout
- **Mục tiêu:** bar crypto miễn phí cho nghiên cứu, với holdout được tách vật lý trước khi bất kỳ nghiên cứu nào chạm vào dữ liệu.
- **Kiến trúc:** §6.1 (`DataSource`), §4.2 (lớp 2), A7.
- **File:** `data/source.py` (protocol), `data/ccxt_source.py`, `data/store.py` (cache parquet dưới `data/` ở gốc), `data/holdout_split.py`.
- **Chi tiết:**
  - Thêm `ccxt` + một thư viện parquet (ghim phiên bản, đã kiểm tra license).
  - Bar ngày/4h cho một rổ crypto nhỏ (ví dụ BTC, ETH, SOL, BNB, XRP perp hoặc spot).
  - Bước tách ghi đoạn holdout vào `holdout/` ở gốc, đặt chỉ-đọc (ADR-0001: `attrib +R` / `icacls` trên Windows, `chmod 0400` ở nơi khác), và ghi SHA256 vào `holdout.lock`. Bộ nạp dữ liệu nghiên cứu **từ chối** mọi yêu cầu chồng lấn `holdout_range` của một campaign.
- **Nghiệm thu khi:** test offline với sàn giả (fixture); yêu cầu chồng lấn khoảng holdout báo lỗi; hash trong `holdout.lock` khớp; test tải dữ liệu có tồn tại dưới marker `network`.
- **Cần:** P0-02, P0-03.

### ✅ P0-10 Bộ chạy backtest (NautilusTrader) — phần cho GĐ 0
- **Mục tiêu:** chạy một `Strategy` trên bar qua NautilusTrader với mô hình fill bi quan, để cùng đường đó dùng được cho live sau này (P5).
- **Kiến trúc:** §3.5 (`FillModel` bi quan), §3.3.
- **File:** `execution/engine.py`, `execution/nautilus_bridge.py` (adapter Strategy → strategy của Nautilus), `tests/execution/`.
- **Chi tiết:**
  - Thêm `nautilus_trader` (ghim phiên bản; xem O3).
  - Tín hiệu tính trên bar **đã đóng** được khớp ở giá mở cửa của bar **kế tiếp** — không bao giờ trên chính bar sinh ra tín hiệu (vô hiệu hóa oracle cấp 2).
  - Phí theo biểu phí của sàn, trượt giá (tính thành phí taker cộng thêm theo bps — [ADR-0003](adr/0003-ngu-nghia-khop-lenh-nautilus.md)), lệnh limit chỉ khớp khi giá đi xuyên qua.
  - Sizing: cho đến P1-06, dùng `PlaceholderSizer` đặt tên rõ ràng (notional cố định × `strength`), sẽ bị P1-06 xóa.
  - Đầu ra: chuỗi lợi nhuận + danh sách lệnh → `EvaluationReport.public`.
- **Nghiệm thu khi:** một test golden tất định (bar cố định → lệnh/PnL chính xác); test tín hiệu ở bar *t* được khớp ở bar *t+1*; lệnh limit chỉ chạm giá thì không khớp.
- **Cần:** P0-04, P0-09.

### ✅ P0-11 Bộ leaky oracle + cổng ①b
- **Mục tiêu:** chứng minh harness bắt được gian lận — cổng của GĐ 0.
- **Kiến trúc:** 07-VALIDATION-LAYER §9, §3.2 dòng ①b.
- **File:** `validation/oracles/level{1..4}_*.py` (strategy ở dạng template), `validation/guardrail.py` (phần động), `tests/validation/test_oracles.py`.
- **Chi tiết:**
  1. `shift(-1)` — dự kiến chết ở ①a (tĩnh).
  2. Dùng giá đóng cửa của bar đang hình thành — kiểm tra động: chạy lại với giá đóng cửa của bar cuối bị nhiễu; tín hiệu của bar đó không được đổi.
  3. Indicator vẽ lại (repainting, ví dụ cửa sổ trung tâm / pivot tương lai) — test cắt cụt động: chạy trên dữ liệu cắt tại *t* so với dữ liệu đầy đủ; mọi tín hiệu `≤ t` phải giống hệt nhau.
  4. Thông tin công bố trễ (lỗi PIT) — một feature tổng hợp có độ trễ *d* nhưng được ghép tại thời điểm sự kiện; bị bắt bởi test cắt cụt trên feature đã ghép. Xem O8.
- **Nghiệm thu khi:** cả 4 oracle đều bị loại ở đúng cổng dự kiến và có dòng ledger, còn EMA crossover đạt ①b. Khi xây thực tế, cả bốn chết ở ①a và riêng ①b cũng bác bỏ cả bốn — [ADR-0005](adr/0005-leaky-oracle-va-guardrail-dong.md).
- **Cần:** P0-07, P0-08, P0-10.

### ✅ P0-12 Cổng ② MinBTL + ③ backtest IS; chạy đầu-cuối
- **Mục tiêu:** khép lại GĐ 0.
- **Kiến trúc:** §3.2 dòng ②, ③; 07-VALIDATION-LAYER §4.4; D15.
- **File:** `validation/statistical.py` (tạm thời chỉ MinBTL), `validation/is_gates.py`, `validation/run.py` + `cli validate`, `tests/e2e/test_phase0.py` — [ADR-0006](adr/0006-cong-minbtl-va-backtest-in-sample.md).
- **Chi tiết:** MinBTL tính từ `N` hiện tại; cổng ③ loại khi `trades < min_trades`, thời gian giữ trung bình `< min_holding_bars`, cặp indicator có `|ρ| > max_indicator_corr` trên IS; từ ③ trở đi mỗi ứng viên là một dòng `trials`.
- **Nghiệm thu khi:** `tests/e2e/test_phase0.py` đưa EMA crossover + cả 4 oracle qua ①a → ①b → ② → ③; các oracle bị loại, EMA được ghi là một trial; `ledger` có đúng các dòng `generation_log` + `trials` như dự kiến. **Đạt cổng GĐ 0.**
- **Cần:** P0-11.

---

## Giai đoạn 1 — Tầng validation đầy đủ + Risk & Sizing (2–3 tuần)

> **Cổng giai đoạn (kiến trúc §7):** chiến lược viết tay vượt mọi cổng; ledger tách đúng audit/trials; tính được `N_eff` + `V[SR]`; DSR/PBO khớp ví dụ số trong các paper gốc; test ½ size đạt.

### ✅ P1-01 Kiểm chứng `purgedcv` (task đầu tiên của GĐ 1)
- **Kiến trúc:** ghi chú §6, 07-VALIDATION-LAYER §7.
- **Mục tiêu:** quyết định *bọc lại* hay *tự cài đặt* DSR/PBO; giữ `purgedcv` cho CPCV/purging nếu đúng.
- **Chi tiết:** đọc source đã cài; ghim phiên bản; kiểm tra DSR có nhận `N` và `V[SR]` riêng không, và ma trận đầu vào cùng quy tắc chọn của PBO là gì.
- **Nghiệm thu khi:** có ADR ghi quyết định kèm các chữ ký đã kiểm tra; đóng O2. Xong: [ADR-0007](adr/0007-purgedcv-boc-lai-hay-tu-cai-dat.md).

### ✅ P1-02 DSR (+ PSR)
- **Kiến trúc:** §3.1.6, §4.1, 07 §4.
- **File:** `validation/statistical.py`.
- **Nghiệm thu khi:** khớp ví dụ số của Bailey & López de Prado (2014) đến độ chính xác đã nêu; test đơn điệu (nhiều trial hơn ⇒ DSR thấp hơn; `V[SR]` cao hơn ⇒ DSR thấp hơn); báo cáo ở cả `N_raw` và `N_eff`. Xong: [ADR-0008](adr/0008-dsr-don-vi-va-cach-dem-trial.md).

### ✅ P1-03 PBO qua CSCV + cổng ④
- **Kiến trúc:** §3.2 ("PBO thực sự kiểm tra gì"), D13.
- **Nghiệm thu khi:** tái hiện ví dụ của Bailey et al. (2017); test mô phỏng — ma trận nhiễu thuần ⇒ PBO ≈ 0.5 (không → 1), một cấu hình được cài edge sẵn ⇒ PBO → 0; tập cấu hình lấy từ lưới TUNABLE + các biến thể trong ledger, giới hạn ở `M`; cổng ④ loại khi PBO ≥ `pbo_max`. Xong: [ADR-0011](adr/0011-cong-4-tap-cau-hinh-va-cscv.md).
- **Cần:** P0-05, P1-01.

### ✅ P1-04 CPCV có purging/embargo + đường cong IS→OOS
- **Kiến trúc:** §3.2 (đường cong suy giảm), 07 §3.
- **Nghiệm thu khi:** purging/embargo loại đúng các quan sát chồng lấn trên một fixture dựng tay; Sharpe trung vị CPCV-OOS đi vào `EvaluationReport.private`, không bao giờ vào `public`. Xong: [ADR-0012](adr/0012-cpcv-cho-chien-luoc-theo-quy-tac.md) — tính trong gate ④ từ ma trận của nó.

### ✅ P1-05 `N_eff` (phân cụm ONC)
- **Kiến trúc:** §4.1 ("Ước lượng `N_eff`").
- **File:** `validation/n_eff.py`.
- **Nghiệm thu khi:** trên trial tổng hợp dựng từ *k* nguồn độc lập cộng nhiễu, ước lượng thu lại được *k* (± dung sai); ghi thêm một lần gom cụm vào `trial_clusters`; xem O10. Xong: [ADR-0009](adr/0009-n-eff-onc-kem-kiem-dinh-y-nghia.md).

### ✅ P1-06 Risk & Sizing
- **Kiến trúc:** §3.4 (toàn bộ), D7, D12.
- **File:** `core/sizing/vol_target.py`, `core/sizing/position_sizer.py`; xóa `PlaceholderSizer`.
- **Nghiệm thu khi:** **test ½ size** — nhân đôi cả `vol_estimate` và ATR ⇒ size giảm đúng một nửa khi trần stop không ràng buộc; trần stop tác động dưới dạng `min`, không bao giờ là hệ số nhân; `round_to_lot` tôn trọng bước/min/max; quy đổi FX về tiền tệ gốc; co giãn ở mức danh mục về lại `target_vol` với trần đòn bẩy và ước lượng lại IDM. Xong: [ADR-0010](adr/0010-risk-sizing-trong-duong-thuc-thi.md); đã xoá `PlaceholderSizer`.

### ✅ P1-07 Xây danh mục
- **Kiến trúc:** §3.2.1 bước 1–6, D9.
- **File:** `validation/portfolio.py`.
- **Nghiệm thu khi:** mỗi bước có unit test trên fixture (một đại diện mỗi ô, lọc `|ρ|` theo thứ tự hạng DSR, giới hạn K, risk parity đơn giản, tái cân bằng hằng tháng); `portfolio_hash` tất định và đổi khi bất kỳ thành viên/trọng số/quy tắc nào đổi; mỗi danh mục dựng ra là một dòng `portfolio_variants`. Xong: [ADR-0013](adr/0013-xay-danh-muc-va-kho-source-chien-luoc.md).

### ✅ P1-08 Cổng ⑤ — DSR của danh mục
- **Kiến trúc:** §3.2 dòng ⑤, hộp DSR ở §3.1.6, §4.1.
- **Nghiệm thu khi:** đầu vào chỉ lấy từ `trial_stats` + `total_portfolio_variants`; một test chứng minh thêm trial (bất kỳ engine, bất kỳ phán quyết) làm kết quả giảm; DSR không bao giờ tính theo từng ô (không có API cho việc đó). Xong: [ADR-0014](adr/0014-cong-5-dsr-danh-muc-va-pipeline-danh-muc.md).

### ✅ P1-09 Cổng ⑥′ — độ bền theo nguồn dữ liệu + độ nhạy chi phí
- **Kiến trúc:** §3.2 dòng ⑥′, §6.1 (dữ liệu hai tầng).
- **Nghiệm thu khi:** chạy lại với phí × 2 và trượt giá × 2; Sharpe > 0 và DSR không giảm quá ngưỡng khóa theo campaign; chạy lại trên dữ liệu broker/nguồn thứ hai nằm trong quy tắc Sharpe giảm ≤ 30%. Xong: [ADR-0015](adr/0015-cong-6p-do-ben-chi-phi-va-nguon-thu-hai.md); nguồn thứ hai = Gate.io (`cli data-fetch-second`).

### ✅ P1-10 Tiến trình đánh giá holdout + máy trạng thái campaign
- **Kiến trúc:** §4.2 (quy trình, 3 lớp), P6, D4.
- **File:** `holdout/evaluator_proc.py` (điểm khởi chạy riêng), `holdout/campaign.py`.
- **Chi tiết:** tiến trình riêng (CLI) chỉ nhận `portfolio_hash`; kiểm tra campaign đang `FROZEN`, `frozen_at < now`, hash `holdout.lock` khớp; từ chối nếu `holdout_pass` chưa đặt (D4); ghi `holdout_access`; in ra đúng `PASS` hoặc `FAIL`; chuyển campaign sang `BURNED`. Không module nào khác import `quantcrucible.holdout` (import-linter).
- **Nghiệm thu khi:** test trên holdout **tổng hợp** trong thư mục tmp: mở lần hai báo lỗi; file holdout bị sửa ⇒ từ chối; đầu ra là một token; `sharpe_oos` được lưu nhưng không bao giờ in ra. Xong: [ADR-0016](adr/0016-bo-danh-gia-holdout-va-dong-bang-campaign.md); đóng băng nằm ở `validation/freeze.py` (CLI không được import package của bộ đánh giá); D4 vẫn mở — bộ đánh giá từ chối tới khi `holdout_pass` được đặt.
- **Cần:** P0-02, P0-03, P0-09, P1-07.

### ☐ P1-11 Hiệu chỉnh (bước 5b)
- **Kiến trúc:** §3.2.1 bước 5b, §3.3.1 (tắt optimizer trong vòng lặp).
- **Nghiệm thu khi:** Optuna trên bounds của TUNABLE với ngân sách cố định; **mọi** lần đánh giá là một dòng `trials` với `source='param_opt'`; PBO và DSR được tính lại sau đó.
- **Cần:** P1-03, P1-08.

### ☐ P1-12 Chạy đầu-cuối GĐ 1
- **Nghiệm thu khi:** `tests/e2e/test_phase1.py` đưa EMA crossover (và vài biến thể viết tay) qua ⓪–⑥′, dựng danh mục và đóng băng nó; cổng giai đoạn ở trên được thỏa.

---

## Giai đoạn 2–6 — các mốc (chia thành task khi bắt đầu giai đoạn)

| Giai đoạn | Mốc | Kiến trúc |
|---|---|---|
| **2** Vòng lặp agent, chỉ crypto (6–8 tuần) | Định tuyến model + LLM client (chỉ dưới `agent/`); bộ dựng prompt kèm test rò rỉ key `private`; các agent Research / Coding / Evaluation; patcher (SEARCH/REPLACE + viết lại block); cổng ⓪ drift (lớp 1–2) + hiệu chỉnh ngưỡng Δ; feature map với biên bin cố định; đảo + di cư + test tích hợp đảo; pipeline bất đồng bộ; engine A/B/C + scheduler cưỡng chế tỷ lệ ngân sách trial; ≥ 150 thế hệ tự chạy; so sánh đa engine ở chế độ `isolated` với quy tắc quyết định khóa **trước** khi chạy; A/B model cho Research Agent | §3.1.x, §3.1.11, §3.3.1, §7 |
| **3** Mở rộng độ rộng (2–3 tuần) | 15–30 công cụ tương quan yếu; chế độ engine `collaborative`; không công cụ nào > 20% rủi ro | §3.1.11, §3.4 |
| **4** IB → forex + cổ phiếu quốc tế (3–4 tuần) | Adapter IB qua Nautilus; phiên/lịch giao dịch; nguồn dữ liệu Stooq; chỉ chỉ số/ETF (survivorship) | §3.5, §6.1 |
| **5** Futures (5–7 tuần) | Tự viết module roll (điều chỉnh tỷ lệ, roll theo OI/volume) từ dữ liệu TurtleTrader; khớp với một nguồn tham chiếu | §6.1 |
| **6** SSI → VN30F1M (3–6 tuần) | InstrumentProvider + DataClient + ExecutionClient; đối soát khi kết nối lại lúc đang có vị thế | §3.5 |

Trước lần mở holdout đầu tiên: **phải chốt D4** (kiến trúc §10).
