# ADR-0028 — Giao thức so sánh v3: ngân sách cố định, monitor chỉ cảnh báo, hash phủ được nó

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-24
- **Task:** P2-15 · **Kiến trúc:** §3.1.11 (so sánh, quy tắc quyết định), §3.2 (đường IS→OOS) · **Thay thế:** vai trò của monitor trong [ADR-0027](0027-giao-thuc-so-sanh-giai-doan-2.md) v2

## Bối cảnh

Lượt chạy hoàn chỉnh đầu tiên dưới giao thức v2 (campaign `c-20260923-090957`, 552 trial) kết thúc ở `stopped_early`: monitor dừng `random-s0` và `random-s1` ở 76 trên 100 trial, nên báo cáo giữ lại quyết định về engine. Ba điều rút ra từ đó.

**Quy tắc dừng quá mỏng.** `is_oos_diverging` cần ba checkpoint rồi đọc dấu của hai độ dốc OLS. Nó không hỏi các checkpoint có cùng người giữ kỷ lục không, mức giảm OOS có đáng kể không, hay tín hiệu có bền không. Ở cả hai arm bị dừng, hai trong ba checkpoint là bản sao giống hệt từng byte và độ dốc đến từ **một** lần đổi kỷ lục:

```
random-s0   26 trial: IS 1.249 → OOS 1.214
            51 trial: IS 1.492 → OOS 1.044     kỷ lục đổi một lần
            76 trial: IS 1.492 → OOS 1.044     lặp lại điểm trước
```

Đường kỷ lục IS đơn điệu không giảm theo định nghĩa, nên với một arm hiếm khi đổi kỷ lục — tìm kiếm ngẫu nhiên — mỗi lần đổi là một giá trị ngoại lai IS cực đoan hơn và OOS của nó được kỳ vọng kém hơn do hồi quy về trung bình. Dữ liệu không tách được điều đó khỏi p-hacking.

**Dừng dựa trên kết quả quan sát khiến phép so sánh thành nội sinh.** §3.1.11 so sánh các arm *ở cùng ngân sách trial*. Một lệnh dừng kích hoạt bởi chỉ số OOS khiến thời điểm dừng của mỗi arm thành hàm của chính đại lượng đang đo, và cắt cụt đúng những arm trông kém hơn. Dù quy tắc có giá trị thế nào bên trong một lượt tìm kiếm thông thường, nó không thuộc về thí nghiệm đo chính lượt tìm kiếm đó.

**Hash đã khóa không phủ được monitor.** `protocol_hash` băm dictionary `PROTOCOL`, trong đó `is_oos_diverging` chỉ xuất hiện dưới dạng chuỗi ký tự ở `supporting`. Đổi `FLAT_SLOPE`, `min_points` hay viết lại cả hàm đều không làm hash nhúc nhích, nên một campaign vẫn qua được `assert_protocol_is_the_locked_one` dưới một monitor hành xử khác hẳn. Đây đúng là lỗ hổng mà ADR-0027 v2 đã bịt cho quy tắc quyết định, còn để ngỏ cho monitor.

## Quyết định

1. **Campaign so sánh chạy hết ngân sách của nó.** Dưới giao thức v3, monitor không bao giờ dừng một arm: mọi (engine, seed) dùng hết hạn mức, và các arm được so ở ngân sách bằng nhau như §3.1.11 yêu cầu.
2. **Monitor cảnh báo và cảnh báo được ghi lại.** Khi phân kỳ, monitor ghi một sự kiện audit `DEGRADATION_WARNING` cho mỗi (engine, seed) rồi chạy tiếp. Báo cáo liệt kê cảnh báo ở `divergence_warnings` và theo từng seed ở `diverging`, nhưng chúng không tham gia quyết định — chúng được đánh giá sau lượt chạy, theo một quy tắc còn chưa tồn tại.
3. **Giao thức mang theo monitor.** `PROTOCOL["monitor"]` ghi chế độ, tín hiệu và các tham số của nó, còn `protocol_hash` gộp thêm một **dấu vân tay hành vi**: phán quyết của `is_oos_diverging` trên một bộ hình dạng checkpoint cố định (`MONITOR_SCENARIOS`), đem băm. Một thay đổi làm lật bất kỳ phán quyết nào trong đó sẽ đổi hash giao thức và do đó cần campaign mới; đổi tên, chạy formatter hay sửa docstring thì không.
4. **`stopped_early` bị loại bỏ.** Dưới v3 một arm không thể bị dừng, nên kết cục đó không thể phát sinh. Lượt chạy bị ngắt vẫn là `incomplete`.

## Hệ quả

