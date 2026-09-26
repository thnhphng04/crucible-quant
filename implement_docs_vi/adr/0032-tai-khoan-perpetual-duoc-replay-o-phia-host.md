# ADR-0032 — Tài khoản perpetual được replay ở phía host

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-25
- **Task:** P3-05 … P3-08 · **Kiến trúc:** §3.4, §3.5, §6.1 · **Bào mòn:** P5

## Bối cảnh

Chuyển từ spot cash sang Binance USDT-M perpetual thêm ba cơ chế mà venue trước không có: margin lấy từ bracket leverage theo bậc, funding thanh toán mỗi tám giờ, và liquidation trên mark price chứ không phải giá khớp. Hedge mode thêm cơ chế thứ tư: một contract giữ đồng thời một vị thế LONG và một vị thế SHORT, mỗi bên có isolated margin **của riêng nó** và giá liquidation của riêng nó.

Câu trả lời của §3.5 cho "mô hình hoá execution thế nào" xưa nay vẫn là NautilusTrader, dưới hard invariant P5: backtest và live chạy cùng một đường code. Nên câu hỏi đầu tiên là Nautilus có gánh được bốn thứ đó không. Mã nguồn đã cài được đọc trực tiếp thay vì tin snippet trong kiến trúc, theo `CLAUDE.md`:

- `MarginAccount._margins` là một `cdef dict` key theo riêng `InstrumentId` (`accounting/accounts/margin.pxd:34`). Một symbol không thể giữ hai ví isolated. Đây không phải lỗ hổng của API; đó là cấu trúc dữ liệu.
- `MarginModel.calculate_margin_init(instrument, quantity, price, leverage, use_quote_for_inverse)` **không có tham số side** (`accounting/margin_models.pxd:27-34`). Chỉ `calculate_margin_maint` nhận `PositionSide`. Một margin model cắm vào không nhìn thấy hướng mà nó đang định giá.
- `OmsType.HEDGING` sinh **một `PositionId` mới cho mỗi fill** (`execution/engine.pyx:1513`), chứ không phải hai slot cố định mỗi symbol như sàn.
- Backtest engine không thanh toán funding, một chút nào.

Nên "xây trên Nautilus" sẽ có nghĩa là: viết một margin model mù hướng, tự gán position ID để giả lập hai slot, thò vào số dư tài khoản từ một `SimulationModule` để tính funding, và viết liquidation từ đầu — tức là làm gần như toàn bộ ngữ nghĩa của Binance trong khi chống lại một object tài khoản có cách đánh key mâu thuẫn với chúng.

`execution/engine._summarize` vốn đã dựng lại equity ở phía host từ các fill dưới tài khoản cash. Khuôn mẫu đã có sẵn; câu hỏi chỉ là nó nới ra tới đâu.

## Quyết định

