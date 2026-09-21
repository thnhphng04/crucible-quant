# ADR-0014 — Gate ⑤: DSR danh mục và pipeline danh mục

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-08 · **Kiến trúc:** §3.2 dòng ⑤–⑥′, §3.1.6, §4.1 ("Đầu vào của gate ⑤ DSR", "Ước lượng `N_eff`") · **Vấn đề mở:** —

## Bối cảnh

Từ ⑤ trở đi đối tượng được kiểm tra là một danh mục, thứ mà `GatePipeline` của ứng viên không chở được (nó ghi dòng `trials` và sự kiện audit gắn với một chiến lược). §4.1 cố định đầu vào của gate ⑤ (`trial_stats` + `total_portfolio_variants`, áp lên lợi nhuận của danh mục hợp nhất) và nói `N_eff` được ước lượng lại ở mỗi lần đánh giá danh mục và DSR luôn được báo ở cả `N_raw` lẫn `N_eff` — nhưng không nói cái nào được so với `dsr_min`.

## Quyết định

1. **`PortfolioPipeline`** (`validation/portfolio_dsr.py`): các gate theo thứ tự ⑤ → ⑥′, dừng ở lần thất bại đầu tiên, một exception là một lần loại, và mọi kết quả đi vào `gate_results` với `candidate_id = portfolio_hash` (không có dòng `trials`, không có sự kiện audit — danh mục không phải là trial; việc chọn nó được đếm qua `portfolio_variants`).
2. **Gate ⑤** trước tiên chạy `update_n_eff` (một lần phân cụm mới trên mọi trial), rồi `portfolio_dsr(returns, trial_stats, total_portfolio_variants, periods_per_year)` (ADR-0008).
3. **Qua khi và chỉ khi DSR tại `N_eff` ≥ `dsr_min`.** `N_eff` là cách đếm có cơ sở (ONC + bước bảo vệ ý nghĩa, ADR-0009, không bao giờ gộp các trial không liên quan). DSR tại `N_raw` và cả hai benchmark nằm trong `gate_results.detail` ở mọi lần đánh giá.
4. `deflated_sharpe_ratio` chỉ được gọi từ `statistical.py` — một test quét cây source.

## Hệ quả

- INV-42: `tests/validation/test_portfolio_dsr.py::test_more_trials_lower_dsr` (thêm 200 trial từ engine khác, một nửa bị loại ⇒ DSR thấp hơn ở cả hai cách đếm), `::test_no_per_cell_dsr_api`.
- ONC được giới hạn ở k ≤ 50 và 3 lần khởi tạo (đã điều chỉnh ADR-0009) để gate ⑤ chạy trong vài giây với hàng trăm trial.

## Phương án đã cân nhắc

- **So DSR tại `N_raw`:** loại làm mặc định — §4.1 cho phép `N_eff` hạ ngưỡng khi có cơ sở, và bước bảo vệ giữ cho điều đó có cơ sở; `N_raw` vẫn được báo cáo. Siết về `N_raw` sẽ là một quyết định ở §10.
- **Dùng lại `GatePipeline` với một ứng viên giả:** loại — nó sẽ ghi một dòng `trials` cho danh mục và làm phình `N`.

## Sửa đổi — review commit d765668

- Mọi kết quả ⑤/⑥′ còn lưu `ledger_snapshot` — số trial và số biến thể danh mục trên toàn ledger lúc lần chạy bắt đầu (`Ledger.snapshot()`). DSR giảm khi một trong hai tăng, nên một kết quả chỉ còn hiệu lực khi snapshot chưa đổi; bước đóng băng kiểm tra điều này (sửa đổi ADR-0016, lỗi 3).
