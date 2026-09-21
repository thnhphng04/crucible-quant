# ADR-0007 — `purgedcv`: phần nào bọc lại, phần nào tự cài đặt

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-01 · **Kiến trúc:** ghi chú §6, §3.2, §4.1, 07-VALIDATION-LAYER §7 · **Vấn đề mở:** O2 (đã đóng)

## Bối cảnh

Kiến trúc gọi `purgedcv` là lõi của tầng validation nhưng đánh dấu API của nó là chưa kiểm chứng (§6, §10): chưa ai kiểm tra DSR có nhận `N` và `V[SR]` riêng không, đầu vào và quy tắc chọn của PBO là gì, hay license. Phương án dự phòng là tự cài đặt DSR/PBO. P1-01 đã cài `purgedcv` 0.1.6 và đọc source (`_metrics.py`, `_pbo.py`, `_cpcv.py`, `_paths.py`, `_purge.py`, `_embargo.py`).

## Quyết định

1. **Dependency:** `purgedcv>=0.1.6,<0.2` (MIT, có `py.typed`). Kéo theo scikit-learn, joblib, threadpoolctl, cloudpickle (BSD-3) và narwhals (MIT) — không có GPL/AGPL.
2. **Chữ ký đã kiểm tra (0.1.6):**
   - `deflated_sharpe_ratio(returns, n_trials, var_sharpe, *, bars_per_year=None) -> float` — **`N` và `V[SR]` là hai đầu vào riêng**; `var_sharpe` tính theo từng quan sát trừ khi truyền `bars_per_year`. SR dùng `ddof=0`, skew/kurtosis đã hiệu chỉnh bias, kurtosis không phải excess. Chỉ nhận chuỗi lợi nhuận, không bao giờ nhận các moment.
   - `probability_of_backtest_overfitting(returns, n_splits=16, *, metric=sharpe, prediction_times=None, evaluation_times=None, purge_horizon=None, embargo=…) -> PBOResult` — ma trận đầu vào là **`(n_configs, n_obs)`** (chuyển vị của T×N trong bài báo); quy tắc chọn = **argmax của `metric` trên IS** (mặc định Sharpe, đúng như §3.2 yêu cầu); hạng OOS ω = rank/(M+1); PBO = tỉ lệ logit < 0. Purge/embargo tuỳ chọn qua `CombinatorialPurgedCV`.
   - `CombinatorialPurgedCV(n_splits, n_test_groups, *, prediction_times, evaluation_times, purge_horizon, embargo | embargo_observations | embargo_fraction).split(X)` sinh `(train_idx, test_idx)` đã purge + embargo; `reconstruct_paths(...)` dựng C(N−1,K−1) đường OOS. `backtest_paths` cần một estimator sklearn — không dùng.
3. **Cách dùng từng phần:**
   - **DSR / PSR — tự cài đặt** trong `validation/statistical.py` từ các moment (`sr, sr_benchmark, n_obs, skew, kurt`), với benchmark `√V[SR] · expected_max_sharpe(N)` (hàm đã có). Lý do: ví dụ đã công bố (Bailey & López de Prado 2014) cho dưới dạng moment, thứ `purgedcv` không nhận được; và ⑤ phải báo DSR ở cả `N_raw` lẫn `N_eff`. `purgedcv.deflated_sharpe_ratio` được giữ làm **đối chiếu trong test** (probe: bằng nhau tới 1e-15 trên một chuỗi ngẫu nhiên).
   - **PBO — bọc lại** `probability_of_backtest_overfitting` trong `validation/pbo.py` (P1-03), `S = 16`. *Được điều chỉnh bởi [ADR-0011](0011-cong-4-tap-cau-hinh-va-cscv.md): tự viết CSCV vector hoá, giữ `purgedcv` làm oracle trong test (vì tốc độ).*
   - **CPCV — bọc lại** `CombinatorialPurgedCV.split` + `reconstruct_paths` trong `validation/cpcv.py` (P1-04); việc chọn cấu hình theo từng fold trên ma trận lưới là của ta.
   - **MinBTL — giữ bản của ta** (ADR-0006); một test ghim nó bằng `purgedcv.minimum_backtest_length`.
   - **Không dùng:** `effective_n_trials` (heuristic tự tương quan theo thứ tự trial) — `N_eff` là phân cụm ONC theo §4.1 (P1-05).
4. **Chỉ các module wrapper** (`validation/statistical.py`, `validation/pbo.py`, `validation/cpcv.py`) được import `purgedcv`; cưỡng chế bằng `tests/test_architecture_boundaries.py`.

## Hệ quả

- Đóng O2. P1-02, P1-03, P1-04 hết bị chặn; probe đã tái lập ví dụ DSR của bài báo (0.9004) từ các moment.
- Image sandbox lớn thêm scikit-learn (cài từ `uv.lock`); P1-05 có thể dùng lại scikit-learn cho KMeans/silhouette.
- `purgedcv` đang alpha (0.x): ghim `<0.2` cộng các test đối chiếu sẽ bắt được việc công thức bị đổi âm thầm.
- Không đổi kiến trúc. 07-VALIDATION-LAYER §7 nay ghi các chữ ký này là đã kiểm chứng (người dùng đã duyệt, VI trước, rồi EN).

## Phương án đã cân nhắc

- **Bọc luôn DSR:** loại — không test được với ví dụ dạng moment của bài báo, và che mất các quy ước SR/moment mà ta phải kiểm soát.
- **Tự cài đặt PBO/CPCV:** loại — CSCV và purge/embargo của thư viện khớp bài báo và có test; tiết kiệm ~300 dòng.
- **Dùng `effective_n_trials` làm `N_eff`:** loại — phụ thuộc thứ tự trial chứ không phải tương quan lợi nhuận; §4.1 chỉ định ONC.
