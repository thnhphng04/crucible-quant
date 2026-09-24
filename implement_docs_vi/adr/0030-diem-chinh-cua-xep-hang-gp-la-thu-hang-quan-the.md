# ADR-0030 — Số hạng chính của điểm xếp hạng GP là thứ hạng trong quần thể

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-24
- **Task:** P2-16 · **Kiến trúc:** §3.1.6 #1, §3.1.11 · **Sửa đổi:** [ADR-0026](0026-toan-tu-gp-va-diem-xep-hang.md)

## Bối cảnh

Lượt so sánh hoàn chỉnh đầu tiên (campaign `c-20260923-090957`, giao thức v2) cho một kết quả đảo ngược. C-gp qua gate ④ nhiều hơn hẳn C-random — 72.5% so với 47.0% trong số ứng viên đã qua gate ③, 145 entry đủ điều kiện vào archive so với 65 — nhưng danh mục dựng từ ứng viên của nó chỉ đạt Sharpe 0.466 so với 1.096 của C-random, và DSR 0.0012 so với 0.0988.

Nguyên nhân nằm trong `ranking.py`. Số hạng chính của nó, DSR-rank theo ADR-0026, là PSR của ứng viên so với `deflated_benchmark(N_eff, V[SR])`. Ngưỡng đó dâng lên theo số trial: ở `N_eff = 145` và `V[SR] = 0.3355` nó đạt **1.540 Sharpe năm hóa**, cao hơn phân vị 90 của quần thể (1.22) và cao hơn nhiều so với trung vị 0.71. PSR do đó bị nén — trung vị **0.0187**, so với số hạng plateau 0.0486, số hạng SPP 0.0321 và phạt trùng lặp lên tới 0.10. Số hạng mang λ ngầm định 1.0 bị các số hạng mang λ = 0.05 lấn át hơn bốn lần.

C-gp đã dành hai mươi thế hệ để chọn lọc theo tham số phẳng, cấu trúc mới lạ và ít TUNABLE. Số đo xác nhận điều đó: quần thể đã chọn lọc của nó có `sharpe_is` trung bình **thấp hơn** (0.595) so với mẫu không chọn lọc của C-random (0.664). Tệ hơn, `plateau` và `SPP median` đo gần đúng thứ mà PBO của gate ④ đo, nên bộ tìm kiếm đang được huấn luyện trên chính người chấm nó.

Hai giả thuyết đã được kiểm trên ledger và bị bác bỏ. Tương quan giữa các thành viên danh mục không giải thích được khoảng cách: bể ứng viên của C-gp *ít* tương quan hơn của C-random (|ρ| trung bình 0.312 so với 0.347) và thành viên chỉ nhỉnh hơn chút (0.129 so với 0.097), cả hai đều nằm sâu trong ngưỡng `max_corr` 0.5. Chen chúc là có thật — 145 entry đủ điều kiện rút còn 108 đại diện, so với 65 còn 63 của C-random — nhưng §3.2.1 đã loại nó trước khi dựng danh mục. Thứ còn lại là chất lượng: thành viên của C-gp đơn giản là kém hơn.

## Quyết định

1. **Số hạng chính là thứ hạng của DSR-rank trong quần thể**, chuẩn hóa về [0, 1], đồng hạng chia trung bình hạng và một entry đơn lẻ nhận điểm giữa 0.5. PSR là một phép biến đổi đơn điệu của các mô-men Sharpe, nên lấy thứ hạng giữ nguyên mọi thứ tự nó mang mà bỏ đi phần bị nén.
2. **Không λ nào đổi.** Số hạng chính giờ trải 1.0 so với tổng 0.245 của các số hạng phụ, đúng mức nhấn mạnh mà ADR-0026 đặt ra. Hiệu chỉnh lại là một quyết định riêng, cần bằng chứng riêng.
3. **Thứ hạng lấy trên tập entry được đưa vào `scores()`** — một (engine, seed) — khớp phạm vi của số hạng novelty, vốn đã so xuyên island. Xếp hạng theo island sẽ khiến một island vừa gieo lại với hai entry nhận số hạng chính đúng bằng {0.0, 1.0}.
4. **Đồng hạng chia trung bình hạng, so sánh bằng chính xác.** `dsr_rank` trả về literal 0.0 cho mọi entry thiếu mô-men IS; xếp hạng thứ tự sẽ trải khối đó khắp đáy khoảng theo thứ tự trial và thưởng cho entry nào chạy trước.
5. **`term_dispersion` và INV-79** phơi ra dạng hỏng này: độ phân tán của số hạng chính phải lớn hơn tổng độ phân tán của các số hạng phụ. Đây là một chẩn đoán, báo cáo theo (engine, seed) dưới tên `ranking_margin`; nó không tới `decide` cũng không tới `EarlyStop`, vì một phép so sánh không được để một đại lượng đang đo ấn định thời điểm dừng ([ADR-0028](0028-giao-thuc-so-sanh-v3-monitor-chi-canh-bao.md)).
6. **Độ phân tán là khoảng tứ phân vị, không phải độ lệch chuẩn.** Điều này được đo, không phải giả định. Bệnh lý ở đây là phần lớn quần thể bị ép sát về 0 trong khi vài entry đầu bảng vẫn giữ PSR lớn, và sd bị chi phối đúng bởi cái đuôi đó: trên campaign thật, công thức cũ cho margin theo sd là 1.36 và 1.08 nên **đạt** một phép kiểm mà lẽ ra nó phải trượt. Cùng quần thể đó, margin theo IQR là 0.59 và 0.44, so với 4.56 và 3.21 của dạng thứ hạng.
7. **Giao thức mang theo hàm xếp hạng (INV-80), giao thức v4.** Các λ được hash như dữ liệu, đọc lúc khóa giống hệt luật phân kỳ; `ranking_fingerprint()` hash thứ tự lựa chọn và điểm số đã làm tròn trên `RANKING_SCENARIOS` như một kiểm tra thứ cấp. Hàm xếp hạng chính là thứ phân biệt C-gp với C-random, nên một thay đổi âm thầm ở đó là lỗ hổng lớn hơn lỗ hổng monitor mà ADR-0028 đã bịt.

