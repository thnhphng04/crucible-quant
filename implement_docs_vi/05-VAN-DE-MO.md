# 05 — Vấn đề mở

Những điều chưa biết ở mức triển khai. Mỗi mục ghi rõ task phải giải quyết nó. Quyết định ở mức kiến trúc nằm trong sổ quyết định §10 của kiến trúc; mục duy nhất còn mở ở đó là **D4**.

Đóng một mục bằng cách ghi lại câu trả lời (thành ADR nếu đó là một quyết định) và chuyển dòng đó xuống "Đã đóng".

| ID | Câu hỏi | Chặn | Giải quyết ở | Ghi chú |
|---|---|---|---|---|
| O1 | **D4 — ngưỡng kinh tế** (`holdout_pass`) | Lần mở holdout đầu tiên, live | Trước khi P1-10 được *dùng* (không phải khi xây) | Kiến trúc §10. P1-10 từ chối mở holdout khi giá trị này chưa đặt, nên việc xây không phải chờ |
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
| O3 | Phiên bản NautilusTrader, wheel, limit khớp khi đi xuyên qua, trượt giá theo tick | ghim `>=1.231,<1.232` (wheel cp312 Windows + manylinux); khớp ở giá mở kế tiếp nhờ tick mở tổng hợp + độ trễ 1 ns; `prob_fill_on_limit=0`; trượt giá tính thành phí taker cộng thêm theo bps | [ADR-0003](adr/0003-ngu-nghia-khop-lenh-nautilus.md) |
| O6 | Docker trên Windows: đường dẫn mount, `--read-only` + tmpfs, chi phí khởi động | Docker Desktop (WSL2) mount thư mục job trong `%TEMP%` bình thường; `--read-only` + tmpfs `/tmp` 256 MB hoạt động; khởi động container ≈ 0,6 s, một job điển hình 2,5–4 s (import NautilusTrader ≈ 2 s, chỉ job backtest mới nạp) — ổn cho GĐ 0; worker sống lâu vẫn là phương án dự phòng cho GĐ 2 | [ADR-0004](adr/0004-sandbox-docker.md) |
| O2 | API thật của `purgedcv`: DSR có nhận `N` và `V[SR]` riêng không? Đầu vào/quy tắc chọn của PBO là gì? License/phiên bản? | 0.1.6, MIT, ghim `<0.2`; DSR nhận `N` và `V[SR]` riêng nhưng chỉ nhận chuỗi lợi nhuận — DSR/PSR tự cài đặt từ moment, PBO (`(n_configs, n_obs)`, argmax Sharpe trên IS) và CPCV được bọc lại | [ADR-0007](adr/0007-purgedcv-boc-lai-hay-tu-cai-dat.md) |
| O8 | Leaky oracle cấp 4 cần dữ liệu point-in-time | một báo cáo tổng hợp công bố 3 bar sau ngày sự kiện nhưng được ghép tại ngày sự kiện; dữ liệu PIT thật cần từ GĐ 4 (cổ phiếu) | [ADR-0005](adr/0005-leaky-oracle-va-guardrail-dong.md) |
