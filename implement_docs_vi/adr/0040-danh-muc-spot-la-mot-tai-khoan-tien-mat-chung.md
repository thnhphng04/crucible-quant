# ADR-0040 — Danh mục spot là một tài khoản tiền mặt chung

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-01
- **Task:** P3-52 · **Kiến trúc:** §3.2.1, §3.4 · **Mở rộng:** ADR-0033 (slot), ADR-0035 (replay tài khoản)

## Bối cảnh

Từ ADR-0033, campaign bracket tìm riêng từng `(instrument, direction)`, và danh mục perpetual là một lần replay tài khoản trên tín hiệu của các member (ADR-0035). Nhánh spot chưa bao giờ theo. `build_and_record` vẫn chạy luật v0.3 của §3.2.1:

- mỗi ô feature-map một đại diện, mà id ô không chứa instrument, nên BTC-long và ETH-long cùng ô loại nhau (phép lọc trùng mà quyết định 5 của ADR-0033 chỉ scope lại cho perpetual);
- một bộ lọc tương quan, rồi w ∝ 1/σ, cân bằng lại hằng tháng, và tổng có trọng số của các tài khoản 100k độc lập;
- không có trần rủi ro danh mục, dù §3.4 lấy trần đó làm luật ở cấp danh mục và ADR-0031 đã bỏ chân volatility mà 1/σ đưa trở lại;
- một cửa sổ chung, nên member niêm yết muộn (SOL, 08/2020) cắt lịch sử của mọi member.

Buổi đối chiếu kiến trúc ngày 2026-10-01 phát hiện điều này. User chọn tài khoản chung và cả hai chi tiết dưới đây.

## Quyết định

1. **Một tag lock mới, `derived.portfolio_protocol: shared_account_v1`**, do CLI và Studio ghi cho mọi campaign mới. Lock không có tag giữ luật nó đã đăng ký; tag lạ bị từ chối. Campaign harness đang mở `c-20260930-170212` và hash của giao thức so sánh không đổi.
2. **Chọn member bằng `select_slots`, như perpetual:** mỗi slot một member, PSR tốt nhất so với SR₀(N_eff, V[SR]), Sharpe IS > 0, tối đa `max_strategies`. **Không lọc tương quan** (user, 2026-10-01): bộ lọc được làm để bỏ bản gần trùng của một cuộc tìm cả rổ; các cặp crypto spot tương quan ≈ 0,7–0,8, nên nó sẽ chỉ để lại một hai member, và trần 10% đã chặn tổng rủi ro. `max_corr` vẫn nằm trong lock và không được đọc trên đường này.
3. **Danh mục là `replay_spot_signals`** (`execution/spot_account.py`): một tài khoản tiền mặt, slot long, chỉ `bracket_timeout_v1`. Mỗi bước:
   1. lệnh hết hạn bán ở giá mở cửa;
   2. lệnh vào khớp ở giá mở cửa, snapshot cũ nhất trước, rồi theo thứ tự chuẩn. Mỗi lệnh được tính `Q = R/d` từ snapshot equity của bar tín hiệu, được `admit_batch` nhận khi Σ q·d ≤ 10% equity sau các lệnh thoát ở giá mở cửa, rồi làm tròn xuống theo số tiền mặt còn mua được;
   3. SL/TP xử lý trên OHLC, chạm xấu hơn trước;
   4. một snapshot equity ở giá đóng cửa.
4. **Trục thời gian là hợp các timestamp của member** (user, 2026-10-01). Slot chờ bar đầu tiên của instrument mình; instrument đang giữ mà thiếu bar thì định giá ở giá đóng cửa gần nhất.
5. **Một member tái tạo backtest gate ③ của chính nó khớp từng bit** (`_run_spot_bracket`, INV-112). Danh mục chỉ thêm đúng những gì việc dùng chung tài khoản thêm vào.
6. **Mọi nơi dùng danh mục đều replay cùng một tài khoản:** bước dựng (`research_run.account_portfolio`, cả danh mục theo engine của campaign harness có tag), gate ⑥′ (`robustness.account_returns`), và holdout. Holdout chọn đường theo `rule_config.weighting == "risk_per_slot"` của variant đã freeze và xin engine job `signals`, như perpetual. Đường equity tài khoản được ghi cho review và Studio.

## Hệ quả

- Danh mục spot và perpetual giờ chung một luật: slot, một snapshot, trần 10%, không trọng số. Danh mục spot không còn mất lịch sử vì member trẻ nhất.
- Lợi nhuận danh mục bắt đầu từ bar đầu tiên của member sớm nhất. Trước khi member sau niêm yết, tài khoản giữ tiền mặt cho nó, điều này không pha loãng gì: mỗi lệnh vẫn rủi ro đúng `R`.
- `max_corr` và `rebalance` là khoá chết trên đường tài khoản. Bỏ chúng khỏi Group B cần ADR riêng.
- Lock spot cũ giữ ô và 1/σ mãi mãi, đúng như P2 và việc đăng ký trước đòi hỏi.
- Sửa kiến trúc (VI trước, rồi EN): §3.2.1 mô tả cả hai luật và lock nào chọn luật nào; §3.4 thôi gọi bước danh mục là ngân sách vol; changelog ghi lại.

## Các phương án đã cân nhắc

- **Giữ tổng có trọng số và chỉ lọc trùng theo slot.** User bác bỏ: nó vẫn đánh trọng số theo 1/σ, thứ ADR-0031 đã bỏ, và vẫn không có trần danh mục.
- **Lấy giao các trục, như replay perpetual.** Bác bỏ: giao cắt BTC, ETH và BNB về ngày niêm yết của SOL. Perpetual cần một trục vì funding và thanh lý được xử lý theo từng bar; spot không có cả hai.
- **Giữ bộ lọc tương quan trên slot.** Bác bỏ, xem quyết định 2.
- **Đổi luật cho mọi lock.** Bác bỏ: dựng lại danh mục của một campaign đang mở theo luật nó chưa đăng ký là chọn lọc sau khi biết kết quả.
