# ADR-0002 — Bổ sung schema ledger và config cho GĐ 0

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P0-02 · **Kiến trúc:** §3.2, §4.1, §4.2, §10, §10.1 · **Vấn đề mở:** O4, O5, O12

## Bối cảnh

Khi xây ledger mới lộ ra các lỗ hổng trong DDL của kiến trúc. §10.1 nói lock được hash vào `campaigns`, nhưng bảng không có cột nào cho việc đó (O4). `trials.cluster_id` được bước `N_eff` ghi sau khi insert, nên `trials` không thể chỉ-ghi-thêm tuyệt đối (O5). §3.2 yêu cầu mọi `GateResult` nằm trong ledger, nhưng các gate sau ③ không có chỗ để ghi, vì dòng `trials` không được sửa (O12). GĐ 0 còn cần hai tham số mà kiến trúc chưa đặt: Sharpe mục tiêu của MinBTL và rổ dữ liệu nghiên cứu. Cuối cùng, §3.2 nói các gate chạy theo `cost` tăng dần, trong khi chính bảng của nó xếp ② (cost 1) sau ①b (cost 2).

## Quyết định

1. **`campaigns`** thêm `lock_hash TEXT NOT NULL` và `holdout_lock_hash TEXT`. Campaign phải bắt đầu ở `OPEN`; chỉ `status` được đổi, và chỉ theo `OPEN → FROZEN → BURNED`.
2. **Không bảng nào nhận UPDATE hay DELETE**, cưỡng chế bằng trigger SQLite (chỉ `campaigns.status` đổi được như trên). Bỏ `trials.cluster_id`; kết quả gom cụm ghi vào `clustering_runs` + `trial_clusters`. `trial_stats.n_eff` = số cụm của lần gom mới nhất + số trial lần đó chưa gom (mỗi trial là một cụm riêng); chưa gom lần nào ⇒ `n_eff = n_raw`.
3. **`gate_results`** lưu mọi `GateResult`, đạt hay trượt, kèm `trial_id` khi ứng viên đã có. `trials` thêm `candidate_id` để nối hai bảng. `generation_log` vẫn ghi các sự kiện loại, thêm tên mới `LEAK_REJECT`, `MINBTL_REJECT`, `GATE_ERROR`, `CANDIDATE_SUBMITTED`.
4. **`trials.source`** nhận thêm `manual` (strategy viết tay, ví dụ EMA crossover của GĐ 0). Trial viết tay vẫn được tính vào `N` như mọi trial khác.
5. **`holdout_access`** chỉ được insert khi campaign đang `FROZEN` (trigger), và `frozen_at < accessed_at` (CHECK).
6. **Config, Nhóm B:** `minbtl_target_sharpe: 1.5` (D17; giá trị > 1.5 bị từ chối khi nạp — mục tiêu thấp hơn là chặt hơn) và `data: {exchange, symbols, timeframe, start, holdout_months}` (D18: Binance spot, 1d, BTC/ETH/SOL/BNB/XRP theo USDT từ 2018, 12 tháng cuối làm holdout).
7. **Thứ tự gate là thứ tự tường minh của bảng §3.2**, không phải sắp theo `cost`.

## Hệ quả

- Bảo đảm chỉ-ghi-thêm đúng với mọi client, kể cả `sqlite3` thô (có test).
- Gom cụm lại vẫn giữ lịch sử; có thể kiểm toán `N_eff` theo thời gian.
- Đã cập nhật §3.2, §4.1, §4.2, §10 (D17, D18) và §10.1 của kiến trúc ở cả hai ngôn ngữ.
- Với mục tiêu MinBTL 1.0, ~7 năm dữ liệu IS miễn phí sẽ loại mọi ứng viên sau ~100–200 trial; 1.5 cho phép vài nghìn (MinBTL ≈ 4.6 năm ở N = 1000).

## Các phương án đã cân nhắc

- **Trigger chỉ cho UPDATE `trials.cluster_id`** — người dùng loại: mất lịch sử gom cụm và làm yếu nguyên tắc "không bao giờ UPDATE".
- **Chỉ ghi dòng `trials` sau gate cuối cùng của ứng viên** — loại: nếu crash giữa ③ và ④ sẽ mất một trial, làm `N` bị đếm thiếu.
