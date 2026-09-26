# ADR-0035 — Tài khoản chung sizing, từ tín hiệu

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-26
- **Task:** P3-20 · **Kiến trúc:** §3.2.1, §3.4 · **Thay thế:** `replay_account` theo fill của P3-08 · **Thêm:** INV-101

## Bối cảnh

P3-08 đã dựng một joint-account replay lấy **fill** của từng member trong danh mục — do chính backtest độc lập của nó sinh ra — rồi đi qua một `PerpAccount`. Được review trước khi nó được nối vào đâu, thiết kế đó **không giữ được P4′**.

Quantity trong một fill đã được `RiskSizer` tính theo equity của chính lần chạy sinh ra nó. Replay nó trên một tài khoản chung nghĩa là cùng quantity ấy trên một số dư khác: một lệnh sized từ 100.000 replay trên tài khoản đang giữ 80.000 mang rủi ro **1,25%**, không phải 1%. Đó là vi phạm ngay tại chỗ P4′ được định nghĩa, và nó im lặng — mọi con số trông vẫn như những con số. `SlotFill` còn không mang `stop_distance`, nên không thể dẫn xuất lại rủi ro để chỉnh.

Cùng một khuyết điểm có mặt thứ hai. `BridgeStrategy._equity()` đọc tài khoản của venue, nơi margin bằng 0 theo thiết kế (ADR-0032) và không bao giờ thấy một lần trả funding, nên một backtest đơn chiến thuật vốn đã sizing theo một equity mà tài khoản host không có.

## Quyết định

1. **Tài khoản là bên sizing, ở cả hai tầng.** `RiskSizer.target` vốn đã nhận equity làm tham số; thứ thay đổi là con số đó đến từ đâu. Với một chiến thuật (gate ③/④) nó là equity của `PerpAccount` host, đã settle trước khi bar được sizing (P3-19). Với một danh mục (gate ⑤) nó là equity của tài khoản chung, chụp một lần mỗi bar.
2. **Một danh mục replay tín hiệu, không phải fill.** `SlotPlan` mang **luồng tín hiệu** của một slot — hướng và khoảng stop theo từng bar, đúng thứ mà job `signals` trong sandbox vốn đã sinh ra — và `replay_signals` tự làm sizing, admission, margin và các lần thoát. Bản `replay_account` theo fill giữ lại làm công cụ đối chiếu trong test; không gì trong pipeline gọi nó.
3. **`admit_batch` được gọi thật, trên một snapshot mỗi bar.** Thứ tự chuẩn tắc — instrument tăng dần, long trước short — với trần danh mục 10% tính theo phần cam kết tại stop, đóng băng từ lúc vào lệnh. Giới hạn ≤10 vị thế là số học chứ không phải một luật riêng: mười slot mỗi cái rủi ro đúng 1% thì cạn một trần 10% (ADR-0031 quyết định 5).
4. **Mọi lệnh khớp ở open của nến sau** (ADR-0003), cả vào lẫn ra, với stop neo vào **giá đóng của nến ra tín hiệu** và cố định suốt đời vị thế (P3-19). Một lần thoát định giá tại chính giá đóng của nến ra quyết định sẽ dùng thông tin mà lệnh không thể hành động theo; đó là một lỗi thật trong bản cài đặt đầu tiên, bị test định giá bằng tay bắt được.
5. **Một stop khớp tại trigger của nó, hoặc tệ hơn nếu nến gap qua.** Không bao giờ tốt hơn — như thế là một quyền chọn miễn phí. Nến mở ở 90 với trigger 95 thì khớp ở 90, và test ghim cả hai đường vào khoản lỗ 2.000 tương ứng thay vì 1.000 mà riêng trigger gợi ý.
6. **Đóng góp là chuỗi thời gian, và phép phân rã chính xác theo cấu trúc.** Mọi USDT vào hay ra khỏi số dư chung được quy về đúng một slot, và đóng góp của một slot là tổng đã ghi sổ cộng giá trị hiện tại của ví đang mở. Nên tổng chúng bằng equity tài khoản trừ số dư ban đầu **ở mọi bar**, kể cả khi còn vị thế mở — đúng ca quan trọng, và là ca mà một đóng góp dạng số vô hướng không diễn tả được.
7. **Một lần vượt drawdown được ghi lại và không gì hành động theo nó.** `kill_switch_drawdown` là Group A theo §10.1 — nó *"only matters live"* — nên một replay nghiên cứu mà đóng hết vị thế vì nó sẽ phát minh ra một luật không có bản ghi quyết định nào phủ, và sẽ đổi việc chiến thuật nào qua được. `SignalReplay.drawdown_breach_at` báo cáo nó thay vì hành động. Điều này **đính chính kế hoạch P3-20**, nơi tôi đã liệt "kill switch được gọi" thành một tiêu chí nghiệm thu.
8. **Các instrument trong một replay phải chung một trục timestamp, và một tập không chung thì bị từ chối.** Không hợp trục: một slot có nến lệch sẽ bị giải funding và liquidation theo nến của instrument khác, đúng phần lệch pha mà ADR-0034 sinh ra để ngăn.
9. **Bản cài đặt lại được ghim vào venue.** Với một member và vốn dư dả, `replay_signals` phải tái tạo đường equity đơn chiến thuật do Nautilus điều khiển. Nó làm được, ở `rtol=1e-9`, cho cả một vị thế chạy tới cuối và một vị thế gap qua stop. Test còn khẳng định đường không phẳng, vì hai đường hằng sẽ khớp nhau mà không vì lý do gì.

