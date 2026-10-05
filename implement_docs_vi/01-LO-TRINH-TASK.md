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

> **Cổng giai đoạn (kiến trúc §7):** chiến lược viết tay vượt mọi cổng; ledger tách đúng audit/trials; tính được `N_eff` + `V[SR]`; DSR khớp ví dụ số công bố trong paper; PBO khớp các fixture tính tay theo định nghĩa CSCV/PBO và kết quả của một cài đặt độc lập trên cùng đầu vào ([ADR-0023](adr/0023-pbo-kiem-chung-bang-dinh-nghia-va-cai-dat-doc-lap.md)); test ½ size đạt.

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

### ✅ P1-11 Hiệu chỉnh (bước 5b)
- **Kiến trúc:** §3.2.1 bước 5b, §3.3.1 (tắt optimizer trong vòng lặp).
- **Nghiệm thu khi:** Optuna trên bounds của TUNABLE với ngân sách cố định; **mọi** lần đánh giá là một dòng `trials` với `source='param_opt'`; PBO và DSR được tính lại sau đó. Xong: [ADR-0017](adr/0017-calibration-moi-lan-danh-gia-la-mot-trial.md).
- **Cần:** P1-03, P1-08.

### ✅ P1-12 Chạy đầu-cuối GĐ 1
- **Nghiệm thu khi:** `tests/e2e/test_phase1.py` đưa EMA crossover (và vài biến thể viết tay) qua ⓪–⑥′, dựng danh mục và đóng băng nó; cổng giai đoạn ở trên được thỏa. Xong: [ADR-0018](adr/0018-quy-trinh-phase1-va-e2e.md) — quy trình ở `validation/research_run.py`, CLI `validate` (①a → ④) / `portfolio [--calibrate]` / `freeze`; chỉ dữ liệu tổng hợp; lần chạy trên dữ liệu thật chờ O16.

---

## Giai đoạn 2 — Engine C, không LLM, chỉ crypto (4–6 tuần)

> **Cổng giai đoạn (kiến trúc §7, v0.6):** ≥ 150 thế hệ C-gp tự chạy; mọi `s_new` vào ledger; feature map không sụp về một bin; test tích hợp đảo đạt; so sánh C-gp với C-random ở chế độ `isolated` với quy tắc quyết định khóa trước khi chạy. Engine A/B (LLM) hoãn (D19): giai đoạn này không có code LLM và không có cổng ⓪ drift — drift chỉ canh các lượt refine của LLM.

### ✅ P2-01 Chia giai đoạn 2 thành task + ADR điều phối
- **Kiến trúc:** §3.1.11, §3.1.8, §7.
- **Mục tiêu:** danh sách task này, các dòng bất biến của giai đoạn 2, và [ADR-0024](adr/0024-dieu-phoi-engine-c-va-trang-thai-tien-hoa-dan-xuat.md): điều phối bằng Python thuần, trạng thái tiến hóa dẫn xuất từ ledger, lập lịch theo trial thống kê.
- **Nghiệm thu khi:** `scripts/check_doc_mirror.py` đạt; ADR-0024 được chấp nhận.

### ✅ P2-02 Cấu hình cho engine C
- **Kiến trúc:** §10.1, D14, D19, D20.
- **File:** `config/schema.py`, `config/loader.py`, `config/user.yaml`.
- **Chi tiết:** khóa engine `gp`, `random`, `quantevolve`, `simple_loop`; `gp: {param_only_max, plateau_threshold}`; `campaign: {purpose: research|harness_test, trial_budget}` — đều thuộc Nhóm B, khóa theo campaign.
- **Nghiệm thu khi:** test loader — tổng tỷ lệ bằng 1; tỷ lệ A/B > 0 bị từ chối khi D19 còn hoãn chúng; `param_only_max` ∈ [0, 1]; `trial_budget` > 0; lock ghi bằng khóa engine cũ bị `assert_lock_matches` từ chối (cần campaign mới). Xong: `tests/config/test_loader.py::test_engine_c_is_the_focus_by_default`, `tests/config/test_lock.py::test_a_lock_with_the_old_engine_keys_is_refused`.
- **Cần:** P2-01.

### ✅ P2-03 Provenance của ứng viên + schema ledger v5
- **Kiến trúc:** §4.1, §3.1.5.
- **File:** `validation/run.py`, `validation/research_run.py`, `ledger/migration_005_*.sql`, `ledger/db.py`, `ledger/records.py`.
- **Chi tiết:** một `Provenance` (engine, seed, run_id, cell_id, đảo, hash của cha, loại đột biến) truyền qua `submit` vào `StrategyCandidate`, `generation_log` và `trials`; cột mới `island`; cha và loại đột biến nằm trong `detail`; hàm đọc theo (campaign, engine, seed).
- **Nghiệm thu khi:** provenance ghi đúng vào cả hai bảng; migration 4 → 5 chạy được trên DB v4; các trigger append-only vẫn giữ. Xong: `validation/run.py::Provenance`, `submit(..., params, provenance)`, `Ledger.trials(campaign, engine, seed)`, `Ledger.events_for`; cột island chỉ được thêm khi chưa có nên mọi migration vẫn idempotent.
- **Cần:** P2-01.

### ✅ P2-04 DSL có kiểu + bất biến theo thang giá ở cổng ①a
- **Kiến trúc:** §3.1.6, §3.3.1 luật 3, §3.1.11 luật 2.
- **File:** `core/strategy/registry.py` (`OpSpec`: kiểu vào/ra, loại tham số và biên, warm-up), `validation/guardrail.py`, `agent/dsl.py`.
- **Nghiệm thu khi:** test trước — so một chuỗi theo thang giá (vd `bars.close`) với hằng số hoặc TUNABLE là `AST_REJECT`; mọi chiến lược trong zoo vẫn qua ①a; unit test suy luận kiểu. Xong: bất biến theo thang giá là một phép kiểm tra đơn vị (thứ nguyên) trong `core/strategy/dims.py`, cổng ①a chạy nó sau whitelist cấu trúc; `OPS` trong registry mang đơn vị đầu ra, khoảng chu kỳ, khoảng giá trị và warm-up của từng toán tử (không cần `agent/dsl.py` riêng).
- **Cần:** P2-01.

### ✅ P2-05 Bộ lấy mẫu văn phạm + renderer = engine C-random
- **Kiến trúc:** §3.1.11 (C-random), §3.3.1.
- **File:** `agent/grammar.py`, `agent/engines/random_search.py`.
- **Chi tiết:** cây cú pháp có kiểu → text của block tiến hóa qua `template.render`; ≤ 6 TUNABLE, lấy đều trong biên; luôn sinh guard warm-up/hữu hạn; loại trùng theo `strategy_hash`; tất định theo seed; API của engine không nhận kết quả nào.
- **Nghiệm thu khi:** 1.000 mẫu liên tiếp qua ①a (property test); cùng seed cho cùng chuỗi; mọi loại clause đều được sinh; C-random không đọc được metric (test API + import). Lúc hoàn thành P2-05, văn phạm chỉ sinh stop ATR, không có TP; P3-25 sau đó bổ sung stop ATR/Bollinger và TP khóa cho campaign mới (O19, ADR-0036). Bằng chứng: `agent/grammar.py`, `agent/engines/random_search.py`, `tests/agent/engines/test_random_search.py`.
- **Cần:** P2-04.

### ✅ P2-06 Scheduler ngân sách trial + campaign thử harness
- **Kiến trúc:** §3.1.11 (ngân sách), §4.1, cổng ② (MinBTL).
- **File:** `agent/scheduler.py`, `validation/run.py`, `validation/portfolio.py`, `validation/freeze.py`.
- **Nghiệm thu khi:** hạn mức theo (engine, seed) không bao giờ bị vượt khi nhiều worker chạy đồng thời; campaign `harness_test` không đóng băng được, nên không bao giờ claim được holdout (vẫn được dựng danh mục: phép so sánh §3.1.11 cần DSR danh mục của từng engine); mở campaign mà `trial_budget` cộng `N` của ledger vượt mức MinBTL cho phép với độ dài IS thì bị từ chối. Xong: `agent/scheduler.py` (hạn mức chia theo tỷ lệ engine rồi theo seed, mỗi lượt giữ chỗ được chốt là trial / không phải trial), schema ledger v6 `campaign_purposes` với trigger từ chối FROZEN cho campaign thử harness, `validation/run.py::_check_trial_budget`.
- **Cần:** P2-02, P2-03.

### ✅ P2-07 Feature map biên cố định + archive riêng từng engine
- **Kiến trúc:** §3.1.3, §3.1.11.
- **File:** `agent/evolution/feature_map.py`, `agent/evolution/archive.py`, `config/lock.py` (biên nằm trong `derived`).
- **Chi tiết:** 6 chiều, 16 bin, biên khóa; category lấy từ phân loại AST của cây DSL; archive dựng lại từ ledger và kho source chiến lược; mỗi engine một archive.
- **Nghiệm thu khi:** biên chỉ lấy từ lock; giá trị ngoài khoảng rơi vào bin biên; dựng lại từ ledger cho đúng archive; các engine không bao giờ dùng chung archive ở chế độ `isolated`. Xong: `agent/evolution/feature_map.py` (biên lấy từ `derived.feature_map`, mặc định ở `validation/run.py::FEATURE_MAP`), `agent/evolution/archive.py` (theo (engine, seed), dựng từ trials + kết quả cổng + lineage); cổng ③ giờ giữ metric `public` trong detail và báo thêm `sortino_is`; category lấy từ genome (`grammar.categories`), ghi thành `descriptors` lúc submit.
- **Cần:** P2-03.

### ✅ P2-08 Pipeline bất đồng bộ, tắt an toàn, thông lượng cổng ④
- **Kiến trúc:** §3.1.8, O9, O14, [ADR-0004](adr/0004-sandbox-docker.md).
- **File:** `agent/pipeline.py`, `validation/sandbox.py`.
- **Chi tiết:** mỗi (engine, seed) một producer → hàng đợi prefetch → K slot đánh giá gọi `submit`; một luồng ghi ledger duy nhất; container sandbox gắn nhãn theo run và bị kill khi ngắt; đo thông lượng cổng ④ rồi cải thiện (chạy job lưới song song hoặc worker sống lâu) mà không rời NautilusTrader (P5).
- **Nghiệm thu khi:** ngắt giữa chừng không để lại container `qc-sandbox` nào (test docker); không lỗi khóa ledger với K slot; thông lượng đo được ghi trong một ADR; O14 được đóng hoặc thu hẹp. Xong: `agent/pipeline.py` (luồng điều phối + `workers` slot, mỗi slot một kết nối ledger — SQLite tuần tự hóa các lượt ghi), `SandboxRunner(label=…).kill_all()`, cửa sổ bar và sổ sách tầng Risk rẻ hơn (equity trùng từng bit, nhanh hơn ~40%); [ADR-0025](adr/0025-thong-luong-danh-gia-va-chay-dong-thoi.md).
- **Cần:** P2-06.

