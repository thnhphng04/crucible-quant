# ADR-0029 — Giao diện review chỉ đọc, đọc thẳng ledger

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-24
- **Task:** — (công cụ, không thuộc task lộ trình) · **Kiến trúc:** §4.1 (ledger), §4.2 (vòng đời campaign), §3.1.11 (so sánh)

## Bối cảnh

Muốn đọc xem một campaign đã xác lập được gì thì phải truy vấn tay `ledger/crucible.db` hoặc chạy lại `qc compare --report`. Lần chạy protocol v2 hoàn chỉnh đầu tiên có 552 trial, 941 audit event và năm gate result cho mỗi ứng viên; đúng những phần quan trọng khi review — gate nào chặn ứng viên, arm nào mang cảnh báo, file lock trên đĩa còn khớp với lock lúc mở campaign không — lại là những phần dựng lại bằng SQL mỗi lần rất mất công.

Một giao diện đặt trên dữ liệu nghiên cứu là một mặt tiếp xúc rò rỉ, nên phải chốt hai điều trước khi làm. Thứ nhất, nó không được ghi: P2 quy định ledger API là đường duy nhất để chạy backtest, và một thao tác "giúp cho tiện" từ trình duyệt sẽ đặt một người ghi thứ hai bên cạnh nó. Thứ hai, nó không được trở thành đường thứ hai chạm tới `holdout/`.

Lớp `Ledger` là lớp ghi: nó mở cơ sở dữ liệu ở chế độ đọc-ghi, giữ run lock và migrate schema khi mở. Trỏ giao diện vào đó nghĩa là có một tiến trình web ghi được ledger và tranh lock với phần nghiên cứu đang chạy.

## Quyết định

1. **Một package riêng `quantcrucible.review`**, phục vụ bằng FastAPI trên `127.0.0.1` qua `cli review`. Nó chỉ làm phần trình bày.
2. **Nó tự mở kết nối SQLite ở chế độ chỉ đọc** — `file:…?mode=ro`, `PRAGMA query_only=ON`, mỗi request nằm trong một transaction kết thúc bằng `ROLLBACK` — thay vì đi qua `Ledger`. Nó không bao giờ migrate: schema khác với schema nó được viết cho (`SUPPORTED_SCHEMA`) là một lỗi hiện ra cho người review, không phải thứ đem đi sửa bằng cách mở file ở chế độ đọc-ghi.
3. **Mọi route đều là GET** và ứng dụng không phơi ra đường ghi nào. Một contract import-linter cấm `quantcrucible.review` import `agent`, `validation`, `execution`, `core`, `data`, `config` và `holdout`, nên mặt tiếp xúc này không thể lan vào các tầng nghiên cứu.
4. **Artifact chỉ được phục vụ từ trong `results/`**, đường dẫn được resolve và đối chiếu với thư mục cha cho phép trước khi đọc, nên một `returns_path` hay một strategy hash lấy từ cơ sở dữ liệu không thể trỏ tới file ở chỗ khác trên đĩa.
5. **Giao diện nói trạng thái nghiên cứu, không nói kết quả.** Ngân sách chưa đủ thì đọc là "Chưa kết luận"; cảnh báo phân kỳ hiện ra như một dữ kiện audit không quyết định gì; dữ liệu thiếu đọc là "Chưa có" và không bao giờ được vẽ thành số 0.

## Hệ quả

- Review không còn cần SQL, và đường đọc không bao giờ lấy được lock của ledger hay chặn một campaign đang chạy.
- Read model lặp lại hiểu biết về schema (tên bảng và tên cột trong các chuỗi SQL). Một lần migrate ledger buộc phải nâng `SUPPORTED_SCHEMA` và sửa `repository.py`; trước khi đó giao diện từ chối mở file chứ không hiển thị dữ liệu sai.
- `cli review` phục vụ `ui/dist`, nên front-end đã build phải tồn tại. `scripts/review_demo_server.py` dựng một ledger demo dùng một lần cho lần chạy Playwright, nên test end-to-end không bao giờ đọc `ledger/`, `config/` hay `holdout/` thật.
- Quy ước front-end từ nay cũng nằm trong repo: TypeScript strict, pin phiên bản npm, Vitest cho component và Playwright cho lần chạy smoke trên desktop.

## Các phương án đã cân nhắc

- **Dùng lại `Ledger` bên trong ứng dụng web** — nó mở cơ sở dữ liệu ở chế độ đọc-ghi và lấy lock; một bên chỉ đọc không được phép làm cả hai.
- **Một báo cáo HTML tĩnh sinh ra cho mỗi campaign** — không lọc, không đi sâu được, và cũ ngay khi trial tiếp theo được ghi.
- **Mở giao diện ra ngoài localhost** — sẽ cần xác thực và một lần rà soát xem nó được phép hiện gì; giai đoạn 0–2 không cần điều đó.