## Hệ quả

- **Đây là một engine thực thi thứ hai, và đó là cái giá của quyết định này.** `replay_signals` cài lại quy ước next-open, mô hình chi phí và cách giải stop bảo vệ. Quyết định 9 là thứ duy nhất giữ nó trung thực; nếu phép ghim đó lỏng ra, tầng danh mục và tầng candidate đang đo hai thứ khác nhau và phép so giữa chúng vô nghĩa.
- **`Member.weight` mất nghĩa trên đường perpetual.** Dưới `Q = R/d` mỗi slot rủi ro đúng 1% và trần chặn tổng, nên chẳng còn gì cho naive risk parity cân. Bước 4–5 của `validation.portfolio` được thay bằng replay ở P3-21; đường spot giữ `combine`.
- **Gate ③ phải lưu luồng tín hiệu của mỗi trial**, ở `results/signals/{campaign}/{candidate}.parquet`, cạnh phần returns nó đã ghi. Không cần migration ledger: nó theo đúng quy ước artifact đang có.
- **`PROTOCOL["replay"]` đã đổi**, nên hash của protocol campaign dịch. Chưa campaign nào mở dưới v5 nên việc này miễn phí; sau khi có một cái thì nó cần bump phiên bản.
- **Bản replay theo fill giờ là code chết có test sống.** Giữ lại có ý thức — nó là một cài đặt độc lập của cùng phần số học nên hữu ích để đối chiếu — nhưng nó không được có caller.

## Phương án đã cân nhắc

- **Dẫn xuất lại rủi ro của từng fill từ đường equity của member rồi co giãn.** Bác bỏ: hệ số co giãn phụ thuộc đường đi của tài khoản chung, mà đường đó lại phụ thuộc phép co giãn. Nó vòng tròn, và một phép lặp điểm bất động trên năm năm nến thì vừa chậm vừa không kiểm chứng được.
- **Thêm `stop_distance` vào `SlotFill` rồi sizing lại tại chỗ.** Gần hơn, nhưng nó vẫn lấy *quyết định vào lệnh* từ một lần chạy đã thấy một tài khoản khác: một vị thế mà lần chạy độc lập chịu được có thể là vị thế mà tài khoản chung phải từ chối, và một fill thì không thể bị từ chối sau khi đã xảy ra.
- **Cho tầng danh mục cũng chạy Nautilus, với mọi member trong một engine.** Hấp dẫn cho P5, và bị bác bỏ vì đúng lý do của ADR-0032 quyết định 1: `MarginAccount` đánh key margin theo `InstrumentId`, nên nó không giữ được hai ví isolated của một contract, mà đó chính là hedge mode.
- **Đóng hết vị thế khi vượt drawdown.** Bác bỏ theo quyết định 7.
