# ADR-0036 — SL/TP cố định và thoát theo thời gian

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-27
- **Task:** P3-25

## Bối cảnh

Bộ sinh hiện phát flat ngoài điều kiện vào; take_profit được kiểm tra nhưng chưa thực thi. Stop perpetual của vị thế cố định đã neo tại close của nến tín hiệu, còn spot đặt lại stop mỗi nến.

## Quyết định

Lock campaign mới dùng `bracket_timeout_v1`: stop và take-profit neo tại close nến tín hiệu, đứng yên từ lúc vào và bỏ qua flat về sau. Nến vào lệnh là nến giữ thứ nhất. Sau khi nến thứ 100 đóng mà chưa thoát theo giá, lệnh market khớp ở open nến kế. Lock cũ thiếu phiên bản giữ `legacy_flat`.

Bộ sinh chọn stop ATR hoặc Bollinger(8, 2); campaign mặc định đặt khoảng TP bằng 1,1 lần khoảng stop. Điều kiện vào lệnh không đổi. Khi OHLC spot mơ hồ thì chọn stop. Perpetual dùng đường trade phút cho SL/TP, đường mark phút cho liquidation và chọn kết quả xấu hơn khi hòa.

Funding được tính tại từng mốc cắt chỉ khi ví còn mở, đúng một lần cho mỗi bên của hedge; settlement đến sau lúc SL/TP đóng lệnh không bị tính. Thiếu hoặc lệch trade path thì từ chối trước khi định giá.

Với lịch sử intraday, cổng ④ chia lưới cấu hình đã đăng ký thành các job sandbox đủ nhỏ để nằm dưới giới hạn báo cáo, sau đó ghép các hàng lợi nhuận theo thứ tự gốc và yêu cầu trục thời gian giống nhau. Không cấu hình nào thành trial riêng hoặc bị bỏ đi.

## Hệ quả

Mọi job định giá và replay danh mục phải nhận phiên bản từ lock campaign. Đường trade phút là dữ liệu đi kèm bắt buộc với đường mark cho campaign perpetual mới. Kết quả campaign cũ giữ ngữ nghĩa gốc.