1. **Nautilus sinh lệnh và fill; tài khoản là của mình.** Bridge giữ đường signal → order → fill, gồm cả quy ước next-open của ADR-0003 và `LatencyModel(base_latency_nanos=1)`. Margin, funding và liquidation được replay ở phía host trong `execution/perp_account.py` và `execution/joint_account.py`.
2. **`OmsType.NETTING` với `AccountType.MARGIN`, không phải `HEDGING`.** Một backtest chạy một chiến thuật trên một phía của một instrument, nên engine đó không bao giờ giữ cả hai chân; sổ hai chiều thuộc về joint-account replay. `HEDGING` đã được thử trước và gây hại thật sự: một `PositionId` mới cho mỗi lệnh vào khiến stop `reduce_only` không còn gì để giảm và **lặng lẽ ngừng khớp**, chỉ bị bắt vì `test_protective_stop_fills_at_gap_open` chuyển đỏ.
3. **`margin_init` và `margin_maint` của venue bằng 0.** Nautilus không được từ chối một lệnh dựa trên những con số mình không dùng. Ràng buộc thật là bảng bracket.
4. **Bracket leverage là một snapshot khoá lại, ghi nhận như một giả định.** `GET /fapi/v1/leverageBracket` là endpoint signed và Binance không công bố lịch sử, nên một campaign lấy một snapshot, hash vào lock và áp cho toàn bộ cửa sổ in-sample. Gate ⑥′ mang thêm một nhánh độ nhạy theo bracket, cùng lối với stress `cost_multiplier` đang có.
5. **Isolated nghĩa là isolated, cưỡng chế ngay tại ví.** Lỗ của một ví dừng ở chính margin của nó: `Wallet.bounded_pnl` chặn PnL đã thực hiện ở `−margin` và `Wallet.value` đặt sàn đóng góp equity của nó ở 0. Lỗ của một ví không với tới được số dư tự do lẫn phía bên kia. Một fill *xuyên qua* giá phá sản được ghi là một **liquidation**, không phải một fill, vì đó là điều sàn đã làm.
6. **Phân giải trong nến đi dưới dạng path summary đơn điệu, không bao giờ là nến phút.** Dãy breakpoint theo thời gian của running-min và running-max là một **thống kê đủ cho first-touch** ở bất kỳ mức nào theo cả hai hướng. Nó còn làm luật mơ hồ trở nên chính xác: một stop và một liquidation cùng chạm trong một phút thì thật sự không có thứ tự, và summary nói đúng như vậy thay vì trả về một phỏng đoán. **Đính chính ở P3-16:** quyết định này ban đầu nói summary tiết kiệm "~1.200× dữ liệu". Con số đó so 1.440 phút với *một nến ngày*, không phải với summary, và nó sai với tư cách một lời biện minh. Đo trên 900 path 1.440 phút mô phỏng, một ngày nén còn **~87 breakpoint** (p95 132; ngày xu hướng mạnh đẩy một phía lên 200–400) — **~16×** so với dòng phút thô và ~60–90× so với minute OHLCV đầy đủ, tức ~1,6 MB mỗi instrument-chuỗi trên 1.840 nến. Lý do thật không phải dung lượng payload mà là ngữ nghĩa ambiguity chính xác, cộng chi phí tải và lưu trên toàn universe × hai chuỗi × mọi campaign. Summary dựng từ **low và high của chính từng phút**, không phải giá đóng: một phút là một nến, và cái bấc của nó đúng là thứ chạm mức giá trước tiên. Module nằm ở `core/path_summary.py` (P3-16) vì summary được dựng lúc fetch mà `data` không được import `execution`.
6b. **Một nến được cắt thành các đoạn tại những mốc funding của nó, mỗi đoạn mang summary riêng** (P3-16). Funding rời ví isolated vào những thời điểm cố định, làm dịch giá thanh lý *bên trong* nến, và một dãy đơn điệu không trả lời được một mức thay đổi giữa chừng: `[100, 80, 110, 100, 90]` và `[100, 80, 110, 100, 100]` có summary cả nến giống hệt nhau, nhưng ngưỡng 95 chỉ có hiệu lực sau phút 3 thì path đầu chạm còn path sau không. Nên `resolve_bar` nhận một giá thanh lý **cho mỗi đoạn**; lệch độ dài thì từ chối chứ không tái dùng giá trị cuối, vì như thế một mốc settlement bị bỏ sót sẽ thành vô hình. Phút thiếu cũng bị từ chối (INV-94): điền khuyết cho ra một câu trả lời trông có vẻ chính xác cho một câu hỏi mà dữ liệu không trả lời được.
7. **Thứ tự xử lý mỗi nến là cố định và được hash vào protocol của campaign** dưới tên `PROTOCOL["replay"]`: funding, rồi liquidation trên mark, rồi các fill của nến với **close trước open**. Funding trước vì trả nó là rút khỏi ví isolated nên có thể chính là thứ đẩy một vị thế qua bờ. Close trước open vì một slot phải giải phóng margin trước khi slot khác xin — và slot nào thì chẳng liên quan gì tới bảng chữ cái, đó đúng là cách mà sắp theo instrument trước đã lặng lẽ từ chối những lệnh vào vốn đủ vốn.
8. **Fill ngoài cửa sổ replay bị từ chối, không bị kẹp.** Gộp một fill từ sau nến cuối vào nến cuối khiến một giao dịch tương lai làm dịch equity bên trong kỳ đo.
9. **Admission chạy trên một snapshot equity theo thứ tự chuẩn tắc** — instrument tăng dần, long trước short — với trần danh mục 10% và tối đa 10 vị thế, theo quyết định 6 của ADR-0031. `_SIDE_ORDER` là một literal chứ không dẫn xuất từ chuỗi, vì protocol hash thứ tự này.
10. **Luật clearance 25% được giữ và được test ở chỗ nó ràng buộc.** Liquidation phải nằm cách stop ít nhất 25% khoảng entry-đến-stop. Ở leverage 1 luật này gần như rỗng — liquidation cách chừng một lần biến động vào lệnh, xa ngoài mọi stop vài ATR — nên `assert_clearance` được test bằng một ca **dựng ra** để nó ràng buộc. Một luật rỗng-thoả trong mọi test thì không phải luật.