## Hệ quả

- Campaign `c-20260923-090957` giữ nguyên vị thế theo ADR-0028: **không có phán quyết engine**, và 552 trial của nó ở lại trong `N`. Các con số trial-efficiency và coverage của nó giờ đã có nguyên nhân đã biết, nên không được đọc như bằng chứng về bản thân tiến hóa.
- Phép so sánh khởi động lại dưới giao thức v4 (`0cafe016ef5f…`, ranking fingerprint `77597182279a…`). Đây là lần khởi động lại thứ ba; như hai lần trước, nó được mua bằng một defect tìm ra trước khi đọc bất kỳ kết quả nào.
- Mọi lần hiệu chỉnh λ sau này đều buộc mở campaign mới. Điều đó là đúng, và nó cơ giới hóa quy tắc của ADR-0026 rằng λ chỉ đổi khi có lý do được ghi lại.
- Dạng thứ hạng loại bỏ ảnh hưởng của ngưỡng lên **thang đo**, không phải lên **thứ tự**. PSR vẫn chia cho √(T−1) và cho các mô-men, nên hình phạt của ADR-0026 với bản ghi ngắn, lệch và đuôi dày còn nguyên, và `RankContext` vẫn đổi ai hơn ai. Không test nào được khẳng định điểm số bất biến theo `N_eff`.
- Điểm số không còn so sánh được xuyên quần thể: 0.8 giữa 20 entry không phải 0.8 giữa 145 entry. Mọi nơi tiêu thụ hiện nay đều so trong một lần gọi; một chẩn đoán "điểm cao nhất mỗi seed" trong tương lai sẽ sai.
- `monitor.py` giờ import `ranking`. Luồng là một chiều và `load_entries` đã lọc bỏ `private` trước khi bên nào thấy entry, nên INV-68 vẫn đứng; `test_ranking_ignores_private` đã chuyển cùng các test ranking khác sang `tests/agent/evolution/test_ranking.py` và tiếp tục canh giữ nó.
- Một bộ đo hiệu chỉnh (`tests/agent/test_ranking_recovery.py`) chạy toàn bộ engine trên một thị trường mô phỏng có Sharpe tái hiện hiện tượng nén, còn các metric phụ lấy từ một hash độc lập. Nó ghi lại một phát hiện đáng giữ: phiên bản trước cài một **cây kim sạch** — một clause trả cao vượt hẳn mọi thứ khác — và đo được *không có khác biệt giữa hai dạng*. Phép nén là đơn điệu, nên phần đỉnh cực trị sống sót qua nó và cả hai dạng đều xếp cây kim lên đầu. Defect cắn ở **giữa** một quần thể gồm những ứng viên tầm thường, sát nhau. Với một edge yếu chồng lấn (lệch trung bình 0.7 Sharpe trên hai dải rộng 1.5), dạng thứ hạng cho phần đầu tốt hơn ở cả ba seed. Ai hiệu chỉnh lại λ nên bắt đầu từ đó, trong một ledger riêng.
- Kiến trúc §3.1.6 #1 vẫn ghi `Score = DSR(returns, N_eff) − λ₁·AST_sim − λ₂·n_params`. ADR-0026 đã thay thế phần `DSR(...)` theo nghĩa đen, thứ mà INV-42 cấm tính cho từng chiến lược; đây là tinh chỉnh tiếp cùng phép thay thế đó và không đổi số hạng, λ, gate hay ngưỡng nào. Nếu kiến trúc được đọc là đòi một xác suất tuyệt đối chứ không phải một thứ tự, thì thay đổi này là cấp kiến trúc và là quyết định của người dùng.

## Phương án đã cân nhắc

- **Nâng hoặc hạ các λ phụ.** Bác bỏ: bộ λ khôi phục được cân bằng phụ thuộc vào vị trí của ngưỡng, vốn là thuộc tính của chính `N_eff` và `V[SR]` của campaign đó. Giá trị chọn hôm nay sẽ lỗi thời ở campaign kế tiếp, và ADR-0026 đòi một lý do được ghi lại mà không dẫn xuất từ dữ liệu.
- **Đặt `SR₀ = 0`.** Bác bỏ: hiện tượng nén quay lại ở đầu thấp mỗi khi quần thể nằm quanh Sharpe 0, và nó vứt bỏ vốn từ vựng ngưỡng-deflate của ADR-0013 mà khâu dựng danh mục dùng chung.
- **Chuẩn hóa z cho PSR thay vì lấy thứ hạng.** Bác bỏ: không bị chặn, nên một điểm ngoại lai đơn lẻ lại nén tất cả những người còn lại — vẫn là lỗi cũ trong bộ áo mới.
- **Bỏ `plateau` và `SPP` khỏi điểm số (D20).** Không làm ở đây. Chúng đúng là trùng đo với thứ gate ④ đo, và vòng Goodhart sống sót qua việc đổi thang đo; nhưng D20 nằm trong sổ đăng ký quyết định và mở lại nó là quyết định của người dùng, không phải quyết định triển khai.
