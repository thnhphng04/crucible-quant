# ADR-0042 — Lỗ mark ngắn được điền từ nến trade cùng phút

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-04
- **Task:** P3-24 · **Kiến trúc:** §6.1, D18 · **Thu hẹp:** INV-94 (ADR-0032) · **Vấn đề mở:** —

## Bối cảnh

Lần tải perpetual thật đầu tiên (2026-10-04) dừng ở lỗi `BTC/USDT:USDT mark: missing minute in download page`. Kho dữ liệu phút từ chối mọi lỗ (P3-17, INV-94), trong khi lịch sử **mark** 1 phút của sàn lại có lỗ. Khảo sát cửa sổ 2020-09-15 → 2026-10-02, với mọi lỗ trong file lưu trữ đều được kiểm lại qua API thật, cho thấy:

- thiếu 24 phút ngày 2020-12-17 07:32–07:55, ở cả năm contract;
- thiếu 1–7 phút ngày 2022-07-12/13 ở ETH, SOL, BNB, XRP (tối đa 11 phút mỗi contract);
- thiếu 2 phút ngày 2024-08-12 10:02 ở SOL và XRP.

Tổng cộng tối đa 35 phút mỗi contract trên khoảng 3,18 triệu phút. Phút **trade** đầy đủ ở cả năm contract, và khoảng holdout không thiếu gì. Các lỗ nguyên ngày trong file lưu trữ data.binance.vision là lỗi của file lưu trữ: API vẫn trả về những ngày đó. Nếu không có luật xử lý, không campaign perpetual nào mở được trên sàn này.

## Quyết định

1. **Phút mark bị thiếu được điền từ nến trade cùng phút:** chép open, high, low, close, còn volume đặt bằng 0. Chỉ được điền khi đoạn phút thiếu liên tiếp dài tối đa `MAX_MARK_FILL_MINUTES = 60` và kho trade có đủ mọi phút trong đoạn.
2. **Mọi trường hợp khác vẫn bị từ chối:**
   - đoạn thiếu dài hơn;
   - lỗ trong trade (phút trade không bao giờ được điền);
   - lỗ không có phút trade tương ứng;
   - trang bị trùng, lùi thời gian hoặc lệch lưới;
   - lỗ nằm sau phút cuối cùng sàn trả về.
3. **Mỗi lần điền đều được ghi lại:**
   - trong `<contract>_1m.mark.fills.json` cạnh bộ nhớ đệm phút, ghi trước trang chứa phút được điền, nên khi chạy tiếp thì lần điền được lặp lại chứ bản ghi không bị mất;
   - trong `PerpCoverage.mark_fills`;
   - trong `mark_fills.json` ở thư mục bundle IS, được `manifest.json` tính checksum. File này chỉ liệt kê phút in-sample, nên không nói gì về khoảng holdout.
4. Mark tại thời điểm tất toán funding và các đường thanh lý đọc phút đã điền như mọi phút khác.
5. **Một lần tất toán thuộc về phút gần nó nhất** (`settlement_minute_ms`). Sau đó cùng lần tải lại dừng ở lỗi `no completed one-minute mark before funding settlement 2020-09-15 08:00`: Binance đóng dấu thời điểm tất toán lệch mốc vài mili-giây. Replay và kernel vốn đã xếp một lần tất toán bằng `round((ts - open) / 1 phút)`. Phép kiểm độ phủ funding, việc tra nến và giá mark, các điểm cắt đường giá (trước đây âm thầm bỏ qua mọi stamp lệch phút) và preflight giờ dùng cùng quy tắc đó. Dòng funding vẫn giữ nguyên stamp gốc của sàn.

## Hệ quả

- Lần tải thật chạy hết được. Trên cửa sổ đã đo, việc xét thanh lý dùng giá trade tối đa 35 phút mỗi contract trong năm năm.
- INV-94, sau khi thu hẹp, vẫn đứng: thiếu cả một *chuỗi* mark hay funding thì vẫn từ chối, và không có gì được điền bằng 0. Ngoại lệ này có bất biến riêng là INV-116; cách xếp lần tất toán là INV-117.
- Một lỗ mark mà API hôm nay chưa cho thấy có thể xuất hiện sau này. Lỗ đó hoặc được điền theo cùng giới hạn và được ghi lại, hoặc bị từ chối.
- Không cần sửa kiến trúc. §6.1 yêu cầu có mark và funding nhưng không quy định cách xử lý lỗ.

## Các phương án đã cân nhắc

- **Kéo giá mark gần nhất về sau (forward-fill).** Bác bỏ: giá có thể cũ tới 24 phút.
- **Đánh dấu các nến bị ảnh hưởng là không xác định** (không vào lệnh; lệnh đang mở lấy kết quả xấu nhất). Bác bỏ: phải đổi replay ở cả CPU lẫn GPU chỉ vì khoảng 35 phút dữ liệu.
- **Cho in-sample bắt đầu sau lỗ cuối cùng (2024-08-12).** Bác bỏ: chỉ còn khoảng 1,1 năm in-sample, MinBTL không còn chỗ.
