# ADR-0011 — Gate ④: tập cấu hình và CSCV dạng vector hoá

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-03 · **Kiến trúc:** §3.2 (④, "PBO thực sự kiểm tra gì"), D13, §3.3.2 · **Vấn đề mở:** O14, O15 (mở mới)

## Bối cảnh

§3.2 định nghĩa tập cấu hình của gate ④ (lưới TUNABLE ±30% + các biến thể vòng lặp đã thực sự thử, `M ≤ 200`) và quy tắc chọn (Sharpe IS cao nhất), loại khi PBO ≥ `pbo_max`. Chưa rõ: cấu hình lưới có phải trial không, lưới chạy thế nào, và chi phí của CSCV. ADR-0007 định bọc PBO của `purgedcv`, nhưng nó tính metric cho từng cấu hình trên từng lát bằng Python: S = 16 (12 870 tổ hợp) trên ma trận 125 × 2 800 mất 85 giây — cho mỗi ứng viên.

## Quyết định

1. **Cấu hình lưới không phải trial** (người dùng quyết, 21/9/2026): chúng không bao giờ thay tham số của ứng viên. Ma trận lợi nhuận được lưu ở `results/pbo/<campaign>/<candidate>.parquet` (+ `.configs.json`) và được tham chiếu từ `gate_results.detail`; `trials` không nhận gì.
2. **Tập cấu hình** = tham số của ứng viên, rồi mọi bộ tham số có **cùng `strategy_hash`** đã có trong `trials` của campaign này (ví dụ các dòng calibration), rồi `pbo_grid(..., center=candidate.params)` — bỏ trùng, giới hạn `max_configs`, ứng viên luôn đứng đầu. Lưới được đặt tâm ở tham số thật của ứng viên, không phải giá trị mặc định đã khai báo. Ít hơn 2 cấu hình ⇒ loại (fail closed).
3. **Một job sandbox** `grid_backtest` chạy mọi cấu hình với cùng dữ liệu, chi phí và sizing như gate ③ (`backtest_options` từ lock) và trả về ma trận `(M × T)`. Timeout của job là `max(300 s, 30 s × M)` (trường mới `SandboxJob.timeout_s`).
4. **Tự viết CSCV vector hoá** trong `validation/pbo.py` (điều chỉnh ADR-0007 §3): tổng và tổng bình phương theo block → Sharpe cho mọi tổ hợp cùng lúc. Cùng định nghĩa với `purgedcv` (ddof = 1, lát suy biến ⇒ 0, argmax đầu tiên, hạng trung bình, ω = rank/(M+1)); một test ghim PBO và logit bằng `purgedcv` trên ma trận ngẫu nhiên. S = 16 trên 200 × 2 800 mất ~0,6 giây. `n_splits` được ghi vào lock (`derived.pbo.n_splits = 16`).
5. **PBO, slope, M là private** (`EvaluationReport.private` + `gate_results`), không bao giờ vào `public` hay feedback.
6. `candidate_pipeline()` = ①a → ①b → ② → ③ → ④; `run_candidate` vẫn mặc định dùng pipeline GĐ 0 tới khi P1-12 chuyển CLI sang.

## Hệ quả

- INV-41: `test_pbo_reference_example` (tính tay từ định nghĩa trong bài báo, S = 2), `test_pbo_noise` (≈ 0.5), `test_pbo_planted_edge` (→ 0), cùng `test_matches_purgedcv`.
- **O14:** job lưới tốn M lần backtest đầy đủ (vài giây mỗi lần trên NautilusTrader) — chấp nhận được với chiến lược viết tay của GĐ 1, quá chậm cho vòng lặp GĐ 2.
- **O15:** "các biến thể vòng tinh chỉnh đã thử" có *code khác* thì không chạy lại được: ledger lưu `strategy_hash`, không lưu source. Cần kho source của GĐ 2.

## Phương án đã cân nhắc

- **Bọc PBO của `purgedcv`:** loại vì tốc độ (85 giây ở S = 16); giữ làm oracle trong test.
- **S = 10:** loại — 16 là chuẩn của bài báo; tốc độ đã giải quyết bằng vector hoá.
- **Cấu hình lưới là trial (`source='pbo_grid'`):** người dùng đã loại — sẽ nhân `N_raw` lên ~100 lần mỗi ứng viên cho những cấu hình không ai chọn.
