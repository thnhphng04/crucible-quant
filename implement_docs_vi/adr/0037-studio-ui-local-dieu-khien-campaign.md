# ADR-0037 — Studio UI local để điều khiển campaign

- **Trạng thái:** Đã chấp nhận; triển khai ngày 2026-09-28
- **Ngày:** 2026-09-27
- **Task:** P3-26 · **Kiến trúc:** §4.1 (ledger), §4.2 (vòng đời campaign), §5 (cấu trúc thư mục), §10 D22 · **Kế tiếp:** ADR-0029

## Bối cảnh

ADR-0029 cố ý làm `cli review` thành UI chỉ đọc trên ledger. Ranh giới đó vẫn đúng cho việc xem lại lịch sử campaign, nhưng nó không cho người dùng cấu hình campaign mới, chuẩn bị dữ liệu, preview lock và quota, tạo campaign, chạy/tiếp tục search, hoặc enqueue bước portfolio research từ trình duyệt một cách an toàn.

Đường ghi hiện tại nằm rải rác qua adapter CLI, `validation/run.py`, lock campaign, carve dữ liệu, setup protocol so sánh và lệnh portfolio. Một số đường có thể ngầm mở campaign, phụ thuộc lock active, hoặc giả định vị trí dataset cố định. Nếu đưa vào trình duyệt, các giả định đó thành vấn đề an toàn trừ khi việc tạo campaign, danh tính dataset, quyền sở hữu run và trạng thái job được làm tường minh.

Studio hiện thực hiện hợp đồng này qua `cli studio`; preflight perpetual nguồn thật còn lại được theo dõi ở P3-24.

## Quyết định

1. **Giữ review và control tách nhau.** `quantcrucible.review` vẫn chỉ đọc và chỉ GET. Package mới `quantcrucible.studio` sở hữu route ghi, trạng thái job local và bảo mật riêng của Studio. `cli studio` có thể ghép review router cho màn hình đọc, nhưng `cli review` không được import hoặc phơi đường ghi của Studio.
2. **Dùng một đường nghiệp vụ cho CLI và UI.** Tạo campaign, run/resume, setup so sánh và đánh giá portfolio đi qua service nhận campaign, draft hoặc dataset identity tường minh. Lệnh CLI trở thành adapter trên các service đó. `run_campaign(id)` không bao giờ tự tạo campaign.
3. **Thêm dataset v1 bất biến.** Campaign mới dùng `data/datasets/<dataset_id>/` cộng `holdout/datasets/<dataset_id>/`, với manifest chứa spec chuẩn, path tương đối, coverage và SHA256. Lock campaign ghi `dataset_id`, hash manifest, hash holdout lock và data end đã chốt. Lock legacy thiếu `dataset_id` giữ resolver hiện có và không bị viết lại.
4. **Create phải idempotent và phục hồi được.** Draft có version và không ghi config, lock hoặc ledger. Preview dùng cùng validator với Create nhưng không có side effect. Create cần preview token và request key, lấy writer lock của project, kiểm lại input, ghi lock chuẩn, ghi request row vào ledger và cập nhật file active tương thích theo thứ tự phục hồi được.
5. **Lưu job ngoài ledger.** Studio giữ `studio/jobs.sqlite` cho job queued/running/stopping/succeeded/failed/interrupted/unknown, process identity, heartbeat, log path và idempotency key. Ledger vẫn là sổ thống kê; trạng thái job là trạng thái vận hành. Mỗi project root chỉ được có một writer job.
6. **Giới hạn ranh giới trình duyệt local.** Studio bind `127.0.0.1`, từ chối Host/Origin lạ, yêu cầu token/header cùng origin cho mutation, chỉ nhận JSON allowlist, giới hạn body, dùng argv subprocess cố định với `shell=False`, và không bao giờ nhận code thực thi, command tùy ý, URL hoặc path filesystem từ browser.
7. **Giữ thao tác nghiên cứu không đảo ngược ngoài nút Run của Studio.** Studio không tự freeze, burn, mở holdout hoặc đặt lệnh live. `harness_test` khóa protocol so sánh trước trial 1; `research` có thể enqueue đánh giá portfolio sau search.

## Hệ quả

- UI có thể thành mặt vận hành local thật mà không làm yếu bảo đảm chỉ đọc của ADR-0029.
- Danh tính dataset trở thành một phần hợp đồng campaign. Việc này tăng khối lượng triển khai, nhưng ngăn đổi timeframe, market, khoảng ngày hoặc byte nguồn dưới một campaign đã có.
- Sự thật job và sự thật nghiên cứu được tách có chủ ý. Sau restart, Studio phải đối soát process identity, heartbeat và lock thay vì suy rằng campaign `OPEN` nghĩa là job còn chạy.
- Bề mặt crash quanh Create lớn hơn, nên test phải bao phủ trạng thái pending lock, lock-only, ledger-only và active-complete, cộng retry double-click/idempotency-key.
- Command, API, supervisor và luồng UI đã được kiểm chứng bằng 993 test Python (trừ docker/network/slow), 28 test component, 18 test Playwright, lint/typecheck/build và doc mirror EN/VI ngày 2026-09-28.

## Các phương án đã cân nhắc

- **Thêm route POST vào `quantcrucible.review`.** Bị từ chối vì nó xóa ranh giới ADR-0029 và bắt read model import quan tâm của tầng ghi.
- **Cho browser sửa `config/user.yaml` rồi gọi code CLI hiện có.** Bị từ chối vì draft sẽ có side effect và `run` vẫn có thể tạo hoặc nhắm sai campaign qua giả định active-lock.
- **Dùng ledger làm job queue.** Bị từ chối vì ledger là sổ thống kê append-only, còn trạng thái job gồm process identity, heartbeat, đối soát stop/restart và log path.
- **Phục vụ Studio ra ngoài localhost.** Bị từ chối ở giai đoạn này; truy cập từ xa cần xác thực và một lần review riêng về thứ được hiển thị hoặc mutation.
