# ADR-0041 — Chu kỳ stop là gene TUNABLE

- **Trạng thái:** Đã chấp nhận; guard ATR(14) riêng được sửa bởi [ADR-0047](0047-clause-bien-dong-va-mot-thuoc-atr.md)
- **Ngày:** 2026-10-02
- **Task:** P3-53 · **Kiến trúc:** §3.2, §3.3.1 luật 2, §10 D24 · **Vấn đề mở:** —

## Bối cảnh

Engine C chỉ tiến hóa độ rộng stop `k_stop`. Chu kỳ của stop bị viết cứng: ATR(14) cho stop ATR và Bollinger(8, 2) cho stop Bollinger (ADR-0036), cả hai được §3.3.1 luật 2 cho nằm ngoài ngân sách ≤ 6 TUNABLE. Renderer, parser genome, cổng ①a, engine router và chương trình GPU đều giả định hai hằng này. Người dùng quyết định (D24) cho phép tìm kiếm tune độ nhạy của stop, mà không đổi nghĩa của quy tắc vào lệnh và không đổi bất kỳ campaign nào đang có.

## Quyết định

- Lock mới ghi `derived.stop_period: tunable_v1` và `derived.max_tunables: 7`. Lock không có các khóa này nghĩa là chu kỳ cố định và trần 6.
- `Genome` thêm `stop_period: Param | None = None`, render thành TUNABLE `n_stop` ∈ [5, 50] ngay sau `k_stop`. `None` render từng byte như cũ, nên `strategy_hash` cũ không đổi.
- Stop ATR: feature riêng `"atr_stop": ind.atr(bars, self.p.n_stop)` và `stop = k_stop * x["atr_stop"]`. `atr` = ATR(14) vẫn là thước đo của guard `ready` và của `Distance`.
- Stop Bollinger: chu kỳ dải là `self.p.n_stop`; hệ số 2σ giữ cố định, vì `k_stop` đã co giãn độ rộng.
- Cổng ①a cưỡng chế trần của lock; dưới `tunable_v1` số 8 viết trực tiếp không còn được phép. Trần cứng của parser thành 7.
- C-gp và C-random đều lấy mẫu gene dưới lock `tunable_v1`. `param` dịch nó, và crossover lấy stop của mate thì lấy cả `stop_period`. Phạt độ phức tạp giữ thang cố định `n_params / 6`: mỗi TUNABLE tốn như nhau ở mọi campaign, và protocol so sánh mà các campaign `harness_test` đang mở đã khóa không đổi.
- Parser, engine router CPU và chương trình GPU (thêm `stop_slot` bên cạnh `atr_slot`) đọc gene; CPU ≡ GPU vẫn khớp từng bit.

## Hệ quả

- Mỗi genome có thêm một TUNABLE: lưới PBO quét `n_stop`, `n_params` và trần đều đếm nó, nên bậc tự do thêm vào được đo chứ không miễn phí.
- Campaign cũ không bị ảnh hưởng: render giống, trần giống, thang ranking giống khi chạy tiếp (INV-114). Campaign mới: INV-115.
- Sửa kiến trúc (làm cùng ADR này): §3.3.1 luật 2, §3.1.10 L2, tập cấu hình §3.2, D24, thay đổi 0.10 → 0.11.
- `STOP_PERIOD_RANGE = (5, 50)` là tạm thời; đổi nó là một phiên bản lock mới, không bao giờ sửa giữa campaign.

## Các phương án đã cân nhắc

- Giữ trần 6 và để `n_stop` dùng chung — quy tắc vào lệnh mất một tham số; người dùng không chọn.
- Một ATR(n_stop) chung cho guard, `Distance` và stop — tune stop sẽ âm thầm đổi thang ngưỡng vào lệnh và trộn hiệu ứng vào/thoát trong lưới PBO.
- Tune thêm hệ số σ của Bollinger — hai gene cho một stop, trùng vai trò một phần với `k_stop`.