### ✅ P2-09 CLI `evolve` + e2e cho C-random
- **File:** `cli.py`, `tests/e2e/test_phase2_random.py`.
- **Nghiệm thu khi:** trên dữ liệu tổng hợp với ledger tạm, mọi ứng viên có dòng trong ledger, run dừng đúng ở hạn mức trial, feature map có nhiều hơn một ô. Xong: `agent/run.py::evolve` (hạn mức lấy từ ngân sách đã khóa của campaign, scheduler khởi đầu từ ledger, C-random phát lại chuỗi theo seed khi chạy tiếp), `cli evolve --engine random [--workers N]`; e2e còn kiểm tra lần chạy thứ hai không thêm gì.
- **Cần:** P2-05, P2-06, P2-07, P2-08.

### ✅ P2-10 Độ ổn định tham số ở cổng ④ (trung vị SPP, plateau)
- **Kiến trúc:** §3.2 (v0.6, D20).
- **File:** `validation/pbo_gate.py`.
- **Nghiệm thu khi:** cả hai tính từ lưới sẵn có (không backtest thêm, không tốn trial); cả hai là `public`; fixture lưới dựng tay cho đúng trung vị và plateau; PBO và CPCV vẫn là `private`. Xong: `pbo_gate.param_stability` (ứng viên ở dòng 0; plateau bằng 0 khi không có Sharpe dương); cổng ④ báo `spp_median_sharpe` và `plateau` dạng `public` và giữ chúng trong detail cho archive.
- **Cần:** P2-01.

### ✅ P2-11 Toán tử GP + điểm xếp hạng
- **Kiến trúc:** §3.1.11 (C-gp), §3.1.6 #1, D20.
- **File:** `agent/evolution/operators.py`, `agent/evolution/ranking.py`.
- **Chi tiết:** lai ghép cây con có kiểu, đột biến cây con và đột biến điểm, đột biến TUNABLE trong biên; con chỉ đổi tham số ≤ `param_only_max`; ghi loại đột biến; điểm xếp hạng = §3.1.6 #1 với trung vị SPP và plateau là thành phần phụ — công thức cụ thể trong một ADR.
- **Nghiệm thu khi:** mọi con hợp lệ về kiểu và qua ①a (property test); con chỉ đổi tham số giữ `strategy_hash` của cha; trần tỷ lệ giữ suốt một run; điểm xếp hạng chỉ đọc metric `public`. Xong: [ADR-0026](adr/0026-toan-tu-gp-va-diem-xep-hang.md) — thành phần DSR là DSR-rank của ADR-0013 (PSR so với ngưỡng đã deflate: không có DSR theo từng chiến lược, INV-42); cổng ③ thêm các moment của return IS vào `public`; lúc submit ghi `signature` của clause.
- **Cần:** P2-05, P2-10.

### ✅ P2-12 Chọn cha, đảo, di cư
- **Kiến trúc:** §3.1.4, §3.1.5.
- **File:** `agent/evolution/sampling.py`, `agent/evolution/islands.py`.
- **Nghiệm thu khi:** test tích hợp đảo — cha lấy từ đúng đảo đang xử lý; migrant không bị nhân bản ở đảo đích; con của migrant thuộc đảo đích; đảo rỗng không sinh con; đảo của từng cha được ghi log. Xong: `agent/evolution/islands.py` (N = C + 1 đảo, di cư top 10% theo vòng dưới dạng event audit `MIGRATION`, quần thể dựng lại từ ledger), `agent/evolution/sampling.py` (Eq. 1, α = 0,5; bạn lai chọn theo cùng cách).
- **Cần:** P2-07, P2-11.

### ✅ P2-13 Engine C-gp + e2e giai đoạn 2
- **File:** `agent/engines/gp_search.py`, `agent/loop.py`, `tests/e2e/test_phase2.py`.
- **Nghiệm thu khi:** trên dữ liệu tổng hợp, ≥ 150 thế hệ tự chạy, mọi `s_new` có trong ledger, feature map không sụp, test đảo đạt. Xong: `agent/engines/gp_search.py` (trạng thái dựng lại từ ledger ở mỗi đề xuất: entry, genome ghi cùng mỗi lần submit, di cư, xếp hạng), nối vào `agent/run.py` (không cần `loop.py` riêng); `tests/agent/test_gp_loop.py` chạy 150 thế hệ × 5 đảo qua pipeline và ledger thật với các cổng giả tất định; `tests/e2e/test_phase2.py` chạy C-gp và C-random qua các cổng thật trong docker.
- **Cần:** P2-09, P2-12.

### ✅ P2-14 Giám sát + dừng sớm
- **Kiến trúc:** §3.2 (đường IS→OOS), §3.1.11 (các chỉ số so sánh).
- **File:** `agent/monitor.py`.
- **Nghiệm thu khi:** độ phủ, hiệu suất trial và các ô đói được báo theo engine; phân kỳ IS→OOS (`is_oos_diverging`) dừng engine đó; có test trên fixture. Xong: `agent/monitor.py` — `engine_report`, event audit `DEGRADATION_CHECKPOINT` mỗi 25 trial (ứng viên giữ kỷ lục IS so với trung vị CPCV-OOS của nó; monitor là nơi duy nhất đọc metric private này), `EarlyStop` gắn vào pipeline (`RunStats.stopped`).
- **Cần:** P2-13.

### ✅ P2-15 Giao thức so sánh + lần chạy thật
- **Kiến trúc:** §3.1.11 (so sánh, quy tắc quyết định).
- **Chi tiết:** một ADR khóa trước khi chạy (chỉ số, định nghĩa độ dao động giữa các seed, `trial_budget`, hạn mức); báo cáo so sánh chỉ đọc; một campaign thử harness trên dữ liệu IS thật: C-gp và C-random × 3 seed.
- **Nghiệm thu khi:** ADR có trước trial đầu tiên của lần chạy (đối chiếu với ledger); báo cáo áp đúng quy tắc quyết định. Cả hai đều đạt. [ADR-0027](adr/0027-giao-thuc-so-sanh-giai-doan-2.md) (+ bổ sung v2) và [ADR-0028](adr/0028-giao-thuc-so-sanh-v3-monitor-chi-canh-bao.md) mang giao thức; `agent/compare.py`, `agent/runlock.py` (INV-72) và chia slot công bằng (INV-71) mang phần chạy.
- **Các lần chạy tới nay.** v1, campaign `c-20260922-181850`: dừng sau 121 trial — các slot không bao giờ rời `gp-s0`, và đợt review sau đó tìm thêm sáu lỗi. v2, campaign `c-20260923-090957`: **552 trial trong 14 giờ** (≈ 90 giây/trial, 8 worker), gp 100/100/100 và random 76/76/100. Kết cục **`stopped_early`: không có quyết định về engine**, vì monitor dừng `random-s0` và `random-s1` trước khi hết hạn mức. Ghi nhận chỉ như gợi ý: hiệu suất trial gp 56/45/44 so với random 19,7/31,6/26,0 (trung bình 48,3 so với 25,8, spread 6,66), độ phủ archive 45/34/32 so với 15/24/26 ô, số backtest cổng ④ mỗi chiến lược qua được 169 so với 240, DSR danh mục 0,0012 so với 0,0988 ở `N_eff` 145 — cả hai còn rất xa mức Sharpe thường niên ≈ 2,3 mà cổng ⑤ đòi ở `N` đó. Toàn bộ 673 trial vẫn nằm trong `N`.
- **Điều lần chạy v2 xác lập** là một lỗi trong chính harness của ta, không phải một kết quả về các engine: ở cả hai arm bị dừng, hai trong ba checkpoint là bản sao giống hệt, nên một lần đổi kỷ lục gánh toàn bộ độ dốc ([ADR-0028](adr/0028-giao-thuc-so-sanh-v3-monitor-chi-canh-bao.md)). Giao thức v3 sửa điều đó — ngân sách cố định, monitor chỉ cảnh báo (INV-77), và hash giao thức giờ phủ được cái mà monitor quyết định (INV-76).
- **v4, campaign `c-20260924-180916` — lần chạy đã hoàn tất.** 600/600 trial trong 11,9 giờ (12 worker, 71 giây/trial), mọi arm dùng hết quota, không arm nào starve, `unfinished` rỗng: campaign đầu tiên thỏa `complete` (INV-73). Kết quả **`gp_beats_random`** — trial efficiency 53/48/50 so với 20/30/25, trung bình 50,33 so với 25,00 trên spread giữa các seed là 5,00. Ba arm nhận `DEGRADATION_WARNING` (gp-s1, random-s0, random-s1) và cả ba vẫn chạy tiếp, đúng mục đích của giao thức v3. Ranking margin (INV-81) trên dữ liệu thật đạt 2,72–9,83. **Không danh mục nào triển khai được**: DSR 0,0144 (gp) và 0,0201 (random) so với `dsr_min` 0,95, đúng như §11 kiến trúc cảnh báo với luật đơn giản trên crypto khung ngày. Phán quyết là "tiến hóa hơn may rủi", không phải "cái này kiếm được tiền".
- **Cần:** P2-13, P2-14.

---