- Campaign `c-20260923-090957` giữ nguyên như nó là: **không có quyết định về engine**. 552 trial của nó vẫn nằm trong `N`. Hiệu suất trial của C-gp (48,3 so với 25,8 trên 100 trial) và độ phủ (45/34/32 so với 15/24/26 ô) được ghi nhận là gợi ý, không phải phán quyết.
- v3 đổi hash, nên phép so sánh khởi động lại trong một campaign mới — thêm khoảng 600 trial vào `N`. Đây là lần khởi động lại thứ hai; cả hai đều đổi lấy các lỗi được tìm ra trước khi đọc bất kỳ kết quả nào, đúng thứ tự mà thiết kế mong muốn.
- Dấu vân tay chỉ phát hiện được thay đổi làm lật một phán quyết trên bộ kịch bản của nó. Vì vậy bộ này trải quanh ranh giới quyết định — đường phẳng, một lần chuyển tiếp, kỷ lục lặp lại, IS lên với OOS lên, IS lên với OOS xuống, và chuỗi ngắn hơn `min_points` — và sẽ lớn thêm mỗi khi tìm ra một ca biên mới.
- Phân kỳ không còn là cái phanh trong một lượt chạy so sánh. Một arm p-hacking thật sự sẽ tiêu hết hạn mức của nó; ở mức 100 trial mỗi arm, đó là cái giá của một thời điểm dừng không thiên lệch.

## Phương án đã cân nhắc

- **Siết `is_oos_diverging` rồi vẫn cho dừng** (đòi ≥ 3 giá trị kỷ lục IS khác nhau, một mức giảm OOS tối thiểu, tính bền qua các checkpoint): bị từ chối *cho campaign so sánh*, vì không ngưỡng nào khử được tính nội sinh — thời điểm dừng vẫn phụ thuộc vào kết quả đang đo. Giữ lại làm việc tương lai cho tìm kiếm thông thường, nơi tiết kiệm trial mới là mục đích; ba kỷ lục cũng chỉ cho hai lần chuyển tiếp, nên quy tắc cần thêm điều kiện về độ lớn và độ bền trước khi được phép dừng bất cứ thứ gì.
- **Băm mã nguồn của `is_oos_diverging`:** bị từ chối — nó vỡ khi chạy formatter, và bỏ sót các tham số nằm ngoài hàm.
- **Bỏ hẳn monitor khỏi campaign so sánh:** bị từ chối — đường cong là một trong năm chỉ số của §3.1.11 và ghi lại nó không tốn gì; chỉ quyền dừng của nó bị lấy đi.

## Bổ sung — ngưỡng mới là cái khóa, vân tay chỉ là kiểm tra phụ (2026-09-24)

Đợt review bản hiện thực đầu tiên phát hiện cái khóa bị đặt ngược chiều. `monitor_fingerprint` chỉ băm phán quyết của tám kịch bản cố định, nên một ngưỡng dời vào *khoảng giữa* các độ dốc của tám kịch bản đó làm đổi hành vi thật mà hash đứng yên: với `flat_slope` bằng 1e-5 thay vì 1e-9, một arm trôi chậm thôi không còn bị gọi là phân kỳ, nhưng không phán quyết nào trong tám cái kia lật. Hệ quả đã nêu ở mục **Hệ quả** phía trên — "vân tay chỉ phát hiện được thay đổi làm lật một phán quyết trên bộ kịch bản của nó" — vì thế không phải một ghi chú bên lề mà là một lỗ hổng, bởi vân tay đang gánh toàn bộ vai trò khóa.

`DivergenceRule` (`validation/cpcv.py`) giờ giữ `version`, `min_points` và `flat_slope` dưới dạng dữ liệu. `protocol()` ghi rule đó vào `monitor.rule` nên các ngưỡng được băm trực tiếp; `divergence_rule()` đọc nó trở lại, và monitor, `stopped_keys` cùng `engine_report` đều chạy bằng rule **mà campaign đã khóa** thay vì mặc định của module. `version` phủ trường hợp viết lại phần số học, thứ mà không ngưỡng nào mô tả nổi. Vân tay ở lại làm kiểm tra phụ đúng cho trường hợp đó, và được ghi rõ là không bao giờ đóng vai khóa.

Cùng đợt review đó phát hiện `divergence_warnings` được dựng từ cờ `diverging` hiện thời của từng seed thay vì từ ledger. Một arm phân kỳ ở checkpoint thứ ba rồi phục hồi ở checkpoint thứ tư vẫn giữ sự kiện `DEGRADATION_WARNING` nhưng biến mất khỏi báo cáo — đúng chỗ duy nhất mà cảnh báo lẽ ra phải sống sót, vì dưới v3 cảnh báo chính là toàn bộ đầu ra của monitor. Báo cáo giờ liệt kê `warned_keys()` và giữ `diverging` bên cạnh như trạng thái của đường cong ở thời điểm hiện tại.
