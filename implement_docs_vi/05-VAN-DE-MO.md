# 05 — Vấn đề mở

Những điều chưa biết ở mức triển khai. Mỗi mục ghi rõ task phải giải quyết nó. Quyết định ở mức kiến trúc nằm trong sổ quyết định §10 của kiến trúc; mục duy nhất còn mở ở đó là **D4 phần cấp vốn live** (phần holdout đã chốt, ADR-0020).

Đóng một mục bằng cách ghi lại câu trả lời (thành ADR nếu đó là một quyết định) và chuyển dòng đó xuống "Đã đóng".

| ID | Câu hỏi | Chặn | Giải quyết ở | Ghi chú |
|---|---|---|---|---|
| O9 | Khi nào chuyển ledger từ SQLite sang Postgres | — | GĐ 2 | Dấu hiệu: nhiều tiến trình ghi đồng thời từ pipeline bất đồng bộ (§3.1.10) gây tranh chấp khóa ở chế độ WAL |
| O13 | Template theo module: các block có tên `entry` / `exit` / `regime` ánh xạ vào `indicators()` / `signal()` thế nào | Tiến hóa theo module (D16 ≠ `joint`) | GĐ 2 | Bộ phân tích và cách hash theo `evolve_scope` đã hỗ trợ block có tên (P0-05); template của GĐ 0 chỉ có một block `joint` |
| O14 | Lưới gate ④ = M lần backtest NautilusTrader đầy đủ cho mỗi ứng viên (vài giây mỗi lần) — quá chậm cho vòng lặp GĐ 2 | Thông lượng GĐ 2 | GĐ 2 | Phương án: chạy song song nhiều container sandbox; một bộ backtest thô vector hoá chỉ cho lưới (phải khớp NautilusTrader trên một tập tham chiếu) |
| O15 | "Các biến thể vòng tinh chỉnh đã thử" của gate ④ có code khác thì không chạy lại được: `trials` lưu `strategy_hash`, không lưu source | Tập cấu hình §3.2 đầy đủ trong vòng lặp | GĐ 2 | Cần kho source của mọi ứng viên đã đo (archive của agent); GĐ 1 chỉ dùng biến thể tham số cùng hash (ADR-0011) |

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
| O10 | Cài đặt ONC: tự viết hay dùng thư viện; chọn số cụm theo silhouette trên hàng nghìn chuỗi có thể chậm | tự viết ONC (k-means của scikit-learn) + bước bảo vệ ý nghĩa để không bao giờ gộp các trial không liên quan; O(n²) ổn với vài trăm, xem lại ở GĐ 2 | [ADR-0009](adr/0009-n-eff-onc-kem-kiem-dinh-y-nghia.md) |
| O11 | `PlaceholderSizer` dùng ở GĐ 0 khi chưa có sizing thật | đã xoá; `RiskSizer` (execution) dựa trên `PositionSizer` (core); hằng số sizing khoá trong `derived.sizing` | [ADR-0010](adr/0010-risk-sizing-trong-duong-thuc-thi.md) |
| O16 | Campaign dữ liệu thật mở ở GĐ 0 có trước `derived.sizing` và chỉ rời OPEN được bằng cách mở holdout | trạng thái `ABANDONED` có audit, là trạng thái cuối (trial vẫn trong `N`, lock lưu trữ nguyên vẹn, holdout chưa dùng); campaign mới cần D4 đã đặt | [ADR-0019](adr/0019-campaign-bi-bo-va-d4-truoc-campaign-moi.md) |
| O1 | **D4 — ngưỡng kinh tế** (`holdout_pass`) | Sharpe OOS quy năm tối thiểu của toàn bộ danh mục đã đóng băng trên holdout, sau phí và trượt giá, tham chiếu 0; **1,3**, khóa theo campaign; PASS ≠ chấp thuận live (tiêu chí cấp vốn live vẫn mở trong D4) | [ADR-0020](adr/0020-d4-y-nghia-va-gia-tri-holdout-pass.md) |
| O17 | Một lần thử lỗi trước khi đo được gì có tính vào `N` không? | không: không phải trial, không trong `N` hay V[SR], không Sharpe bịa; đã đo rồi bị loại vẫn tính; lỗi sau khi có đầu ra do người phân loại; luôn truy vết; DSR "mỗi lần một cụm" được báo như phép kiểm độ nhạy | [ADR-0022](adr/0022-quy-uoc-dem-lan-thu-khong-do-duoc-gi.md) |