### ✅ P2-16 Số hạng chính của điểm xếp hạng, và giao thức mang nó
- **Kiến trúc:** §3.1.6 #1, §3.1.11 · **Sửa đổi:** [ADR-0026](adr/0026-toan-tu-gp-va-diem-xep-hang.md).
- **Mục tiêu:** số hạng lợi nhuận trong điểm xếp hạng của C-gp quyết định lại việc chọn cha mẹ, một phép kiểm vĩnh viễn bắt được lớp defect đã che giấu nó, và protocol hash bao phủ hàm xếp hạng như nó đã bao phủ monitor.
- **Chi tiết:** số hạng chính trở thành thứ hạng của DSR-rank trong quần thể (đồng hạng chia trung bình, [0, 1]); không λ nào đổi; `term_dispersion` + `ranking_margin` báo cáo độ phân tán theo (engine, seed), không bao giờ dừng một arm; `PROTOCOL["ranking"]` hash λ như dữ liệu và `ranking_fingerprint()` hash thứ tự lựa chọn, giao thức v4.
- **Đạt khi:** test hồi quy tái hiện hiện tượng nén của campaign `c-20260923-090957` và cho thấy dạng PSR tuyệt đối trượt INV-81 trong khi dạng thứ hạng đạt; các λ được protocol hash bao phủ (INV-82); các test ranking cũ đạt nguyên trạng. Đã xong: [ADR-0030](adr/0030-diem-chinh-cua-xep-hang-gp-la-thu-hang-quan-the.md), `agent/evolution/ranking.py`, `tests/agent/evolution/test_ranking.py` (mirror mới; các test ranking đã chuyển khỏi `test_operators.py`), `ranking_margin` trong `agent/monitor.py`, `RANKING_SCENARIOS` + `ranking_fingerprint` trong `agent/compare.py`, và bộ đo hiệu chỉnh `tests/agent/test_ranking_recovery.py` (thị trường mô phỏng, gate giả, ledger riêng, ≈ 105 giây). Protocol hash `0cafe016ef5f…`.
- **Cần:** P2-14.

---

## Giai đoạn 3 — Chiến lược riêng theo (instrument, direction) trên perpetual USDT-M (6–10 tuần)

Kiến trúc §3.4 (đã viết lại), §3.5, §7. Yêu cầu: `PLAN-PER-INSTRUMENT-STRATEGIES.vi.md`, chốt 25/9/2026.
Quyết định: ADR-0031 (rút P4, `Q = R/d`), ADR-0032 (tài khoản perpetual ở host), ADR-0033 (scope, protocol v5).

### ✅ P3-01 Spike coverage dữ liệu perpetual
**Mục tiêu:** biết Binance USDT-M công bố những gì, trước khi ghi bất kỳ quyết định nào. **Kiến trúc:** §6.1, D18. **Cần:** —
**File:** `scripts/perp_coverage.py` (chỉ đọc, không đụng `src/`).
**Kết quả (25/9/2026):** cả ba chuỗi đều có cho cả năm contract. SOLUSDT là ràng buộc, niêm yết 2020-09-14, cho **5,03 năm** IS sau khi cắt holdout 12 tháng. MinBTL ở target 1,5 chặn N ở **1.475** trong khi `n_eff` đã là 290 — headroom 1.185. Bracket cần endpoint có chữ ký. Mark 1m khoảng 3,5 triệu dòng mỗi contract.
**Đạt khi:** ✅ bảng coverage và một dòng go/no-go, ghi vào ADR-0031.

### ✅ P3-02 Rút P4 trong kiến trúc
**Mục tiêu:** luật sizing đổi ở nguồn chân lý trước khi đổi trong code. **Kiến trúc:** §3.4, §7, §10 (D7, D12, D18). **Cần:** P3-01
**File:** `research_docs_vi/Architecture_Design.md` trước, rồi mirror EN; `CLAUDE.md`; `implement_docs/adr/0031-*.md` + mirror VI; `03-BAN-DO-BAT-BIEN-TEST.md` (rút INV-05, thêm dải INV-90..98).
**Đạt khi:** `uv run python scripts/check_doc_mirror.py` xanh; ADR-0031 ghi ai quyết, thứ gì thay INV-05, và rằng `idm` / `portfolio_scale` / `target_vol` bị xoá chứ không phải đánh dấu lỗi thời.

### ✅ P3-03 `Q = R/d`: thay bộ test INV-05 trước, rồi tới code
**Mục tiêu:** cỡ vị thế là rủi ro chia khoảng stop, chứng minh bằng test viết trước. **Kiến trúc:** §3.4, ADR-0031. **Cần:** P3-02
**File:** `tests/core/sizing/test_position_sizer.py`, `tests/execution/test_risk.py`, rồi `core/sizing/position_sizer.py`, `core/sizing/vol_target.py`, `execution/risk.py`, `config/schema.py`, `config/lock.py`, `validation/run.py`, `validation/is_gates.py`.
**Đạt khi:** bốn test INV-90 xanh; ba test INV-05 đã biến mất; lock mang `derived.sizing.max_leverage` bị từ chối bằng đúng lỗi "mở campaign mới" đang có, thay vì âm thầm trộn hai luật sizing.

### ✅ P3-04 `direction` thành trục của grammar, và guardrail sai hướng
**Mục tiêu:** genome của một scope render đúng hướng của nó; tín hiệu sai hướng chết ở gate ①a. **Kiến trúc:** §3.3.1, ADR-0031. **Cần:** P3-02
**File:** `agent/grammar.py` (literal `Signal("long", …)` được render), `validation/guardrail.py`.
**Đạt khi:** INV-91; cùng một tập clause render theo hai hướng cho `strategy_hash` khác nhau.

### ✅ P3-05 Margin model theo bracket
**Mục tiêu:** initial margin, maintenance margin, leverage thấp nhất có thể cấp vốn, và giá thanh lý isolated, trên một bảng bracket. **Kiến trúc:** §3.5, ADR-0032. **Cần:** P3-01, P3-03
**File:** thêm mới `execution/margin.py`.
**Đạt khi:** ✅ `tests/execution/test_margin.py` — maintenance margin liên tục qua biên tier (đó chính là việc của số hạng `amount`), biên thuộc về tier dưới, leverage vượt bracket bị từ chối, và thanh lý nằm dưới entry của long và trên entry của short.
**Chệch hướng:** việc chuyển venue (`CryptoPerpetual`, `OmsType.HEDGING`, `AccountType.MARGIN`) dời sang P3-06. Đứng riêng nó không có test nào trượt đúng lý do — một short chỉ quan sát được khi `TargetSizer` mang side. Bảng bracket vẫn là **dữ liệu**: `fetchLeverageTiers` là endpoint có chữ ký, nên giá trị thật đến cùng campaign lock và được ghi ở đó như một giả định.

### ✅ P3-06 Venue giao dịch cả hai phía
**Mục tiêu:** tín hiệu short mở được short kèm stop bảo vệ của nó. **Kiến trúc:** §3.5, P3. **Cần:** P3-04, P3-05
**File:** `execution/nautilus_bridge.py` (`CryptoPerpetual`, `AccountType.MARGIN`, target có dấu, stop phía short, `FillRecord.position_side`), `execution/engine.py` (`_round_trips`, `assert_complete`).
**Đạt khi:** ✅ `tests/execution/test_hedge.py` — short được giao dịch chứ không chỉ được đếm rồi bỏ, và vòng giao dịch của nó được đếm; thiếu ý sau cùng thì gate ③ loại mọi chiến lược short vì `min_trades` với lý do chẳng liên quan gì tới chiến lược. Các test INV-35 giữ nguyên.
**Chệch hướng: `OmsType.NETTING`, không phải `HEDGING`.** Một backtest chạy một strategy trên một hướng, nên engine này không bao giờ giữ cả hai chân của một contract — sổ hai chiều là tính chất của joint-account replay (P3-08), vốn nằm ở host. `HEDGING` còn sinh `PositionId` mới cho mỗi lệnh vào, khiến stop `reduce_only` không còn gì để giảm và âm thầm không khớp.
**Hai test bị thay, không bị xóa:** `test_short_signal_means_flat_on_spot` khẳng định đúng hành vi mà task này gỡ bỏ → `test_a_short_signal_is_traded_not_dropped`. `test_engine_stop_is_not_a_silent_truncation` kích hoạt guard bằng cách làm cạn tài khoản cash, thiếu thứ mà venue margin không làm → guard nay là `assert_complete` và được test trực tiếp.

### ✅ P3-07 Funding, mark price, thanh lý, đường giá trong nến
**Mục tiêu:** ba thứ Nautilus không mô hình. **Kiến trúc:** §3.5, ADR-0032. **Cần:** P3-05, P3-06
**File:** thêm mới `execution/perp_account.py`, thêm mới `core/path_summary.py` (P3-16), `validation/sandbox.py` và `sandbox_runner.py` (kênh dữ liệu vào container mở rộng).
**Đạt khi:** INV-93, INV-94; first touch khớp với quét vét cạn theo phút; chạm đồng thời bị gắn cờ mơ hồ và giải theo kết quả bất lợi hơn; nến phút không bao giờ vào sandbox. **Bằng chứng (kiểm tra phase 2026-09-30):** `tests/core/test_path_summary.py::test_first_touch_matches_a_brute_force_minute_scan`, `::test_a_simultaneous_touch_is_reported_as_ambiguous`; INV-93 và INV-99 ✅; INV-94 đúng trên fixture, nửa nguồn thật của nó thuộc P3-24.

### ✅ P3-08 Backtest tài khoản chung và luật kết nạp
**Mục tiêu:** một lần replay tài khoản thay cho N luồng tự tài trợ. **Kiến trúc:** §3.4, §3.2.1, ADR-0032. **Cần:** P3-07
**File:** `execution/engine.py`, thêm mới `execution/admission.py`, `validation/sandbox_runner.py`.
**Đạt khi:** INV-92; equity tài khoản đối soát khớp tổng đóng góp của các slot; cùng seed tái hiện cùng đường equity. **Bằng chứng (kiểm tra phase 2026-09-30):** INV-92 ✅; `tests/execution/test_joint_account.py::test_account_equity_reconciles_with_the_sum_of_slot_contributions`, `::test_the_same_input_reproduces_the_same_curve`.

### ✅ P3-09 Nguồn dữ liệu perpetual và manifest
**Mục tiêu:** dữ liệu perpetual có hồ sơ toàn vẹn mà parquet spot chưa bao giờ có. **Kiến trúc:** §6.1, D18. **Cần:** P3-01, P3-05
**File:** thêm mới `data/perp_source.py`, `data/store.py`, thêm mới `data/manifest.py`.
**Đạt khi:** INV-94; manifest ghi checksum, coverage và nguồn; nến spot v4 không thoả được một yêu cầu perpetual; `data` vẫn không import `execution` (import-linter).

