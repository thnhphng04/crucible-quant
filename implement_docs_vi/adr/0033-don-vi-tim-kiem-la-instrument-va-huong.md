# ADR-0033 — Đơn vị tìm kiếm là (instrument, direction)

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-25
- **Task:** P3-11 … P3-15 · **Kiến trúc:** §3.1.11, §3.2.1, §4.1 · **Thay thế:** đơn vị `(engine, seed)` của ADR-0024 và ADR-0027

## Bối cảnh

Tới giờ, một chiến thuật được tìm rồi chạy trên năm cặp spot cùng lúc. `validation/run.py::make_candidate` tạo ra điều đó bằng đúng một dòng — `universe = tuple(is_data)` — và vì thế mọi candidate đều là chiến thuật cả rổ, dù có ai định vậy hay không.

`PLAN-PER-INSTRUMENT-STRATEGIES.en.md`, chốt ngày 25/9/2026, thay điều đó bằng một cuộc tìm kiếm độc lập cho từng `(instrument, direction)`: BTC-long và BTC-short là hai cuộc tìm khác nhau, không phải một chiến thuật đọc theo hai cách. Điều đó nới rộng cái đơn vị mà mọi thứ phía sau lấy làm khoá — quota của scheduler, seed ngẫu nhiên, archive MAP-Elites, slot danh mục, giao thức so sánh và giao diện review đều giả định `(engine, seed)`.

Spec yêu cầu một migration ledger "dùng giá trị legacy rõ ràng" cho 1.325 trial đang có. Điều đó bất khả thi: các trigger append-only ở `schema.sql:148-197` cấm `UPDATE` trên mọi bảng trừ `campaigns`. Không thể viết được một backfill, và ràng buộc đó là một tính năng — P2 tồn tại để một trial đã ghi không thể bị sửa lại về sau.

## Quyết định

1. **`instrument` và `direction` là cột ledger thật**, thêm bởi `migration_007_scope.sql` trên `trials` và `generation_log`, theo tiền lệ của migration 005. Chúng không cưỡi lên `universe`, vốn là một TEXT nối bằng dấu phẩy của cả rổ mà `is_gates.universe_bars` phân tích để chọn bars; nhồi thêm nghĩa vào đó biến mọi truy vấn thành so khớp chuỗi, còn `direction` thì chẳng có chỗ nào để ở cả.
2. **Scope legacy được giải ở lúc đọc, không bao giờ được ghi.** Một hàng trước P3-11 lưu NULL và đọc ra `legacy_spot` / `long`. `LEGACY_INSTRUMENT` là **nhãn cho scope vắng mặt, không phải một contract**: `Key.is_legacy` và `Key.searched_instrument` giữ cho nó không đi lại như một contract. Nó đã thoát ra một lần và khiến candidate của một campaign legacy đòi những bars không tồn tại — chỉ bị bắt bởi các lần chạy end-to-end `-m docker` trong khi cả 764 unit test vẫn xanh.
3. **`engine_seed` trộn scope vào seed.** `base ^ (sha256("instrument|direction")[:12] << 8)`. Không có nó thì BTC-long và BTC-short trong cùng một arm bốc đúng cùng một chuỗi ngẫu nhiên, và "tìm kiếm độc lập" là một cuộc tìm được báo cáo hai lần.
4. **Quota được đếm trong chính scope của nó**, và một ngân sách không chia hết cho số đơn vị thì bị từ chối chứ không lặng lẽ làm tròn. INV-61 — worker chạy song song không thể vượt quota — giữ nguyên và vẫn đúng ở khoá rộng hơn.
5. **Archive MAP-Elites và phép dedup ô của §3.2.1 được scope lại.** `build_portfolio` dedup ô trên toàn campaign, nên BTC-long và XRP-short rơi vào cùng một ô hành vi sẽ loại nhau vì những lý do chẳng liên quan gì tới cả hai.
6. **Danh mục theo slot: tối đa một member cho mỗi `(instrument, direction)`.** Một slot không có candidate nào qua gate ④ thì **để trống** chứ không nhận một người thay thế hậu kiểm — lấp nó bằng candidate tốt nhì là chọn lọc sau khi biết kết quả, đúng thứ mà các gate sinh ra để ngăn.
7. **`decide()` trở thành hiệu ghép cặp trong cùng scope.** Với mỗi `(instrument, direction, seed)`, `d = eff_gp − eff_random`, và `gp_beats_random ⟺ mean(d) > sd(d)`. Gộp cả ba mươi con số rồi lấy sd là nạp vào **phương sai giữa các instrument** — BTC và XRP qua gate ④ ở tỉ lệ khác nhau vì lý do chẳng dính gì tới engine — và độ trải nuốt trọn mọi hiệu ứng thật, biến luật thành cỗ máy luôn trả `tie`. Một `tie` đọc ra như bằng chứng của "không có khác biệt", nên như thế còn tệ hơn không có phép kiểm. Dạng gộp được giữ lại làm một test đối chứng âm.
8. **Protocol phiên bản 5**, với chuỗi `unit` rộng hơn và `scope_verdicts = "none"` hash như dữ liệu: một `(instrument, direction)` riêng lẻ là thăm dò và không mang thắng hay thua, và muốn báo cáo một cái thì phải có phiên bản protocol mới chứ không phải diễn giải lại dữ liệu vốn chưa bao giờ được thu cho việc đó.
9. **Reader của review dán nhãn chứ không để trống, và mở được cái đang có.** Một scope NULL hiển thị thành scope legacy, nên campaign v4 vẫn đọc ra như một campaign chứ không như dữ liệu thiếu; một bộ lọc scope không tìm thấy gì trong campaign legacy, vì hàng legacy không thuộc scope perpetual nào. `READABLE_SCHEMAS` là v6 **và** v7: reader bị cấm migrate, nên nó cũng không được phép đòi một migration — trên v6 hai cột vắng mặt thành NULL literal, đúng cái hình dạng mà một hàng legacy có trên v7.
10. **Quota hiển thị trên UI chia ngân sách cho số đơn vị.** Con số `2` nó từng chia cho là số arm, thứ thôi là toàn bộ câu chuyện ngay khi đơn vị được nới rộng.

