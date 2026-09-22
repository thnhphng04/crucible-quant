# ADR-0021 — Đề xuất: đánh giá lại một biến thể danh mục cũ trên ledger hiện tại

- **Trạng thái:** Đề xuất — bản sửa 2, để review, **chưa triển khai**
- **Ngày:** 2026-09-22
- **Task:** sau lần chạy dữ liệu thật đầu tiên · **Kiến trúc:** §3.2.1, §4.1, §4.2 bước 2 · **Vấn đề mở:** —

## Bối cảnh

Trong campaign `c-20260921-171247`, biến thể đầu tiên (`778e…`, chỉ có RSI momentum) đã qua ⑤ và ⑥′. Sau đó calibration thêm 48 trial; RSI đã calibrate trượt ④ (PBO 0,549 trên 197 cấu hình), nên theo "lần đo mới nhất quyết định, không quay về" (sửa đổi ADR-0013) RSI rời danh mục, và biến thể dựng lại (chỉ có SMA) trượt ⑥′. Kết quả của `778e…` đã lỗi thời (INV-52) và không gì chạy lại được ⑤ → ⑥′ trên một biến thể mà quy tắc không còn dựng ra. Người dùng giữ campaign OPEN; bản sửa 1 của đề xuất bị trả lại ở bốn điểm: quyền dùng lại thành viên, ý nghĩa của khoản cộng `N`, thứ tự ghi nhận, và chạy lại.

## Quyết định (đề xuất)

1. **Phạm vi:** `portfolio-reevaluate HASH` chạy lại ⑤ → ⑥′ trên một biến thể đã ghi trong campaign OPEN; nó không đổi thành viên, trọng số hay quy tắc nào. Đóng băng giữ nguyên (chỉ kết quả trên snapshot hiện tại mới có giá trị).
2. **Quyền dùng lại thành viên — ngoại lệ duy nhất của "lần đo mới nhất quyết định":** mỗi trial thành viên `t` chỉ được dùng lại nếu (a) chính `t` đã qua ④ (`gate_results.trial_id = t`) và (b) ④ được **chạy lại ngay lúc đó** cho đúng bộ tham số của `t` trên tập cấu hình hiện tại — các biến thể ledger cùng `strategy_hash`, gồm mọi trial calibration về sau — và qua, được ghi với `trial_id = t`. Lần pass ④ cũ không còn là bằng chứng hiện hành: tập cấu hình dùng để tính nó đã lớn thêm, và ④ của bước xác nhận trên cùng code đã trượt. Phép kiểm lại này thêm một lưới PBO, không thêm trial (ADR-0011).
3. **Khoản cộng `N` là gì:** một **mức phạt lựa chọn theo chính sách** `P`, một đơn vị cho mỗi biến thể khác nhau được đánh giá lại, giữ thành một số hạng riêng: ⑤ và ⑥′ dùng `N_eff + biến thể + P` (và `N_raw + biến thể + P`), báo cáo riêng. Nó không phải trial và không nằm trong V[SR]. `N` của DSR là số thử nghiệm độc lập đứng sau một lần chọn (Bailey & López de Prado, 2014); chạy lại cùng code, dữ liệu và tham số không phải một thử nghiệm độc lập, nên `P` không suy ra từ lý thuyết — nó là một cái giá có chủ ý, thận trọng, cho việc chọn giữa các biến thể sau khi đã thấy kết quả.
4. **Thứ tự:** kiểm quyền dùng lại (2) → ghi mức phạt (dòng `selection_penalties` + sự kiện audit `VARIANT_REEVALUATION_STARTED`) → chụp **một** snapshot ledger có tính `P` → chạy ⑤ rồi ⑥′ trên đúng snapshot đó, cả hai kết quả mang nó. Một mức phạt cộng sau khi chạy sẽ làm kết quả vừa tạo lỗi thời ngay.
5. **Chạy lại kỹ thuật so với một lựa chọn mới — một quy tắc:** đánh giá lại biến thể `V` lần nữa là **chạy lại kỹ thuật** (không phạt thêm, dùng lại bản ghi khởi động cũ) chỉ khi lần đánh giá lại trước của nó chưa ghi kết quả ⑤ nào; nếu đã có kết quả ⑤ (pass hay fail), `V` bị từ chối. Một biến thể **khác** là một lựa chọn mới: **mỗi campaign chỉ được đánh giá lại tối đa một biến thể** — biến thể thứ hai, khác, bị từ chối.
6. **Test phải có khi triển khai:** mức phạt được ghi trước ⑤ và nằm trong snapshot của nó; hai gate báo cùng một snapshot; chạy lại sau khi lỗi trước ⑤ không thêm phạt; chạy lại một biến thể đã có kết quả ⑤ bị từ chối; biến thể khác thứ hai bị từ chối; một thành viên có ④ mới trượt chặn việc đánh giá lại; đóng băng chỉ nhận kết quả trên snapshot có tính `P`.

## Hệ quả

- Với ledger hôm nay, RSI `778e…` có DSR 0,999 tại N_eff = 4 và 0,986 tại N_raw = 54 (chỉ đọc, trước mọi `P` và trước ④ mới); tham số của RSI có qua ④ trên tập cấu hình đã lớn thêm hay không thì chưa biết cho tới khi chạy phép kiểm lại đó.
- Nó mở lại, theo cách hẹp và có trả giá, lựa chọn mà reviewer đã đóng ở lỗi 2; giới hạn một biến thể giữ cho nó không thành một cuộc tìm kiếm trên các biến thể cũ.

## Phương án đã cân nhắc

- **Giữ quy tắc hiện tại (đang áp dụng):** campaign vẫn OPEN; sự đa dạng phải đến từ chiến lược mới, ít tương quan hơn.
- **Đánh giá lại không phạt, hoặc dùng lại lần pass ④ cũ:** loại — một lần chọn miễn phí sau khi đã thấy kết quả, trên bằng chứng lỗi thời.