### ✅ P3-10 Cắt holdout perpetual — tách biệt, ghi một lần
**Mục tiêu:** một holdout thứ hai không bao giờ chạm cái thứ nhất. **Kiến trúc:** §4.2, P6. **Cần:** P3-09
**File:** `data/holdout_split.py`, `cli.py`, `holdout/evaluator_proc.py`.
**Đạt khi:** có khoá và manifest riêng; khoá cũ không bao giờ bị mở hay ghi đè; lần cắt thứ hai bị từ chối; INV-08 đúng với khoá mới. **Bằng chứng (kiểm tra phase 2026-09-30):** hoàn tất qua P3-22 — `tests/data/test_perp_holdout.py::test_an_existing_lock_is_never_overwritten`, `::test_the_second_carve_does_not_read_the_first`, `tests/data/test_holdout_split.py::test_second_carve_refused`.

### ✅ P3-11 Migration 007 — cột scope
**Mục tiêu:** ledger mang được scope, và 1.325 hàng cũ giữ nguyên ý nghĩa mà không bị đụng. **Kiến trúc:** §4.1, ADR-0033. **Cần:** —
**File:** thêm mới `ledger/migration_007_scope.sql`, `ledger/db.py`, `ledger/records.py`.
**Đạt khi:** INV-95 — hàng cũ được giải lúc đọc, không bao giờ bằng UPDATE, thứ mà trigger append-only cấm. Migration chạy trên bản copy tạm, không bao giờ trên ledger thật.

### ✅ P3-12 Đơn vị tìm kiếm thành (instrument, direction, engine, seed)
**Mục tiêu:** mỗi scope là một arm độc lập. **Kiến trúc:** §3.1.11, ADR-0033. **Cần:** P3-04, P3-11
**File:** `agent/scheduler.py`, `agent/pipeline.py`, `agent/run.py`, `agent/engines/`, `agent/evolution/archive.py`, `islands.py`, `validation/run.py` (`universe=tuple(is_data)`), `validation/gates.py`.
**Đạt khi:** INV-96; INV-61 và INV-71 mở rộng và vẫn xanh; INV-65 mạnh thêm — engine vẫn không bao giờ thấy instrument của nó.

### ✅ P3-13 Gate theo scope, danh mục theo slot, protocol v5
**Mục tiêu:** gate ③④ trên một contract và một hướng; gate ⑤ trên một lần replay tài khoản chung. **Kiến trúc:** §3.2, §3.2.1, §3.1.11. **Cần:** P3-08, P3-12
**File:** `validation/is_gates.py`, `pbo_gate.py`, `portfolio.py`, `robustness.py`, `calibration.py`, `agent/compare.py`, `agent/monitor.py`, `review/repository.py`.
**Đạt khi:** INV-97, INV-98; lưới PBO không bao giờ trộn scope; INV-42 giữ nguyên, nên `trial_stats` vẫn toàn cục; campaign v4 bị từ chối dưới v5 (INV-74).

### ✅ P3-14 Timeframe là knob thật
**Mục tiêu:** đổi `research.data.timeframe` sang `4h` phải chạy end-to-end. **Kiến trúc:** D18, §3.2. **Cần:** P3-13
**File:** `validation/portfolio.py` (`Member.from_dict` phải từ chối timeframe thiếu chứ không mặc định nó), `validation/run.py` (`DEFAULT_LOOKBACK` đếm theo nến), `config/schema.py`, `validation/is_gates.py`.
**Đạt khi:** cả đường ống chạy ở 4h trên cùng fixture với niên hoá đúng; funding rơi đúng ở nến 1d, 4h và 8h.

### ✅ P3-15 Review API, UI, hồi quy legacy, cổng chất lượng
**Mục tiêu:** kết quả mới đọc được và kết quả v4 vẫn đọc được. **Kiến trúc:** ADR-0029, ADR-0033. **Cần:** P3-14, P3-10
**File:** `review/repository.py` (`READABLE_SCHEMAS` = v6 và v7, endpoint equity tài khoản), `review/app.py`, `ui/src/`.
**Đạt khi:** equity ban đầu cộng tổng đóng góp bằng equity tài khoản trong sai số làm tròn; campaign v4 vẫn hiển thị được; INV-79 và INV-80 giữ nguyên; toàn bộ cổng chất lượng xanh. **Bằng chứng (kiểm tra phase 2026-09-30):** hoàn tất qua P3-23 — `tests/review/test_account_endpoint.py::test_the_reconciliation_is_served_as_a_measured_residual`; reader nhận schema v6–v9 (`tests/review/test_review_api.py`).

## Giai đoạn 3 (tiếp) — nối tầng perpetual vào

Các module phía trên đã dựng và đã test; **không gì ngoài cụm của chính chúng import tới**. Nguyên
nhân gốc là một dòng: `SandboxJob.bars` là dữ liệu duy nhất container từng thấy, và nó chỉ mang
nến giao dịch. Các task này nối dây, tải dữ liệu thật, và đưa giai đoạn qua cổng của nó.

### ✅ P3-16 `path_summary` xuống `core/`, cắt đoạn tại mốc funding
**Mục tiêu:** một mức thay đổi giữa nến được trả lời chính xác. **Kiến trúc:** §3.5, ADR-0032. **Cần:** —
**File:** `execution/path_summary.py` → `core/path_summary.py` (`data` không được import `execution`, mà summary dựng lúc fetch), `execution/perp_account.py` (`resolve_bar` nhận một giá thanh lý cho mỗi đoạn), ADR-0032 và mirror VI của nó.
**Đạt khi:** hai path có summary cả nến giống hệt nhau cho kết quả khác nhau khi một ngưỡng có hiệu lực giữa nến; `lows` lấy từ low của phút và `highs` từ high của phút; phút thiếu bị từ chối, không bao giờ điền khuyết; `lint-imports` xanh.

### ✅ P3-17 `PerpSource` dùng được thật
**Mục tiêu:** năm năm của bốn chuỗi, nối lại được. **Kiến trúc:** §6.1, D18. **Cần:** P3-16
**File:** `data/perp_source.py` (hiện lấy đúng một trang 1000 dòng, và `end` không được dùng), `data/store.py` (`file_name` để nguyên dấu hai chấm của `BTC/USDT:USDT` trong tên file), thêm mới `data/perp_store.py`.
**Đạt khi:** fake exchange nhiều trang cho đủ số dòng; lần fetch bị ngắt nối lại mà không tải lại; thiếu API key thì từ chối snapshot bracket chứ không đặt mặc định; summary dựng lại được từ minute close đã lưu, nên knob timeframe không kẹt.

### ✅ P3-18 Hợp đồng dữ liệu `PerpInputs` và payload sandbox — ADR-0034
**Mục tiêu:** container nhận được mark, funding và path. **Kiến trúc:** §3.3.3, ADR-0032. **Cần:** P3-17
**File:** thêm mới `core/perp_inputs.py` (schema và một API cắt duy nhất), `validation/sandbox.py`, `validation/sandbox_runner.py`.
**Đạt khi:** INV-99 — nến phút thô không bao giờ vào container; job IS không bao giờ nhận sidecar OOS; settlement rơi đúng ở 1d, 4h và 8h; bundle bắt buộc với `backtest`/`grid_backtest` và thiếu nó thì gate fail closed; kích thước payload đo được ghi lại.

### ✅ P3-19 Sizing theo tài khoản cho N = 1; quantity và stop đứng yên
**Mục tiêu:** gate ③/④ sizing từ tài khoản perpetual, không phải từ venue có margin bằng 0. **Kiến trúc:** §3.4, P4′. **Cần:** P3-18
**File:** `execution/nautilus_bridge.py` (`_equity`, rebalance band, cái stop bị đặt lại mỗi bar), `execution/engine.py`.
**Đạt khi:** giá và ATR đổi trong lúc vị thế mở mà quantity lẫn stop đều không đổi; rủi ro tại stop đo trên equity của tài khoản; một liquidation kết thúc cấu hình đó thay vì phân kỳ khỏi sổ của venue; equity bằng 0 không sinh inf hay nan.

### ✅ P3-20 Joint replay theo tín hiệu cho N > 1 — ADR-0035
**Mục tiêu:** tài khoản chung sizing và admit, chứ không replay quantity đã sized ở nơi khác. **Kiến trúc:** §3.2.1, §3.4. **Cần:** P3-19
**File:** `execution/joint_account.py`, `execution/admission.py` (lần đầu được gọi), `execution/perp_account.py`.
**Đạt khi:** một member với vốn dư dả tái tạo đường equity N = 1 của venue trong dung sai chặt — không có điều đó thì đây là một engine thực thi thứ hai không ai kiểm; INV-92 giữ; kill switch được gọi; đóng góp đối soát khớp **khi vẫn còn vị thế mở**.

### ✅ P3-21 Gate ③/④/⑥′ và danh mục chạy trên tài khoản
**Mục tiêu:** pipeline gọi tới tầng perpetual. **Kiến trúc:** §3.2, §3.2.1. **Cần:** P3-20
**File:** `validation/is_gates.py`, `validation/pbo_gate.py`, `validation/robustness.py`, `validation/portfolio.py`, `validation/run.py`.
**Đạt khi:** returns gate ③ đã trừ funding; chiến lược bị thanh lý trượt ③ và nói đúng như vậy; lưới PBO không bao giờ trộn scope; campaign legacy vẫn chạy qua `_summarize`, thứ không xoá được.

### ✅ P3-22 Holdout perpetual — hoàn tất P3-10
**Mục tiêu:** một holdout thứ hai không đụng cái đầu tiên. **Kiến trúc:** §4.2, P6. **Cần:** P3-18
**File:** `data/holdout_split.py`, `cli.py`, tiến trình evaluator.
**Đạt khi:** INV-94; nó nằm **bên trong** thư mục holdout đang có nên guard hook che sẵn mà không phải sửa hook; thiếu funding hoặc mark ở OOS thì fail closed.

### ✅ P3-23 Endpoint equity tài khoản và UI — hoàn tất P3-15
**Mục tiêu:** phép đối soát nhìn thấy được. **Kiến trúc:** ADR-0029. **Cần:** P3-20
**File:** `review/repository.py`, `review/app.py`, `ui/src/lib/types.ts`, `ui/src/screens/Portfolios.tsx`.
**Đạt khi:** equity ban đầu cộng tổng đóng góp bằng equity tài khoản trong sai số làm tròn, kể cả khi còn vị thế mở; thiếu dữ liệu đọc ra `Chưa có`, không bao giờ `0`.

