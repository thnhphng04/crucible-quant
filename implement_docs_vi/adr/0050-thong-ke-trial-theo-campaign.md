# ADR-0050 — Thống kê trial theo từng campaign

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-05
- **Task:** P3-63 · **Kiến trúc:** §3.1.6, §3.1.11, §3.2 dòng ② ⑤ ⑥′, §4.1, §10 D27 · **Vấn đề mở:** —

## Bối cảnh

`N_raw`, `N_eff`, `V[SR]`, số portfolio variant và số lần thử không đo được trước đây đều đọc từ toàn ledger: mọi trial của mọi campaign, không bao giờ reset (§4.1, ADR-0008, ADR-0014). Ngày 2026-10-05 ledger có 2.525 trial (`N_eff` 1.490) từ tám campaign, bảy đã bỏ và một đang OPEN. Khi đó gate ② cần khoảng 5,3 năm dữ liệu IS ở Sharpe mục tiêu 1,5 thì một campaign mới mới nhận được trial đầu tiên, nên một campaign 1h trên khoảng ba năm dữ liệu (MinBTL cho phép `N ≤ 121`) bị chặn hoàn toàn. Người dùng quyết định (2026-10-05) rằng các campaign độc lập: mỗi campaign chỉ đếm trial của chính nó.

## Quyết định

- Lock mới ghi `derived.trial_scope: campaign_v1` (`validation/trial_scope.py`, do `new_campaign_derived` và binding của Studio ghi). Lock không có khoá này đếm toàn ledger như trước; tag lạ bị từ chối. Mọi campaign mở trước đó giữ cách đếm toàn ledger như lúc chúng mở.
- Dưới tag, `N` của gate ②, `N_raw`/`N_eff`/`V[SR]` của gate ⑤, số variant và phép thử độ nhạy theo số lần không đo được, DSR khi tăng chi phí của gate ⑥′, xếp hạng slot của danh mục, `RankContext` của GP ranking và báo cáo §3.1.11 chỉ đếm trial **của chính campaign này**. Các engine và scope trong cùng một campaign vẫn cộng chung một `N` (ý của ADR-0033 không đổi).
- `update_n_eff` gom cụm các trial của campaign và ghi lần gom cụm kèm `campaign_id` (migration 010 thêm `clustering_runs.campaign_id`). View `trial_stats` toàn ledger giờ chỉ đọc các lần gom cụm có `campaign_id` là NULL, nên lần gom cụm của một campaign không bao giờ thành lần gần nhất của toàn ledger.
- `Ledger.snapshot(campaign_id)` đếm trial và variant của campaign và ghi tên nó (`"scope"`). Freeze so một kết quả ⑤/⑥′ với snapshot mới trong đúng phạm vi mà kết quả đó đã tính: trial của campaign khác không còn làm nó thành stale.
- Kiểm ngân sách trial lúc mở campaign chỉ so ngân sách với `max_trials_within(số năm IS)`: campaign mới bắt đầu từ `N = 0`.
- Không đổi: mọi trial vẫn được ghi vào một ledger append-only duy nhất, không trial nào bị xoá hay reset (P2); các số toàn ledger vẫn được tính cho lock không có tag; ngưỡng giữ nguyên.

## Hệ quả

- Các campaign trên **cùng dữ liệu** không còn trừ vào DSR và MinBTL của nhau. Thử lại ở một campaign mới trên cùng coin và cùng khoảng IS không bị phạt bởi những gì các campaign trước đã thử — đúng rủi ro ADR-0033 đã nêu ("reset hình phạt đa kiểm định"). Phần bảo vệ còn lại giữa các campaign là holdout: mỗi khoảng chỉ được claim và mở một lần (P6, `holdout_collision`), nên một campaign tìm ra danh mục IS may mắn vẫn phải qua một phép thử chưa ai đụng tới, và không campaign nào sau đó dùng lại được holdout ấy.
- Ngân sách của chính một campaign vẫn bị MinBTL giới hạn: khoảng 121 trial cho ba năm IS ở mục tiêu 1,5, chia cho các engine, seed và scope của nó.
- Schema v10; trình đọc review nhận v6–v10. Migration không làm đổi các số toàn ledger của ngày 2026-10-05.
- Kiến trúc: §3.1.6, §3.1.11, §3.2 ⑤, §4.1 và khung ledger nói rõ một campaign đếm những trial nào; §10 thêm D27. ADR-0006, 0008, 0009, 0014, 0019, 0022 và 0033 có ghi chú sửa đổi ở dòng trạng thái.

## Các phương án đã cân nhắc

- Giữ cách đếm toàn ledger và chờ dữ liệu IS dài hơn — chặn campaign 1h nhiều năm.
- Đếm theo bộ dữ liệu (coin × khoảng IS) — gần với thống kê hơn, nhưng người dùng chọn campaign làm đơn vị; holdout đã giới hạn những gì một campaign có thể dùng lại.
- Áp phạm vi cho cả các campaign đã mở trước đó — kết quả gate của chúng đã tính trên toàn ledger; đổi cách chúng đếm là sửa hồ sơ sau khi đã ghi.