## Hệ quả

- **P5 bị bào mòn, và đó là cái giá của quyết định này.** Backtest ≡ live vẫn đúng cho signal → order → fill. Nó **không** đúng cho margin, funding hay liquidation: phần số học đó là của mình. Nó bắt buộc phải được đối chiếu với Binance testnet hoặc một sao kê thật trước khi có đồng vốn nào chịu rủi ro. Đây là nghĩa vụ của người mở campaign live đầu tiên, không phải một chú thích.
- **Bây giờ tồn tại hai nguồn sự thật cho equity** — tài khoản của Nautilus trong một backtest đơn chiến thuật, và `PerpAccount` trong joint replay. Chúng khớp nhau chỉ vì margin của venue đơn chiến thuật đã bị đặt về 0. Ai bật lại margin của venue phải đối soát cả hai hoặc chọn một.
- **Joint replay mới là nơi danh mục thật sự sống.** `validation.portfolio.combine` — N luồng returns tự tài trợ gộp theo trọng số — thôi mô tả đúng thực tế ngay khi mười chiến thuật dùng chung một số dư, một pool margin và một kill switch. Đóng góp theo slot của replay đối soát khớp với equity tài khoản (INV-92b), và đó là thứ làm cho chart theo từng instrument là một phép phân rã chứ không phải một tài khoản con bịa ra.
- **Snapshot bracket là một giả định có ngày tháng nằm bên trong mọi kết quả.** Số học margin của một campaign chỉ tốt ngang cái ngày mà bracket được lấy, và Binance thì thay đổi chúng. Lock ghi lại ngày đó.
- **Chưa có gì trong đường ống gate gọi tới bất kỳ phần nào ở đây.** Payload vào sandbox vẫn chỉ mang nến giao dịch, nên một backtest bên trong container chưa nhận được mark price hay funding. Linh kiện đã dựng và đã test; đường dây thì chưa. Cổng Phase 3 chưa đạt, và lộ trình ghi đúng như vậy thay vì đánh dấu task đã xong.

## Phương án đã cân nhắc

- **Cứ làm bên trong Nautilus** — một margin model mù hướng, position ID tự gán để giả lập hai slot, một `SimulationModule` thò vào số dư để tính funding. Bác bỏ: đó là ~100% ngữ nghĩa của sàn viết lại trên một object tài khoản có cách đánh key mâu thuẫn với chúng, và nó mua về một P5 trên danh nghĩa chứ không phải thật, vì phần số học vẫn là của mình.
- **Mô hình hoá hedge mode thành hai tài khoản con netting bên trong Nautilus.** Bác bỏ vì cùng lý do đánh key, và vì nó chia tách chính cái số dư USDT chung mà cả phase này sinh ra để mô hình hoá.
- **Đẩy nến phút vào sandbox.** Bác bỏ theo chi phí đã đo (quyết định 6) và vì nó cũng chẳng làm luật mơ hồ chính xác hơn path summary.
- **Cho lỗ của một ví với tới số dư tự do và dựa vào liquidation để ngăn.** Bác bỏ: liquidation kiểm trên mark, mà giá khớp có thể gap qua điểm phá sản giữa hai lần mark. Ràng buộc phải nằm ở ví, nếu không thì không phải ràng buộc.