### ✅ P3-24 Tải thật, preflight, dry-run lock
**Mục tiêu:** điều kiện thứ năm của cổng giai đoạn. **Kiến trúc:** §6.1, D18. **Cần:** P3-17 · **Quyết định:** [ADR-0048](adr/0048-lo-mark-ngan-duoc-dien-tu-nen-trade.md)
**File:** `cli.py` (`data-preflight`; `data-fetch --market perp` chạy nó trước; `campaign-dryrun` xem trước đúng lock thật), mới `data/perp_preflight.py`, `data/perp_source.py`, `data/perp_store.py`, `data/perp_pipeline.py`, `validation/run.py` (`new_campaign_derived`).
**Đạt khi:** perpetual OHLCV, mark, funding và bracket đủ coverage chung để khoá; `common_window` trả về 2020-09-14; một lần dry run không đổi lock nào, không mở campaign và không claim holdout. **Bằng chứng (2026-10-02, Binance thật, key chỉ-đọc):** `data-preflight` trên năm contract, 1h, holdout 12 tháng đọc được 10–12 bậc bracket mỗi contract và tìm thấy cửa sổ chung bắt đầu **2020-09-14** (SOLUSDT là ràng buộc; nến đầu tiên mọi chuỗi đều có là 2020-09-14 08:00 UTC, nên start trong config là 2020-09-15) — 5,05 năm IS; `start: 2020-09-14` bị từ chối, `2020-09-15` qua, và lần chạy không ghi gì. Test: `tests/data/test_perp_preflight.py`, `tests/test_perp_cli.py::test_dryrun_leaves_lock_ledger_and_holdout_claim_untouched`. **Tải thật và cắt (2026-10-04):** `data-fetch --market perp` (start 2020-09-15, end 2026-10-02, 1h) tải khoảng 31,8 nghìn trang phút và dừng hai lần vì dữ liệu sàn: (1) lịch sử mark có lỗ — được điền từ nến trade cùng phút trong giới hạn 60 phút và được ghi lại (ADR-0048, INV-122): BTC 24, ETH 35, SOL 32, BNB 28, XRP 31 phút, khớp đúng các lỗ mà khảo sát file lưu trữ, kiểm lại qua API, đã tìm ra; (2) stamp tất toán lệch phút vài mili-giây, mà các điểm cắt đường giá từng âm thầm bỏ qua — giờ được xếp vào phút gần nhất như replay (INV-123). Sau đó lần cắt khoá holdout perpetual 2025-10-02/2026-10-03 với 31 file IS có checksum. **Dry run trên dữ liệu đó:** lock xem trước có mọi tag mà lần mở thật ghi (trước đây thiếu: `new_campaign_derived`, `tests/test_perp_cli.py::test_the_dryrun_previews_the_lock_a_real_open_writes`); lock và ledger giữ nguyên từng byte; lý do từ chối duy nhất là campaign spot `harness_test` `c-20260930-170212` đang OPEN, đúng như phải thế.

**Cổng giai đoạn 3:** fixture chứng minh đường tài khoản chung và preflight dữ liệu thật đạt. **Không campaign thật nào mở trong giai đoạn này** — hình dạng của nó là quyết định riêng, và headroom là 1.185 trial.

**Trạng thái hiện tại.** P3-16 đến P3-23 đã triển khai và kiểm thử: sandbox hiện nhận mark,
funding, path và bracket; gate ③/④/⑥′, replay holdout và chart tài khoản đều dùng dữ liệu đó.
P3-24 đã xong: preflight nguồn thật qua ngày 2026-10-02, và ngày 2026-10-04 dữ liệu thật đã được tải, holdout perpetual đã được cắt và một lần dry run đã được kiểm trên đó — không campaign nào mở. P3-25 bổ sung
thoát theo bracket có phiên bản mà không diễn giải lại campaign hay kết quả cũ.

Điểm chặn payload sandbox cũ đã được giải quyết. Điều kiện preflight dữ liệu thật của cổng
giai đoạn đã đạt, và lần cắt cùng dry run của nó đã được xác minh trên cửa sổ đó.

**Năm lỗi tìm ra khi review, đã sửa hết (INV-92c, INV-93, INV-93c, INV-95b).** Cả năm đều là code
có test xanh trong khi đường mà một campaign thật sự đi thì hỏng. `RiskSizer` trả 0 cho mọi
short, nên nhánh short của bridge không bao giờ chạy tới và mọi test hedge chứng minh điều ngược
lại đều đã tự tiêm sizer riêng. `PerpAccount.close` cộng toàn bộ lỗ đã thực hiện vào số dư tự do,
nên một cú gap qua điểm phá sản rút cạn tài khoản qua đúng cái ví lẽ ra phải isolated. `sort_key`
của replay hứa close trước open rồi sắp theo instrument, nên một lệnh vào có được cấp vốn hay
không lại phụ thuộc bảng chữ cái. Fill ngoài cửa sổ replay bị kẹp vào nến gần nhất thay vì bị từ
chối. Và reader của review chỉ nhận schema mới nhất, thứ mà không ledger nào hiện có đang mang —
một reader bị cấm migrate thì không thể đồng thời đòi hỏi phải có một cái trước khi nó chịu
đọc.

---

## Giai đoạn 3b–6 — các mốc (chia thành task khi bắt đầu giai đoạn)

### ✅ P3-25 Thoát bằng SL/TP cố định và thời gian giữ lệnh tối đa
**Mục tiêu:** mọi campaign mới vào lệnh với SL/TP đứng yên và thoát ở giá mở cửa nến kế sau 100 nến giữ nếu chưa chạm mức. **Quyết định:** ADR-0036. **Cần:** P3-24
**Việc làm:** khóa tỷ lệ TP/SL, giới hạn giữ lệnh và cửa sổ dữ liệu; sinh stop ATR hoặc Bollinger; replay spot và perpetual với cùng thứ tự exit. Chia lưới PBO lớn cho 15m/1h nhưng giữ đầy đủ tập cấu hình.
**Đạt khi:** equity backtest riêng và danh mục một slot khớp, lock cũ vẫn chọn exit legacy, dữ liệu 15m và 1h chạy được, mọi cổng CI xanh.

### ✅ P3-26 Studio UI local để điều khiển campaign
**Mục tiêu:** người dùng cấu hình campaign spot hoặc perpetual, chuẩn bị/tái dùng dữ liệu bất biến, preview đúng các kiểm tra lock/quota, tạo một campaign, chạy hoặc tiếp tục search, rồi xem tiến độ/kết quả từ trình duyệt local mà không sửa YAML hay gõ `evolve`/`compare`/`portfolio`. **Quyết định:** ADR-0037. **Cần:** P3-25 và preflight dữ liệu thật P3-24 cho mọi đường Create perpetual nguồn thật.
**File:** `src/quantcrucible/studio/{app,service,jobs,security}.py`, service campaign/run dùng chung, dataset registry, migration ledger cho request tạo idempotent, tách review router, `cli.py`, `ui/src/`, README/CLAUDE/architecture/ADR docs.
**Đạt khi:** Studio chỉ phục vụ loopback; `cli review` vẫn chỉ GET và chỉ đọc; sửa draft không ghi config/lock/ledger; preview không có side effect và hết hạn khi input/ledger/lock đổi; Create idempotent và phục hồi được sau crash; Run không bao giờ tạo campaign; mỗi project chỉ có một writer/job; Stop/Resume sống qua restart mà không nhân đôi trial; giá holdout không vào browser; `harness_test` khóa protocol trước trial 1; `research` enqueue được portfolio sau search; unit, integration, Playwright và `scripts/check_doc_mirror.py` xanh. **Bằng chứng:** 993 test Python (trừ docker/network/slow), 28 test component UI, 18 test Playwright, typecheck/build, lint/hợp đồng import và doc mirror đều qua ngày 2026-09-28. Preflight perpetual nguồn thật vẫn thuộc P3-24.

### Engine backtest GPU (P3-27 … P3-51, ADR-0038, ADR-0039, D23)
Genome engine C chạy bằng kernel numba trên host (CUDA chính, njit CPU dự phòng), phải khớp từng bit với replay Python. Plan chia việc thành pha 0 (đo đạc), cổng tài liệu, nền móng, GPU-1 (spot ③④), GPU-2 (⑥′, calibration, holdout) và GPU-3 (perpetual).

### ✅ P3-27 Benchmark baseline
**Mục tiêu:** biết backtest tốn thời gian ở đâu. **File:** `scripts/bench_backtest.py`. **Đạt khi:** tỷ lệ theo tầng và chi phí gate ④ dự kiến nằm trong O14. **Bằng chứng:** tín hiệu chiếm 87–98%; lưới 200 cấu hình ≈ 0,4–3,3 giờ mỗi candidate (2026-09-30).

### ✅ P3-28 Toolchain GPU
**Mục tiêu:** numba + numba-cuda chạy trên GPU máy phát triển. **Đạt khi:** group `gpu` đã lock, license đã ghi, bộ test CI pass. **Bằng chứng:** numba 0.67, numba-cuda 0.30.4 (cu12); numpy giới hạn `<2.5`; 993 test pass.

### ✅ P3-29 Numerics E7
**Mục tiêu:** tìm thứ tự phép tính fp64 tái tạo chính xác `registry.py`. **Đạt khi:** 0 lệch bit cho mỗi op. **Bằng chứng:** `scripts/experiments/e7_*.py`, ADR-0038.

### ✅ P3-30 Prototype và go/no-go
**Mục tiêu:** genome G1 viết cứng trên CUDA và njit, khớp bit với `_run_spot_bracket`; chạy E0/E2/E8. **Quyết định:** ADR-0038 (user giữ CUDA làm chính khi chỉ hơn njit 2,1×). **Bằng chứng:** `scripts/experiments/run_proto.py`.

### ✅ P3-31 Cổng tài liệu
**Mục tiêu:** kiến trúc §3.3.3, §3.5, §6, §10 D23, khóa §10.1; ADR-0038/0039; lộ trình, bất biến, quy ước, vấn đề mở. **Đạt khi:** `scripts/check_doc_mirror.py` pass.

### ✅ P3-32 Kiểu genome trong `core`
**Mục tiêu:** chuyển dataclass genome, phần render và (giải) tuần tự hóa sang `core/strategy/genome.py`; `agent/grammar.py` re-export. **Đạt khi:** 1.000 lần `render_genome` có seed cho cùng hash trước và sau; `lint-imports` pass.

