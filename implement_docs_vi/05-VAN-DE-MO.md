# 05 — Vấn đề mở

Những điều chưa biết ở mức triển khai. Mỗi mục ghi rõ task phải giải quyết nó. Quyết định ở mức kiến trúc nằm trong sổ quyết định §10 của kiến trúc; mục duy nhất còn mở ở đó là **D4**.

Đóng một mục bằng cách ghi lại câu trả lời (thành ADR nếu đó là một quyết định) và chuyển dòng đó xuống "Đã đóng".

| ID | Câu hỏi | Chặn | Giải quyết ở | Ghi chú |
|---|---|---|---|---|
| O1 | **D4 — ngưỡng kinh tế** (`holdout_pass`) | Lần mở holdout đầu tiên, live | Trước khi P1-10 được *dùng* (không phải khi xây) | Kiến trúc §10. P1-10 từ chối mở holdout khi giá trị này chưa đặt, nên việc xây không phải chờ |
| O2 | API thật của `purgedcv`: DSR có nhận `N` và `V[SR]` riêng không? Đầu vào/quy tắc chọn của PBO là gì? License/phiên bản? | P1-02, P1-03, P1-04 | P1-01 | Ghi chú §6 của kiến trúc. Dự phòng: tự cài đặt DSR/PBO (~50 dòng mỗi cái) |
| O3 | NautilusTrader: ghim phiên bản nào; có wheel cho Windows + Python 3.12 không; `FillModel` có biểu diễn được "chỉ khớp khi giá đi xuyên qua" và trượt giá cố định theo tick không? | P0-10 | P0-10 (làm spike trước) | Nếu `FillModel` không làm được: kế thừa lớp hoặc hậu xử lý các lần khớp — ghi vào ADR |
| O4 | `campaigns` không có cột cho hash của lock, dù §10.1 nói lock được "hash vào bảng `campaigns`" | P0-02, P0-03 | P0-02 | Đề xuất: thêm `lock_hash TEXT NOT NULL` (+ `holdout_lock_hash`). Ghi thành ADR, rồi phản ánh vào §4.2 của kiến trúc (VI trước) |
| O5 | `trials.cluster_id` được ghi sau khi insert (bởi bước `N_eff`), nên `trials` không thể chỉ-ghi-thêm tuyệt đối | P0-02 | P0-02 | Đề xuất: trigger cho phép UPDATE chỉ thay đổi `cluster_id`. Phương án khác: bảng chỉ-ghi-thêm riêng `trial_clusters(trial_id, run_ts, cluster_id)`, giữ được cả lịch sử mỗi lần phân cụm lại — có lẽ tốt hơn; quyết định bằng ADR |
| O6 | Docker trên Windows: đường dẫn mount của Docker Desktop + WSL2, hành vi `--read-only` + tmpfs, chi phí khởi động mỗi lần đánh giá (hàng nghìn lần chạy) | P0-08 | P0-08 | Nếu khởi động mỗi lần quá chậm: một container sandbox sống lâu cho mỗi worker, mỗi job một thư mục tmp mới — giữ nguyên giới hạn mạng/env/mount |
| O7 | Bảo vệ file holdout trên Windows (không có `chmod 0400`) | P0-09 | ADR-0001 (cách làm), P0-09 (cài đặt) | Thuộc tính read-only + `icacls` cấm ghi với user; SHA256 trong `holdout.lock` mới là cơ chế phát hiện sửa đổi thực sự |
| O8 | Leaky oracle cấp 4 (thông tin công bố trễ) cần dữ liệu point-in-time; dữ liệu crypto miễn phí không có | P0-11 | P0-11 | Dùng một feature tổng hợp với độ trễ công bố đã biết; dữ liệu PIT thật chỉ quan trọng từ GĐ 4 (cổ phiếu) |
| O9 | Khi nào chuyển ledger từ SQLite sang Postgres | — | GĐ 2 | Dấu hiệu: nhiều tiến trình ghi đồng thời từ pipeline bất đồng bộ (§3.1.10) gây tranh chấp khóa ở chế độ WAL |
| O10 | Cài đặt ONC: tự viết hay dùng thư viện; chọn số cụm theo silhouette trên hàng nghìn chuỗi có thể chậm | P1-05 | P1-05 | Bắt đầu từ thuật toán ONC đã công bố của López de Prado; cache ma trận tương quan theo kiểu tăng dần |
| O11 | `PlaceholderSizer` dùng ở GĐ 0 khi chưa có sizing thật | — | P1-06 | Phải bị xóa ở P1-06; grep khi review P1-06 |

## Đã đóng

| ID | Câu hỏi | Trả lời | Ở đâu |
|---|---|---|---|
| — | Bố cục code so với §5 của kiến trúc (`core/` ở gốc, package `quantcrucible`); code và dữ liệu holdout chung một thư mục | src layout; dữ liệu holdout ở gốc, code trong package | [ADR-0001](adr/0001-src-layout-va-tach-holdout.md) |
