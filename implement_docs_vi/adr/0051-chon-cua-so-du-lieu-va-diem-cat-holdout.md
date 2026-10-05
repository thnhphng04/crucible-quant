# ADR-0051 — Chọn cửa sổ dữ liệu và điểm cắt IS/holdout

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-05
- **Task:** P3-64 · **Kiến trúc:** §4.2, §6.1, §10 D18, D28, §10.1 · **Vấn đề mở:** —

## Bối cảnh

Người dùng muốn chọn khoảng thời gian backtest: một cửa sổ dữ liệu chứa cả IS lẫn holdout, rồi chọn ngày cắt nó làm hai. Trước đây holdout luôn là `holdout_months` tháng cuối của cửa sổ. Các kho cũ (`data/is`, `data/perp`) lại được đọc nguyên, nên `research.data.start` không cắt gì ở đó. Một lock có thể ghi `start: 2022-10-02` trong khi mọi backtest chạy từ 2020-09-15. Dời điểm cắt sớm hơn còn mở ra một rủi ro mà quy tắc cũ chưa gặp: ledger đã có trial tìm kiếm trên 2018-01-01 → 2025-09-20 của cùng năm coin, nên một holdout lùi vào khoảng đó sẽ kiểm tra trên dữ liệu mà nghiên cứu đã nhìn thấy.

## Quyết định

- `research.data.holdout_start` (ngày, mặc định `null`) là điểm cắt. Khi được đặt, IS = `[start, holdout_start)` và holdout = `[holdout_start, end]`. Khi là `null`, holdout là `holdout_months` tháng cuối như trước. Holdout dài ít nhất 28 ngày. `data/window.py::holdout_window` là nơi duy nhất tính điểm cắt: đường fetch cũ, fetch và preflight perpetual, và dataset registry đều gọi nó.
- Dataset registry ghi điểm cắt vào `DatasetSpec.holdout_start_utc`. Field này chỉ vào spec hash khi được đặt, nên mọi dataset đã publish giữ nguyên id. Studio từ chối dataset có điểm cắt khác draft. Bước dữ liệu của Studio có ô ngày cắt; khi ô này có giá trị, ô số tháng bị vô hiệu.
- **Holdout sạch (D28).** `Ledger.latest_is_end()` là ngày IS cuối cùng của bất kỳ trial nào đã ghi, bất kể campaign, symbol hay thị trường (spot và perpetual của một coin đi cùng nhau). Holdout phải bắt đầu sau ngày đó. Phép kiểm chạy:
  - trước khi tải một dataset (`fetch_and_publish(latest_is_end=…)`, từ job của Studio);
  - khi mở campaign (`validation/run.py::admission_problems`), ở CLI, dry run và Studio.
- **Kho cũ được kiểm chứ không được cắt.** Lock mới mang `derived.data_window: v1`. Đường mở campaign của CLI từ chối kho cũ có bar đầu tiên không rơi vào `data.start` hoặc ngày kế tiếp. Một kho như vậy sẽ backtest trên lịch sử dài hơn hoặc ngắn hơn những gì lock ghi. Muốn cửa sổ khác kho đang có thì chuẩn bị dataset trong Studio.
- Lock không có `data.holdout_start` vẫn khớp với config để khoá này `null` (`config/lock.py::_comparable`).

## Hệ quả

- Với ledger ngày 2026-10-05, holdout sớm nhất bắt đầu từ 2025-09-21. Cắt sớm hơn đòi hỏi dữ liệu chưa trial nào nhìn thấy, mà năm coin này không có.
- Registry tải một cửa sổ mới, gồm cả đường giá theo phút của perpetual. Chọn cửa sổ tốn một lần tải, không phải cắt lại dữ liệu sẵn có trên đĩa: code nghiên cứu không bao giờ đọc file holdout.
- Giới hạn: phép kiểm chỉ xét trial đã ghi. Một campaign sau có IS chạy qua holdout chưa claim của campaign khác không bị chặn ở đây. Việc claim holdout đó chỉ còn được `holdout_collision` bảo vệ (chồng lấn với holdout *đã claim*).
- Kiến trúc: D18 nói cửa sổ và điểm cắt được chọn; D28 là quy tắc holdout sạch; ví dụ config §10.1 có `holdout_start`.

## Các phương án đã cân nhắc

- Cắt kho cũ theo `data.start` trong session — phía IS làm được, nhưng phía holdout không dời được nếu không đọc file holdout. Một cơ chế duy nhất là registry thì đơn giản hơn.
- Chỉ kiểm chồng lấn theo từng symbol và thị trường — BTC spot và perpetual gần như một chuỗi; kiểm theo thị trường sẽ để một lần tìm kiếm spot làm bẩn holdout perpetual.