### ✅ P3-33 Parser source → genome
**Mục tiêu:** `core/strategy/genome_parse.py` dựng lại genome từ source đã render bằng `ast`, không bao giờ `exec`, chỉ nhận khi render lại khớp từng byte (E3). **Đạt khi:** round trip đúng dưới Hypothesis; source bị đột biến, lạ hoặc quá lớn bị từ chối. **Bằng chứng:** mọi genome lấy mẫu (2.000 lần render) và 11.988 con lai C-gp đều parse lại đúng; mọi byte bị sửa, chiến lược zoo hay source quá lớn đều bị từ chối.

### ✅ P3-34 Report builder dùng chung
**Mục tiêu:** sandbox và engine kernel dựng report gate ③/④ bằng cùng hàm thuần; `SandboxResult.engine`. **Đạt khi:** test sandbox không đổi. **Bằng chứng:** hash golden của report gate ③ và gate ④ từ sandbox không đổi sau khi tách.

### ✅ P3-35 Precision trong config và lock
**Mục tiêu:** `research.backtest.precision`, `derived.backtest_numerics`, `operational.compute`, tương thích lock (lock cũ ⇒ float64), ô trong Studio. **Quyết định:** ADR-0039. **Đạt khi:** lock cũ với mặc định được nhận, với float32 bị từ chối; tag lạ bị từ chối. **Bằng chứng:** `tests/config/test_lock_precision.py`; ô precision trong Studio có test UI (29 test UI, 1.027 test Python).

### ✅ P3-36 Ledger: engine theo trial
**Mục tiêu:** migration 009 `trials.backtest_engine`, event `ENGINE_AUDIT_MISMATCH` / `ENGINE_FALLBACK`. **Đạt khi:** migration idempotent; trigger append-only còn nguyên; NULL đọc là sandbox. **Bằng chứng:** `tests/ledger/test_backtest_engine.py`; bộ đọc review nhận cả v8 và v9.

### ✅ P3-37 Trình biên dịch program
**Mục tiêu:** `execution/kernels/program.py` biến genome và danh sách tham số thành bảng số có giới hạn. **Đạt khi:** khử trùng lặp, ràng theo tên TUNABLE và fuzz tính total đều pass. **Bằng chứng:** `tests/execution/kernels/test_program.py` (19 test; 400 genome lấy mẫu đều compile, dữ liệu rác chỉ raise `ProgramError`).

### ✅ P3-38 Kernel feature
**Mục tiêu:** mọi op của registry trên cửa sổ (`now`, `prev`), njit và CUDA từ một source (bố cục theo E1). **Đạt khi:** khớp bit với `registry.py` trên cửa sổ; test `cudasim` và `gpu`. **Bằng chứng:** cả mười op × tám period × hai lookback, `now` lẫn `prev`, giống từng bit `registry.py` trên CPU njit, trên RTX 3050 Ti và dưới CUDA simulator; CI cài group `gpu` và chạy bước simulator.

### ✅ P3-39 Kernel tín hiệu
**Mục tiêu:** đủ bảy loại clause, and/or, stop ATR/Bollinger, tỷ lệ TP. **Đạt khi:** khớp bit với `generate_signals` của source đã render trên genome lấy mẫu, cả hai chiều. **Bằng chứng:** `tests/execution/kernels/test_signals.py`: 25 genome lấy mẫu × hai chiều × 3 cấu hình lưới, vector vào lệnh và bit stop bằng `generate_signals` trên CPU và trên RTX 3050 Ti.

### ✅ P3-40 Kernel replay spot
**Mục tiêu:** replay spot `bracket_timeout_v1`, fp64, chia khúc dưới ngưỡng watchdog WDDM, có trade log. **Đạt khi:** trade log và equity khớp từng bit `_run_spot_bracket`; kết quả không phụ thuộc kích thước khúc. **Bằng chứng:** `tests/execution/kernels/test_replay.py`: năm trường hợp đối kháng (gap, SL+TP cùng bar, timeout một bar, khối lượng bị giới hạn bởi tiền mặt, làm tròn lot về 0), 12 cấu hình trong một lần gọi và scope short trên spot khớp từng bit `_run_spot_bracket` trên CPU và trên RTX 3050 Ti. Một lần launch cho mỗi job: 67k bar tốn ≈ 0,1 s, rất xa ngưỡng watchdog, nên việc chia khúc theo bar chờ khi đo thấy cần (P3-43).

### ✅ P3-41 Backend và self-test
**Mục tiêu:** `CudaBackend` và `CpuKernelBackend` dựng `BacktestResult` và hàng gate ④; self-test khi khởi động. **Đạt khi:** report gate ③ giống report sandbox. **Bằng chứng:** `tests/execution/kernels/test_backend.py`: 12 genome lấy mẫu, `BacktestResult` gate ③ bằng `run_backtest` từng trường (equity, returns, fills, stops, exits, turnover, public metrics) và hàng gate ④ bằng nhau theo từng cấu hình, trên CPU và trên RTX 3050 Ti; self-test pass trên thiết bị.

### ✅ P3-42 Engine router và audit
**Mục tiêu:** `validation/engine_router.py` implement `JobRunner`, định tuyến, fallback, audit (L0–L2); gate ghi engine. **Đạt khi:** leak check luôn dùng sandbox; fp32 không bao giờ fallback sang fp64; lệch thì ngắt breaker cho phiên bản kernel đó. **Bằng chứng:** `tests/validation/test_engine_router.py` (10 test): report gate ③ và gate ④ giống từng byte report của sandbox; `cli._session` giao router cho mọi gate.

### ✅ P3-43 Executor GPU
**Mục tiêu:** một chủ sở hữu CUDA context, có hàng đợi, ngân sách VRAM và hủy việc (E5). **Đạt khi:** 8 worker pipeline dùng chung GPU không lỗi. **Bằng chứng (E5, `scripts/experiments/e5_gpu_sharing.py`, 8 worker, lưới 200 cấu hình, 67k bar):** bỏ vòng JSON của report đưa router từ 0,17 lên ≈ 1,5 lưới/s; CPU njit đạt ≈ 4,2 lưới/s khi không khóa (2,0 khi khóa), CUDA ≈ 0,8–1,5 dù khóa phần thiết bị theo từng bước hay theo job — sức tính fp64 của GPU laptop là giới hạn. Sau đó: replay chuyển sang CPU (ở đó nhanh hơn 6×), chỉ các bước GPU của một job giữ thiết bị, và audit so theo byte thay vì JSON — CUDA ≈ 8,4 lưới/s so với CPU ≈ 3,9, nên `auto` giữ CUDA (ADR-0038).

### ✅ P3-44 Parity đầu cuối và thông lượng
**Mục tiêu:** một campaign tổng hợp chạy qua sandbox và qua router. **Đạt khi:** `trials.sharpe_is`, byte parquet returns và ma trận gate ④ giống hệt; cập nhật O14. **Bằng chứng:** `tests/e2e/test_engine_parity.py` (docker): năm candidate genome đi qua ①a…④ trong ba campaign (sandbox, `cpu_kernel`, `auto` = CUDA): trial giống từng bit, verdict gate giống nhau, file kết quả giống từng byte, không có fallback hay lệch audit. `scripts/bench_backtest.py --kernels`: 0,27–0,29 s mỗi lưới 200 cấu hình trên 67k bar với CUDA; đã cập nhật ADR-0025 và O14/O22.

### ✅ P3-45 Chế độ float32 và E6
**Mục tiêu:** feature và tín hiệu fp32; audit fp32 so với njit; độ lệch E6 trên dữ liệu tổng hợp ghi vào ADR-0039. **Bằng chứng:** `tests/execution/kernels/test_float32.py`: CUDA fp32 bằng bản CPU fp32 từng bit (feature, tín hiệu, hàng lưới; trên thiết bị và simulator), và cả hai cách fp64 không quá 1e-3; một job genome fp32 chạy trên kernel và không bao giờ tới sandbox (`test_engine_router.py`). E6 trong ADR-0039: không verdict gate ③/④ nào bị lật trên 90 genome; `auto` giữ CUDA.

### ✅ P3-46 Gate ⑥′ và calibration trên engine
**Mục tiêu:** chi phí ×2, chạy lại trên nguồn thứ hai và các lần thử Optuna đều đi qua router. **Bằng chứng:** cả hai vốn đã gửi job qua `ctx.services["sandbox"]`, mà `cli._session` (và do đó Studio) điền bằng router. `tests/validation/test_engine_reruns.py`: một lần calibration qua router cho cùng các lần thử, cùng tham số tốt nhất và cùng Sharpe của trial, khớp từng bit, so với các hàm job chuẩn, và không có job sandbox nào; các lần chạy lại của ⑥′ (chi phí ×2, nguồn thứ hai) cũng khớp, và ở float32 vẫn ở trên kernel.

### ✅ P3-47 Holdout trên engine
**Mục tiêu:** holdout evaluator dùng engine theo precision của lock; self-test trước `claim()`, fallback CPU kernel giữa chừng. **Đạt khi:** preflight bị từ chối thì holdout chưa bị claim. **Bằng chứng:** `evaluator_proc.engine_runner` dựng router (`--engine`, mặc định `auto`; L1 cho mọi job CUDA) và gọi `EngineRouter.preflight` chỉ từ source và tham số, trước `claim()`: member float32 không có kernel, campaign float32 với lựa chọn sandbox, hay bản CPU không ra đúng digest fixture đã ghim (`backend.cpu_check`, được test buộc với `run_backtest`) đều bị từ chối. `tests/holdout/test_evaluator_engine.py`: portfolio genome được chấm trên kernel ở cả hai precision (float64 khớp engine chuẩn từng bit); mọi lần từ chối đều để campaign ở FROZEN và chưa claim; lỗi CUDA giữa chừng hoàn tất trên bản CPU với cùng Sharpe.

### ✅ P3-48 Điều kiện freeze
**Mục tiêu:** mọi thành viên danh mục được tái tạo theo đường chuẩn trước freeze (audit L3). **Bằng chứng:** `validation/reproduce.py` đo lại mọi member do kernel đo — sandbox ở float64, bản CPU ở float32 — và ghi `MEMBERS_REPRODUCED`; `freeze_campaign` từ chối cho tới khi mọi member như vậy khớp returns đã ghi từng bit, và `cli freeze` chạy bước tái tạo trước. `tests/validation/test_reproduce.py`: freeze bị chặn khi chưa tái tạo và khi lệch một ulp; float32 tái tạo trên bản CPU mà không đụng tới sandbox.

