# 05 — Vấn đề mở

Những điều chưa biết ở mức triển khai. Mỗi mục ghi rõ task phải giải quyết nó. Quyết định ở mức kiến trúc nằm trong sổ quyết định §10 của kiến trúc; mục duy nhất còn mở ở đó là **D4**.

Đóng một mục bằng cách ghi lại câu trả lời (thành ADR nếu đó là một quyết định) và chuyển dòng đó xuống "Đã đóng".

| ID | Câu hỏi | Chặn | Giải quyết ở | Ghi chú |
|---|---|---|---|---|
| O1 | **D4 — ngưỡng kinh tế** (`holdout_pass`) | Lần mở holdout đầu tiên, live | Trước khi P1-10 được *dùng* (không phải khi xây) | Kiến trúc §10. P1-10 từ chối mở holdout khi giá trị này chưa đặt, nên việc xây không phải chờ |
| O2 | API thật của `purgedcv`: DSR có nhận `N` và `V[SR]` riêng không? Đầu vào/quy tắc chọn của PBO là gì? License/phiên bản? | P1-02, P1-03, P1-04 | P1-01 | Ghi chú §6 của kiến trúc. Dự phòng: tự cài đặt DSR/PBO (~50 dòng mỗi cái) |
| O3 | NautilusTrader: ghim phiên bản nào; có wheel cho Windows + Python 3.12 không; `FillModel` có biểu diễn được "chỉ khớp khi giá đi xuyên qua" và trượt giá cố định theo tick không? | P0-10 | P0-10 (làm spike trước) | Nếu `FillModel` không làm được: kế thừa lớp hoặc hậu xử lý các lần khớp — ghi vào ADR |
| O6 | Docker trên Windows: đường dẫn mount của Docker Desktop + WSL2, hành vi `--read-only` + tmpfs, chi phí khởi động mỗi lần đánh giá (hàng nghìn lần chạy) | P0-08 | P0-08 | Nếu khởi động mỗi lần quá chậm: một container sandbox sống lâu cho mỗi worker, mỗi job một thư mục tmp mới — giữ nguyên giới hạn mạng/env/mount |
| O8 | Leaky oracle cấp 4 (thông tin công bố trễ) cần dữ liệu point-in-time; dữ liệu crypto miễn phí không có | P0-11 | P0-11 | Dùng một feature tổng hợp với độ trễ công bố đã biết; dữ liệu PIT thật chỉ quan trọng từ GĐ 4 (cổ phiếu) |
| O9 | Khi nào chuyển ledger từ SQLite sang Postgres | — | GĐ 2 | Dấu hiệu: nhiều tiến trình ghi đồng thời từ pipeline bất đồng bộ (§3.1.10) gây tranh chấp khóa ở chế độ WAL |
| O10 | Cài đặt ONC: tự viết hay dùng thư viện; chọn số cụm theo silhouette trên hàng nghìn chuỗi có thể chậm | P1-05 | P1-05 | Bắt đầu từ thuật toán ONC đã công bố của López de Prado; cache ma trận tương quan theo kiểu tăng dần |
| O11 | `PlaceholderSizer` dùng ở GĐ 0 khi chưa có sizing thật | — | P1-06 | Phải bị xóa ở P1-06; grep khi review P1-06 |
| O13 | Template theo module: các block có tên `entry` / `exit` / `regime` ánh xạ vào `indicators()` / `signal()` thế nào | Tiến hóa theo module (D16 ≠ `joint`) | GĐ 2 | Bộ phân tích và cách hash theo `evolve_scope` đã hỗ trợ block có tên (P0-05); template của GĐ 0 chỉ có một block `joint` |

## Đã đóng

| ID | Câu hỏi | Trả lời | Ở đâu |
|---|---|---|---|
| — | Bố cục code so với §5 của kiến trúc (`core/` ở gốc, package `quantcrucible`); code và dữ liệu holdout chung một thư mục | src layout; dữ liệu holdout ở gốc, code trong package | [ADR-0001](adr/0001-src-layout-va-tach-holdout.md) |
| O4 | `campaigns` không có cột cho hash của lock | thêm cột `lock_hash` + `holdout_lock_hash` | [ADR-0002](adr/0002-bo-sung-ledger-va-config.md) |
| O5 | `trials.cluster_id` bị ghi sau khi insert | bảng chỉ-ghi-thêm `clustering_runs` + `trial_clusters`; không bảng nào nhận UPDATE | [ADR-0002](adr/0002-bo-sung-ledger-va-config.md) |
| O12 | Các gate sau ③ không có chỗ ghi `GateResult` | bảng chỉ-ghi-thêm `gate_results` | [ADR-0002](adr/0002-bo-sung-ledger-va-config.md) |
| O7 | Bảo vệ file holdout trên Windows | thuộc tính read-only + `icacls /deny <user>:(WD,AD,WEA,WA,DE)` — chỉ quyền cụ thể: quyền chung `(W)` có cả SYNCHRONIZE nên làm file không đọc được (phát hiện ở lần cắt thật đầu tiên; test `test_hardened_file_stays_readable`); SHA256 trong `holdout.lock` vẫn là cơ chế phát hiện sửa đổi thực sự | P0-09, ADR-0001 |
