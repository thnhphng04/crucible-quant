# ADR-0034 — Gói dữ liệu perpetual và payload sandbox

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-26
- **Task:** P3-18 · **Kiến trúc:** §3.3.3, §3.5, §6.1 · **Thêm:** INV-99

## Bối cảnh

Mọi module đã dựng cho giao dịch perpetual ở P3-05 … P3-13 — bracket margin, tài khoản ví isolated, path summary trong nến, admission, joint replay — đều không được gì ngoài cụm của chính nó import tới. Nguyên nhân là đúng một dòng: `SandboxJob.bars` được ghi rõ là *"the only data the container will see"*, và nó chỉ mang nến giao dịch. Một backtest bên trong container không nhận được mark price, funding rate hay bảng bracket, nên không gate nào chạy được cơ chế perpetual, nên không gì gọi tới bất kỳ phần nào trong đó.

Nới kênh đó là thay đổi mở khoá cho P3-19 tới P3-23. Nó cũng là chỗ duy nhất mà một sai sót trở nên vô hình: một chuỗi thiếu thì raise, nhưng một chuỗi **lệch pha** thì không. Một chuỗi mark bị cắt lệch một nến so với chuỗi giao dịch sẽ mark mọi vị thế theo giá của nến bên cạnh suốt phần còn lại của lần chạy, và không có lỗi nào được sinh ra ở bất kỳ đâu.

## Quyết định

1. **Bốn chuỗi đi chung như một đối tượng.** `core.perp_inputs.PerpBundle` giữ nến mark, bảng funding, một path trong nến cho mỗi nến, và bảng bracket, cho một contract. Có đúng một phép cắt cửa sổ, `PerpBundle.slice(start, stop)`, và nó dịch cả bốn. Không có API nào cắt riêng một chuỗi.
2. **Một bộ quy ước, phát biểu một lần.** Timestamp là nanosecond, UTC, nhãn là giờ **đóng** nến, như `Bars`. Cửa sổ nửa mở `[start, stop)`, như `Bars.slice`. `brackets` là hằng số của campaign chứ không phải một cửa sổ, nên phép cắt mang nó qua nguyên vẹn.
3. **`bar_ix` được đánh lại sau mỗi lần cắt.** Bảng funding đánh chỉ số vào chính gói nó thuộc về, nên một gói đã cắt không đọc được bằng chỉ số của gói chưa cắt. Phương án thay thế — một timestamp thô để người gọi tự giải — bị bác vì làm sai nó sẽ tính funding vào một nến lệch đúng bằng offset của phép cắt, thứ làm đổi kết quả mà không sinh lỗi nào.
4. **Nhiều sự kiện funding trong một nến giữ nguyên thành các dòng riêng.** Chu kỳ thanh toán có thể đổi theo thời gian hoặc theo contract. Cộng các sự kiện thành một rate cho mỗi nến sẽ che mất phần dịch giá thanh lý mà mỗi lần settlement gây ra giữa nến, đúng là thứ mà ADR-0032 quyết định 6b đã cắt path summary thành các đoạn để bắt lấy.
   Sự kiện funding lúc 08:00 dùng giá mark **đóng của phút 07:59**, là phút hoàn tất gần nhất tại thời điểm thanh toán. Giá đóng phút 08:00 là thông tin tương lai. Sự kiện đúng mép bắt đầu cửa sổ bị loại vì không có phút trước đó trong cửa sổ; không vị thế nào có thể đã mở trong cửa sổ trước sự kiện ấy.