### ✅ P3-49 Mảng đầu vào perpetual
**Mục tiêu:** `core/perp_arrays.py` đóng gói `PerpBundle` thành mảng phẳng. **Đạt khi:** first-touch bằng `SegmentedPath`. **Bằng chứng:** `tests/core/test_perp_arrays.py` trên bundle 4h tổng hợp (`tests/perp_fixtures.py`: đường giá giao dịch và mark theo phút, settlement giữa bar, bảng margin nhiều bậc): first-touch trên mảng bằng `PathSummary.first_touch` qua ~7k trường hợp segment/mức giá/chiều, kể cả đúng breakpoint; funding được đóng gói theo đúng thứ tự replay đọc, kèm offset phút; bar mà replay sẽ từ chối được đánh dấu chứ không bị từ chối.

### ✅ P3-50 Kernel replay perpetual
**Mục tiêu:** replay bracket perpetual một slot (funding, thanh lý, leverage, clearance, timeout). **Đạt khi:** khớp bit với `replay_signals` cho một slot. **Bằng chứng:** `execution/kernels/perp_replay.py` (K4, njit trên CPU, mỗi cấu hình một luồng; engine replay trên host). `tests/execution/kernels/test_perp_replay.py`: 11 ca × 4 cấu hình, cả hai chiều, trên bundle 4h tổng hợp — equity khớp từng bit, mọi lần mở (bar, khối lượng, giá, stop, leverage) và đóng (bar, giá, lý do), funding đã trả, thanh lý, từ chối, bar mơ hồ; có đủ stop, take-profit, timeout và thanh lý; bar mâu thuẫn khi đang có vị thế thì lỗi giống replay. E4 (ADR-0038): giữ tìm nhị phân; 200 cấu hình × 3k bar mất 5 ms so với ≈ 14 s của replay Python.

### ✅ P3-51 Định tuyến perpetual
**Mục tiêu:** job perpetual ③/④ và job `signals` cho replay danh mục đi qua router; INV-94 vẫn giữ. **Bằng chứng:** router chỉ lên kế hoạch cho job perpetual khi có bundle (đã căn chỉnh, có đường giá giao dịch theo phút), đóng gói mỗi bundle một lần và replay bằng K4; job `signals`, vốn không định giá gì, lấy từ kernel tín hiệu, nên danh mục perpetual float32 qua được preflight của holdout. `tests/validation/test_engine_router_perp.py`: report gate ③, gate ④ và `signals` bằng report của các hàm job trong container cho genome long và short, audit L2 so cả funding và ngày thanh lý, và một job thiếu bundle đi sandbox ở float64 và bị từ chối ở float32.

### ✅ P3-52 Danh mục spot trên một tài khoản tiền mặt chung
**Mục tiêu:** danh mục spot bracket là một lần replay tài khoản của các slot, như danh mục perpetual ([ADR-0040](adr/0040-danh-muc-spot-la-mot-tai-khoan-tien-mat-chung.md)). **Chấp nhận khi:** một member tái tạo backtest gate ③ của nó khớp từng bit; bước dựng, gate ⑥′ và holdout replay cùng một tài khoản; lock không có tag giữ tổng có trọng số. **Bằng chứng:** `execution/spot_account.py`; `tests/execution/test_spot_account.py` (khớp bit trên 3 seed, trục hợp, tiền mặt chung theo thứ tự chuẩn, trần 10%, một snapshot mỗi bar); `tests/validation/test_spot_portfolio.py` (tag lock, bước dựng, ⑥′, holdout, luật cũ được giữ); `tests/validation/test_run.py::test_opens_once_then_resumes` (lock mới mang tag).

### ✅ P3-53 Chu kỳ stop là gene TUNABLE
**Mục tiêu:** dưới lock `stop_period: tunable_v1`, mọi genome engine C mang `n_stop` ∈ [5, 50] — chu kỳ ATR của stop ATR (feature `atr_stop`) hoặc chu kỳ dải của stop Bollinger — trong trần 7 TUNABLE ([ADR-0041](adr/0041-chu-ky-stop-la-gene-tunable.md), D24). **Chấp nhận khi:** INV-114, INV-115; genome cũ render từng byte như trên `main` (source và hash mẫu); render ↔ parse khứ hồi có và không có gene, cả hai kiểu stop, cả hai hướng; cổng ①a nhận 7 TUNABLE dưới lock mới và loại chúng dưới lock cũ; kernel khớp `generate_signals` từng bit khi có gene; lock mới mang cả hai khóa. **Bằng chứng:** `core/strategy/genome.py`, `genome_parse.py`, `tunable.py`, `validation/guardrail.py`, `execution/kernels/program.py`, `signals.py`; `tests/core/strategy/test_genome.py` (golden của 1.000 render và dạng JSON trước thay đổi vẫn giữ; `n_stop` render cho cả hai kiểu stop), `test_genome_parse.py` (khứ hồi có gene), `tests/validation/test_guardrail.py` (7 TUNABLE chỉ qua dưới `tunable_v1`; số 8 viết trực tiếp bị loại ở đó), `tests/validation/test_engine_router.py` (báo cáo kernel ≡ sandbox khi có gene, lưới quét `n_stop`), `tests/execution/kernels/test_signals.py` (CPU, cudasim và GPU RTX), `tests/agent/engines/test_random_search.py`, `tests/agent/evolution/test_operators.py`, `tests/validation/test_run.py`. Hash protocol so sánh không đổi (phạt giữ thang `/6`), nên campaign `harness_test` đang mở vẫn chạy tiếp được. Chưa chạy campaign e2e qua Docker (không có daemon ngày 2026-10-02).

### ✅ P3-54 Đo grammar trên bar in-sample
**Mục tiêu:** đo grammar của engine C trước khi sửa (P3-55 … P3-59): mỗi loại clause đúng trên bao nhiêu bar, cả với clause lấy mẫu lẫn trên một lưới cố định các con số của nó; bao nhiêu clause lấy mẫu vẫn suy biến dưới các thay đổi ứng viên (period lấy mẫu theo log, giới hạn period RSI và z-score, bốn mẫu số cho `Distance`); mức bão hoà của `Distance` với từng mẫu số, tần suất stop Bollinger `<= 0` khi trigger mean reversion kích hoạt, khoảng giá trị của các feature biến động, và tương quan giữa feature hiện có với feature ứng viên. Chỉ tính giá trị chỉ báo — không có lợi nhuận, fill hay trial (P2); chỉ đọc thư mục in-sample, từ chối đường dẫn `holdout` (P6). **Chấp nhận khi:** giá trị tính tay cho mọi chỉ báo ứng viên và các vị từ cross; một xu hướng làm `Distance` bão hoà khi chia ATR nhưng không bão hoà khi chia `stdev`; một bar sập đưa giá đóng cửa xuống dưới band. **Bằng chứng:** `scripts/grammar_diagnostics.py`; `tests/tooling/test_grammar_diagnostics.py`.

### ✅ P3-55 Loại stop khoá theo campaign; Bollinger tắt mặc định
**Mục tiêu:** khoá nhóm B `research.exit.stop_kinds` (mặc định `[atr]`) quyết định genome của campaign dùng loại stop nào ([ADR-0042](adr/0042-loai-stop-khoa-theo-campaign.md), D25): P3-54 đo được stop Bollinger `<= 0` đúng lúc clause mean reversion kích hoạt. **Chấp nhận khi:** INV-116; lock thiếu khoá giữ loại stop mà exit của nó đã chạy và các campaign đang mở resume được; lock mới mang khoá và từ chối thay đổi. **Bằng chứng:** `config/schema.py`, `config/loader.py`, `config/lock.py`, `core/strategy/tunable.py::lock_stop_kinds`, `agent/run.py::grammar_config`, `validation/guardrail.py::check_no_bollinger`; `tests/config/test_lock_stop_kinds.py`, `tests/agent/test_scope_quotas.py::test_the_lock_decides_the_stop_kinds`, `tests/agent/evolution/test_operators.py`, `tests/validation/test_guardrail.py`, `tests/validation/test_run.py`.

### ✅ P3-56 Danh mục chiến lược theo hướng
**Mục tiêu:** danh mục của clause tính đến hướng của scope, và hai cách viết của cùng một điều kiện có cùng nhãn ([ADR-0043](adr/0043-danh-muc-chien-luoc-theo-huong.md)); lock mới ghi `derived.grammar_version: 2`. **Chấp nhận khi:** INV-117 theo một bảng viết tay cho mọi loại clause ở cả hai hướng; nhãn và cách lấy mẫu version 1 không đổi; đảo danh mục v2 chỉ seed đúng danh mục của nó ở cả hai hướng. **Bằng chứng:** `agent/grammar.py` (`polarity`, `clause_category`, `sample_clause_in_category`, `CATEGORY_CLAUSES_V2`), `agent/engines/gp_search.py::_seeding`, `agent/pipeline.py`, `agent/run.py::grammar_config`, `core/strategy/tunable.py::lock_grammar_version`; `tests/agent/test_grammar_categories.py`, `tests/validation/test_run.py`. `tests/agent/test_ranking_recovery.py::test_the_rank_form_holds_more_of_the_paying_clause` (slow) đã đỏ sẵn trên `main` trước task này.

### ✅ P3-57 Period lấy mẫu theo log và giới hạn period oscillator
**Mục tiêu:** ít clause suy biến hơn ([ADR-0044](adr/0044-period-log-va-gioi-han-oscillator.md)): dưới `grammar_version: 3`, period của clause được lấy theo log-uniform trong bound, period `rsi` giới hạn [2, 30] và period `zscore` giới hạn [10, 300]. **Chấp nhận khi:** INV-118; tỷ lệ lần bốc trên 100 bar trong [2, 300] bằng ln 3 / ln 150; cách lấy mẫu v1 và render golden không đổi. **Bằng chứng:** `agent/grammar.py` (`PERIOD_CAPS_V3`, `period_range`, `sample_period`, `sample_clause`), `agent/evolution/operators.py::_swap_indicator`, `core/strategy/tunable.py::GRAMMAR_VERSION`; `tests/agent/test_grammar_sampling_v3.py`, `tests/tooling/test_grammar_diagnostics.py`, `tests/validation/test_run.py`.