## Hệ quả

- **1.325 trial đang có mang scope NULL vĩnh viễn.** Chúng đọc ra legacy và không bao giờ sửa được, đúng như P2 hứa. Năm campaign OPEN giữ nguyên ý nghĩa như bản ghi; không cái nào chạy tiếp được, vì campaign v4 bị từ chối dưới protocol v5 (INV-74).
- **`n_eff` và `trial_stats` vẫn là toàn cục.** INV-42 giữ nguyên: số hạng deflate đếm mọi trial mà chương trình nghiên cứu này từng chạy, trên mọi scope. Tách nó theo scope sẽ cho một campaign tự reset hình phạt đa kiểm định của chính nó bằng cách đổi tên instrument.
- **Ngân sách trial chia nhỏ thêm.** 600 trial trên 6 đơn vị là 100 mỗi đơn vị; trên 30 đơn vị là 20. Hai mươi trial mỗi scope là một cuộc tìm kiếm hay một lần lấy mẫu là câu hỏi còn mở cho người định hình dạng pilot — nó không được quyết ở đây.
- **Archive bị phân mảnh, nên tính mới lạ cấu trúc xuyên scope không còn nhìn thấy được.** Hai scope không bao giờ tranh một ô, điều đó đúng cho việc tìm kiếm; nó cũng có nghĩa một clause mới lạ ở BTC-long và tầm thường ở ETH-long được tính là mới lạ ở cả hai.
- **Phép so sánh giờ cần ít nhất ba seed × số scope mới có cặp.** Với một scope, dạng ghép cặp suy biến về dạng cũ; luật `complete` của protocol vẫn giữ lại quyết định cho tới khi mọi đơn vị của mọi arm tiêu hết quota.
- **`RANKING_CTX` giữ `periods_per_year = 365.0` viết cứng.** Đó là kịch bản đóng băng cho fingerprint của ranking (INV-82) và không được trôi theo knob timeframe.

## Phương án đã cân nhắc

- **Mã hoá scope bên trong `universe`.** Bác bỏ theo quyết định 1: `universe` được phân tích để chọn bars, và `direction` không có cách biểu diễn nào trong đó.
- **Backfill trial legacy bằng giá trị `legacy_spot` rõ ràng, như spec yêu cầu.** Không khả thi — trigger append-only cấm `UPDATE`, và tắt chúng đi để chạy migration là phá P2 chỉ vì hình thức. Giải ở lúc đọc cho cùng kết quả mà không ghi gì.
- **Gộp ba mươi hiệu suất rồi so hai trung bình.** Bác bỏ theo quyết định 7. Đó là dạng gốc của ADR-0027 và nó chỉ đúng khi mọi quan sát là hoán đổi được, điều thôi đúng ngay khi các scope khác nhau.
- **Cho mỗi `(instrument, direction)` mang phán quyết riêng.** Bác bỏ theo quyết định 8: ba mươi phép so sánh đồng thời với ba seed mỗi cái là một bài toán đa kiểm định mà protocol hiện không hiệu chỉnh, và báo cáo những cái thắng là best-of-N dưới một cái tên khác.
- **Bắt ledger phải migrate lên v7 trước khi giao diện review mở nó.** Bác bỏ theo quyết định 9: reader là chỉ-đọc theo hợp đồng, và một reader không thể migrate thì không được phép đòi hỏi một cái — ledger duy nhất đang tồn tại là v6.