5. **Tính khớp được kiểm hai lần.** `assert_aligned` chạy ở host trong `prepare`, nơi traceback đọc được, và `load_inputs` kiểm lại bên trong container, vì container mới là thứ không được phép chạy tiếp trên dữ liệu lệch pha.
6. **Gói là bắt buộc với `backtest` và `grid_backtest`, và thiếu nó thì fail closed**, kèm thông báo nêu tên những contract đang thiếu. `signals` và `leak_check` chỉ đọc hướng và khoảng stop, không bao giờ định giá một vị thế, nên đòi hỏi nó ở đó sẽ dàn hàng megabyte cho những job không bao giờ nhìn tới.
7. **Yêu cầu này đọc từ chính ký hiệu, không từ một cờ cấu hình.** Ký hiệu có `:` là perpetual theo ccxt và cần một gói; `BTC/USDT` là spot và không cần. Nên luật không thể bị tắt bởi một lock quên khai thị trường, và đường spot legacy — vốn không có mark price và không có funding để mô hình — chạy nguyên như cũ.
8. **Cả bốn chuỗi ra file; `job.json` chỉ ghi tên chúng.** Kế hoạch ban đầu đặt bracket vào `job.json` như một phép tối ưu. Một cơ chế dễ kiểm chứng hơn một phép chia parquet-và-JSON, và bracket chỉ vài dòng nên phần tiết kiệm không đáng một đường code thứ hai. `options` vốn được JSON-serialize, nên numpy dù sao cũng phải chuyển bằng tay ở đó.
9. **Bảng path là dạng dài, không lồng nhau** — một dòng cho mỗi breakpoint, `(bar_ix, segment_ix, start_minute, side, minute, price)` — vì đó là thứ parquet lưu tốt và dựng lại được mà không cần codec riêng. `side` là 0 cho running minimum và 1 cho running maximum.
10. **Nến mark không bao giờ đi qua `job.bars`.** `job.bars` chảy thẳng vào `build_engine`, nên một chuỗi mark đặt ở đó sẽ được thêm vào venue thành một **instrument giao dịch được**. Chỉ sidecar.

## Hệ quả

- **INV-99: một chuỗi phút thô không bao giờ vào container.** Summary tồn tại để làm việc đó, và một test khẳng định điều đó trên các file đã dàn hàng chứ không tin vào bên ghi.
- **`source_hash` phủ toàn bộ `src/`, nên thay đổi này rebuild image sandbox một lần.** Lần chạy gate đầu tiên sau đó trả giá một `docker build` im lặng vài phút, nên đáng pre-build trong một bước smoke thay vì phát hiện bên trong một job có tính giờ.
- **`load_inputs` giờ là tuple bốn phần và `Job` nhận gói qua `job["perp_inputs"]`.** Đó là hình dạng công khai của runner, và việc nới nó chạm cả bốn hàm job.
- **Payload lớn thêm phần nến mark, bảng funding và bảng path.** Đo trên path phút mô phỏng là ~1,6 MB mỗi instrument-chuỗi trên 1.840 nến; phải đo lại trên dữ liệu Binance thật ở P3-19, vì microstructure thật sinh nhiều breakpoint hơn random walk và một ngày xu hướng mạnh đẩy một phía lên 200–400.
- **`prepare` vẫn ép đúng một timeframe** cho các nến của một job. Sidecar không phải một timeframe thứ hai nên ràng buộc đó không bị ảnh hưởng.
- **Chưa có gì gọi tới phần này.** P3-19 và P3-21 mới là những task làm cho các gate dùng nó; ADR này ghi lại hợp đồng mà chúng sẽ được dựng theo.

## Phương án đã cân nhắc

- **Truyền nến mark qua `job.bars` với một quy ước đặt tên.** Bác bỏ theo quyết định 10: venue sẽ giao dịch chúng.
- **Đẩy minute close thay cho summary** (~21 MB mỗi instrument, ~15 MB sau nén, so với trần 2 GB của container). Bác bỏ: dung lượng thì chịu được và chi phí CPU nhỏ hơn giả định ban đầu, nhưng summary là thứ làm luật ambiguity trở nên chính xác, và là thứ giữ chi phí tải cùng lưu trữ ở host trong giới hạn trên toàn universe × hai chuỗi × mọi campaign.
- **Giữ các chuỗi thành những tham số riêng của `SandboxJob`.** Bác bỏ theo quyết định 1: bốn tham số cắt được độc lập chính là hình dạng mà một phép cắt lệch pha biểu diễn được.
- **Giải funding theo timestamp trong container thay vì theo `bar_ix`.** Bác bỏ theo quyết định 3. Nó đẩy một quyết định về biên vào mọi chỗ gọi, và mỗi chỗ lại phải tự dẫn xuất lại cùng một luật nửa mở.