### ✅ P3-58 Distance chia độ lệch chuẩn của chính spread
**Mục tiêu:** clause `Distance` sống ở mọi period ([ADR-0045](adr/0045-distance-chia-do-lech-cua-spread.md)): dưới `grammar_version: 4`, nó chia `a − b` cho `spread_stdev(a, b, n)`, toán hạng ≤ 200 bar. **Chấp nhận khi:** INV-119; giá trị registry tính tay; kernel ≡ registry trên mọi cửa sổ và kernel ≡ `generate_signals` trên CPU, giả lập CUDA và GPU; báo cáo của router bằng báo cáo của sandbox; `Distance` dạng ATR không đổi. **Bằng chứng:** `core/strategy/registry.py::spread_stdev`, `core/strategy/genome.py` (`Distance.scale`), `core/strategy/genome_parse.py`, `agent/grammar.py` (`SPREAD_OPERAND_MAX`), `execution/kernels/program.py` (`OP_SPREAD`, `inst_aux`, cột clause thứ sáu), `features.py` (`spread`), `signals.py`, `backend.py`, `validation/engine_router.py::_evaluate`; các test liệt kê ở INV-119.

### ✅ P3-59 Feature map không phụ thuộc timeframe
**Mục tiêu:** một map MAP-Elites cho mọi timeframe và độ dài IS ([ADR-0046](adr/0046-feature-map-khong-phu-thuoc-timeframe.md)): lock mới chia bin số lệnh mỗi năm theo thang log và dùng lợi nhuận năm; bound đối chiếu với trial của các campaign 1d. **Chấp nhận khi:** INV-120; bin log tính tay; lợi nhuận năm là lãi kép; map v1 đọc như trước. **Bằng chứng:** `agent/evolution/feature_map.py` (`dimensions`, `log_dims`, `descriptors`), `validation/run.py::FEATURE_MAP_V2`; `tests/agent/evolution/test_feature_map_v2.py`.

### ✅ P3-60 Đồng hồ đo theo ngày
**Mục tiêu:** campaign dưới 1 ngày được chấm trên cùng đồng hồ với campaign ngày (TF-2, [ADR-0049](adr/0049-dong-ho-danh-gia-theo-ngay.md), D26): dưới `derived.evaluation_clock: daily_v1`, Sharpe, Sortino, các moment của ranking, `V[SR]`, danh mục, DSR, gate ⑥′ và Sharpe holdout đọc lợi nhuận theo bar gộp kép theo ngày UTC, annual hoá bằng 365; `N_eff` tính tương quan trên lợi nhuận ngày với mọi lock; backtest, `returns_path`, `min_trades`, gate ④ và MinBTL giữ theo bar. **Chấp nhận khi:** INV-124; lợi nhuận ngày tính tay (bar nửa đêm thuộc ngày trước, chuỗi 1d giữ nguyên từng bit); gate ③, ⑤, ⑥′ và holdout trên bar giờ khớp số liệu ngày tính tay; lock không có khoá không đổi; lock mới mang khoá. **Bằng chứng:** `validation/clock.py`, `validation/is_gates.py::_on_clock`, `validation/n_eff.py` (`onc-v2`), `validation/portfolio.py` (`Portfolio.clock`, `evaluation`), `validation/portfolio_dsr.py`, `validation/robustness.py`, `validation/research_run.py`, `holdout/evaluator_proc.py`, `agent/run.py`, `agent/compare.py`, `validation/run.py::new_campaign_derived`, `studio/service.py`; các test liệt kê ở INV-124, `tests/validation/test_run.py`.

### ✅ P3-61 Clause biến động và một thước ATR
**Mục tiêu:** grammar diễn tả được chế độ biến động ([ADR-0047](adr/0047-clause-bien-dong-va-mot-thuoc-atr.md)): dưới `grammar_version: 5` có clause `VolRatio` (`atr_ratio(bars, fast, slow) op k`) và `Bandwidth` (`band_ratio(close, fast, slow) op k`, độ rộng tính trên √bar), danh mục thứ năm `volatility` và đảo thứ sáu; và, với mọi genome có stop ATR mang gen `n_stop`, ATR(n_stop) là ATR duy nhất của render — ATR(14) đứng cạnh nó làm 39/40 genome trượt ràng buộc tương quan chỉ báo của cổng ③. **Chấp nhận khi:** INV-121; giá trị registry tính tay; kernel ≡ registry trên mọi cửa sổ và kernel ≡ `generate_signals` trên CPU, giả lập CUDA và GPU; báo cáo của router bằng báo cáo của sandbox; lấy mẫu, đảo và render golden của v4 không đổi. **Bằng chứng:** `core/strategy/registry.py` (`bandwidth`, `band_ratio`, `atr_ratio`), `core/strategy/genome.py` (`VolRatio`, `Bandwidth`, `Genome.one_atr`), `core/strategy/genome_parse.py`, `agent/grammar.py` (`clause_types_for`, `categories_for`, `CATEGORY_CLAUSES_V5`), `agent/evolution/islands.py`, `agent/engines/gp_search.py::_seeding`, `agent/run.py::grammar_config`, `execution/kernels/program.py` (`OP_BANDWIDTH`, `OP_ATR_RATIO`, `OP_BAND_RATIO`), `features.py`, `validation/engine_router.py::_evaluate`, `validation/run.py::FEATURE_MAP_V2`, `scripts/grammar_diagnostics.py` (`fire_v5`); các test liệt kê ở INV-121.

### ✅ P3-62 Rà soát chỉ báo ứng viên
**Mục tiêu:** quyết định phiên bản grammar kế tiếp nên thêm chỉ báo nào dựa trên số đo chứ không theo cảm tính: tương quan của từng ứng viên với các feature của grammar, theo mức và theo thay đổi từng bar (cổng ③), và tần suất một ngưỡng trên nó kích hoạt. Chỉ là báo cáo, không có code. **Chấp nhận khi:** mọi ứng viên được đo ở 1d và 1h và có kết luận theo ngưỡng (mức < 0,7, thay đổi < 0,9). **Bằng chứng:** [05-VAN-DE-MO.md](05-VAN-DE-MO.md) O25; chi phí và khớp lệnh dưới 1 giờ mà nó chưa xét là O26 (TF-3).

### ✅ P3-63 Thống kê trial theo từng campaign
**Mục tiêu:** các campaign độc lập, theo quyết định của người dùng ngày 2026-10-05 ([ADR-0050](adr/0050-thong-ke-trial-theo-campaign.md), D27): dưới `derived.trial_scope: campaign_v1`, `N` của gate ②, `N_raw`/`N_eff`/`V[SR]` của gate ⑤, số variant và số lần không đo được, gate ⑥′, xếp hạng slot và GP ranking, ngân sách trial và snapshot của freeze chỉ đếm trial của chính campaign; mọi trial vẫn nằm trong một ledger append-only duy nhất; các campaign đã mở trước đó giữ cách đếm toàn ledger. **Chấp nhận khi:** INV-125; với hai campaign, số liệu của campaign này bỏ qua trial, lần gom cụm và variant của campaign kia; số liệu toàn ledger và số liệu của một ledger v9 không đổi sau migration 010; lock không có khoá không đổi; lock mới mang khoá. **Bằng chứng:** `validation/trial_scope.py`, `ledger/db.py` (`trial_stats`, `total_portfolio_variants`, `unmeasured_attempts`, `latest_clustering`, `record_clustering`, `snapshot`), `ledger/migration_010_campaign_trial_stats.sql`, `validation/n_eff.py::update_n_eff`, `validation/is_gates.py::MinBtlGate`, `validation/portfolio_dsr.py`, `validation/robustness.py`, `validation/portfolio.py::build_and_record`, `validation/research_run.py`, `validation/freeze.py`, `validation/run.py` (`new_campaign_derived`, `_check_trial_budget`), `agent/engines/gp_search.py`, `agent/monitor.py::engine_report`, `agent/compare.py`, `studio/service.py`, `review/repository.py` (v10); các test liệt kê ở INV-125.

### ✅ P3-64 Chọn cửa sổ dữ liệu và điểm cắt IS/holdout
**Mục tiêu:** người dùng chọn khoảng thời gian backtest — một cửa sổ dữ liệu `[start, end]` và ngày cắt nó thành IS và holdout ([ADR-0051](adr/0051-chon-cua-so-du-lieu-va-diem-cat-holdout.md), D18, D28); holdout không bao giờ lùi vào dữ liệu mà các trial đã ghi từng tìm kiếm. **Chấp nhận khi:** INV-126; registry cắt đúng ngày đã chọn và từ chối điểm cắt bẩn trước khi tải; dataset đã publish giữ nguyên id; kho cũ không bắt đầu ở `data.start` bị từ chối với lock mới; lock cũ vẫn khớp; ô điểm cắt của Studio. **Bằng chứng:** `data/window.py` (`holdout_window`, `unclean_holdout`, `DATA_WINDOW_V1`), `config/schema.py` (`Data.holdout_start`), `config/loader.py`, `config/lock.py::_comparable`, `data/registry.py` (`DatasetSpec.holdout_start_utc`, `fetch_and_publish`), `ledger/db.py::latest_is_end`, `validation/run.py` (`admission_problems`, `_check_is_window`, `_check_clean_holdout`), `validation/campaign_service.py`, `cli.py` (fetch, preflight, dry run), `studio/service.py`, `studio/worker.py`, `ui/src/screens/Studio.tsx`; các test liệt kê ở INV-126.

| Giai đoạn | Mốc | Kiến trúc |
|---|---|---|
| **3b** Mở rộng độ rộng (2–3 tuần) | 15–30 công cụ tương quan yếu; chế độ engine `collaborative`; không công cụ nào > 20% rủi ro | §3.1.11, §3.4 |
| **4** IB → forex + cổ phiếu quốc tế (3–4 tuần) | Adapter IB qua Nautilus; phiên/lịch giao dịch; nguồn dữ liệu Stooq; chỉ chỉ số/ETF (survivorship) | §3.5, §6.1 |
| **5** Futures (5–7 tuần) | Tự viết module roll (điều chỉnh tỷ lệ, roll theo OI/volume) từ dữ liệu TurtleTrader; khớp với một nguồn tham chiếu | §6.1 |
| **6** SSI → VN30F1M (3–6 tuần) | InstrumentProvider + DataClient + ExecutionClient; đối soát khi kết nối lại lúc đang có vị thế | §3.5 |

Trước lần mở holdout đầu tiên: **phải chốt D4** (kiến trúc §10).
