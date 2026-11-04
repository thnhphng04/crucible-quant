# Waka Waka EA (Valeriia Mishchenko): cách vận hành và cách bot dựng chuỗi martingale

Waka Waka là một EA **grid + martingale có giới hạn** chạy theo kiểu đảo chiều về trung bình (mean-reversion). Bot vào lệnh ngược xu hướng trên ba cặp chéo AUDCAD, AUDNZD và NZDCAD, dựa vào Bollinger Bands và RSI.\[1\]\[2\] Khi giá chạy ngược, bot nhồi thêm lệnh với lot tăng theo từng bậc, rồi đóng cả rổ bằng một take profit tính theo tiền (weighted TP).\[3\] Chính tài khoản live của tác giả cho thấy rủi ro thật nằm ở **floating drawdown**, không phải ở win rate: tín hiệu MQL5 ghi max drawdown theo equity **72,42%**, trong khi drawdown theo balance chỉ 13,88%.\[4\] Vào giữa tháng 10/2026, tài khoản này lại đang âm sâu theo equity.\[4\]

## TL;DR
- **Bot là gì:** EA grid/martingale cho MT4 và MT5 của Valeriia Mishchenko (ValeryTrading), giá 2.800 USD trên MQL5 với 10 activations. Bot trade AUDCAD, AUDNZD, NZDCAD trên khung M15, vào lệnh ngược xu hướng bằng Bollinger Bands và RSI. Các lệnh sau có lot multiplier chia theo nấc (lệnh thứ 2, lệnh 3–5, lệnh 6 trở đi), có giới hạn số lệnh tối đa, và cả rổ đóng bằng weighted TP.
- **Hiệu suất:** Track record live từ 2018 là có thật và có xác minh (Myfxbook #11662830, MQL5 signal 995287), nhưng con số "+12.000%" là tăng trưởng theo time-weighted tính từ vốn 500 USD. Tác giả đã nạp thêm 3.000 USD. Max drawdown là 66,54% trên Myfxbook và 72,42% (equity) trên MQL5. Trong đợt trend tháng 3–6/2024, review trên MQL5 báo cháy tài khoản: Simon Akubue (12/06/2024) viết "blew up my account after holding multiple SELL positions on NZDCAD", còn phokin (29/03/2024) viết "1% load = exploding account".
- **Khuyến nghị:** Chỉ nên coi đây là một chiến lược "bán bảo hiểm đuôi" (short tail risk). Nếu vẫn dùng, hãy để mức rủi ro thấp, bật Max Floating Drawdown ở mức bạn chấp nhận mất thật, giữ nguyên các multiplier mặc định và chỉ dùng vốn có thể mất trọn. Không nên dùng làm nguồn thu nhập chính.

## Key Findings

| Hạng mục | Thông tin | Mức độ xác minh |
|---|---|---|
| Tác giả | Valeriia Mishchenko (ValeryTrading). Hồ sơ MQL5 ghi Russia, rating người bán 4.2 (277 đánh giá), 14 sản phẩm, 11 signals\[4\] | Nguồn chính (MQL5)\[4\] |
| Ra mắt | Chạy live từ tháng 6/2018. Đăng lên MQL5 ngày 19/08/2021 (trang MT5 product 70283 ghi "Published: 19 August 2021", 23.564 lượt tải demo). Bản hiện tại 4.59 (cập nhật 08/02/2026) | Nguồn chính |
| Nền tảng | MT4 (product 66317) và MT5 (product 70283). Bản MT5 bắt buộc dùng tài khoản hedging\[3\] | Nguồn chính\[3\] |
| Cặp / khung | AUDCAD, AUDNZD, NZDCAD; M15; gắn vào một chart duy nhất (khuyên dùng AUDCAD)\[5\] | Nguồn chính\[3\] |
| Giá | 2.800 USD trên MQL5 (cả MT4 lẫn MT5), 10 activations. Website của tác giả từng bán 2.340 USD (robot-trade.fr: "Full license $2340 – … Perpetual license, 10 MT4 accounts"); hiện bestforexrobots.com và eatested.com ghi 2.520 USD ("10% OFF – REGULAR PRICE: $2,800") | MQL5: nguồn chính; giá website: nguồn thứ cấp |
| Đánh giá | MT4: 4.25 (54 reviews). MT5: 4.13 (44 reviews)\[5\]\[6\] | Nguồn chính\[3\]\[5\]\[7\] |
| Logic | Mean-reversion ngược xu hướng bằng BB và RSI; grid có Smart Distance theo biến động; multiplier theo nấc\[5\] | Nguồn chính\[3\] |
| Thoát lệnh | Weighted TP theo tiền, Break Even sau level N, TP #1/2/3 theo level hoặc số bar, trailing SL quanh điểm hòa vốn của rổ, Max Floating DD %, SL grid (mặc định 1000 pips nếu để 0)\[8\] | Nguồn chính (help center) |
| Track record | Myfxbook: +12.859% gain, +478,66% abs gain, max DD 66,54%. MQL5: growth 383,95%, equity DD 72,42%\[4\]\[9\] | Nguồn chính (bên thứ ba có xác minh)\[4\]\[10\] |

## Details

### 1. Tổng quan và danh tiếng

Theo trang MQL5, Waka Waka là "advanced grid system" và tác giả cho rằng nó "designed to exploit existing market inefficiencies" chứ không phải được fit theo dữ liệu quá khứ.\[3\]\[5\] Đây là lời tác giả tự nói, chưa ai kiểm chứng độc lập. Bot được gắn lên một chart M15 duy nhất, giữ sẵn tham số bên trong (không cần file .set), cần một VPS chạy liên tục và cần quyền truy cập website lịch tin tức cùng time server để news filter và chức năng tự dò GMT hoạt động.\[3\]\[5\]

Về đòn bẩy, tác giả khuyên: với 1:30 thì không dùng mức rủi ro cao hơn "low" nếu tài khoản dưới 6.000 USD; với 1:100 thì tài khoản 1.000 USD có thể dùng tới mức "significant".\[3\]\[5\]

Về danh tiếng, có thể chia làm hai phía:
- **Tích cực:** đây là một trong những grid EA có lịch sử live dài nhất trên MQL5.\[1\] Tác giả hỗ trợ tích cực và trả lời cả các review xấu.\[1\] Tài liệu (help center, PDF guide, Telegram khoảng 8,8 nghìn subscriber) khá đầy đủ.\[11\] Một review năm 2021 trên signal nhận xét: "Grid sizing is tied to the current volatility… System is profitable in pips".\[4\]
- **Tiêu cực:** sau các đợt trend năm 2023 và 2024 có nhiều review 1 sao. Một người viết "UPDATE 29 March 2024: I cannot in good conscience recommend Waka Waka EA to anyone… a simple and inflexible grid martingale system".\[3\] Người khác viết "This EA finally crashed & blew up my account after holding multiple SELL positions on NZDCAD for over a month" (12/06/2024), và một người nữa: "current risk 1.5% load = exploding account / 1% load = exploding account" (29/03/2024).\[5\]
- **Cảnh báo về nguồn:** rất nhiều trang "review" xếp đầu kết quả tìm kiếm (eatested, benzinga, forex-ratings…) là trang affiliate.\[12\]\[13\]\[14\] Một số còn ghi sai rằng bot không dùng martingale: eatested.com viết "uses a safe, non-martingale grid strategy", newyorkcityservers.com viết "doesn't use pure martingale (no doubling)… non-martingale grid", và eafxstore.com viết "No, Waka Waka EA uses a non-Martingale Grid system". Trong khi đó, chính help center của tác giả liệt kê tính năng "Uses Martingale". Nhiều trang khác bán bản crack và báo cáo này không dùng chúng làm nguồn tham chiếu.\[15\]\[16\]\[17\]

### 2. Logic vào lệnh

- **Tín hiệu chính:** Bollinger Bands tạo ra các mức trên và dưới.\[18\] Khi giá chạm hoặc vượt band, bot vào lệnh ngược chiều để chờ giá quay về. RSI (có tham số RSI Period và Maximum RSI Value) dùng để "filtering out trades with small potential".\[3\]\[18\] Có tham số "Working TF for BB and RSI" để chọn khung thời gian cho hai indicator này.\[8\]
- **Vì sao chọn AUDCAD, AUDNZD, NZDCAD:** ba cặp chéo này có tính đi ngang và đảo chiều mạnh, vì AUD, NZD và CAD đều là "commodity currencies" có tương quan cao với nhau.\[1\]\[3\]\[19\]\[20\] Bot không phải arbitrage hay pairs trading đúng nghĩa. Mỗi cặp chạy grid riêng, và có giới hạn "Maximum Symbols at a time" để không mở grid trên cả ba cặp cùng lúc.\[3\]\[21\]
- **Bộ lọc thời gian:** **không có bằng chứng bot chỉ trade phiên Á.** Bảng phân tích theo phiên trên Myfxbook của tài khoản chính cho thấy lệnh rải đều các phiên: London 2.730 lệnh, Frankfurt 2.512, Tokyo 2.282, New York 1.828, Sydney 1.791.\[10\] Người dùng có thể giới hạn giờ bằng HourToStartTrading/HourToStopTrading, chọn ngày trong tuần, bật "Hour to Close & Stop Trading on Friday" và bộ lọc rollover (có cả tùy chọn gỡ TP trong giờ rollover).\[8\]
- **Các bộ lọc khác:**
  - News filter có Wait Minutes Before/After, chọn mức độ tin medium/low và "Speaks", cùng các quy tắc tùy chỉnh. Mặc định đã chặn tin CAD và AUD trên AUDCAD.\[8\]
  - Bộ lọc ngày lễ và Giáng sinh/Năm mới.\[3\] Tác giả nói "I always switch EAs off during Christmas and New Year holidays".\[22\]
  - **Stock Market Crash Filter:** chặn lệnh mới khi historical volatility theo năm của lợi suất H1 trên S&P500 vượt một ngưỡng.\[8\]
  - **ML-based Pattern Recognition & Filtering**, có tham số ngưỡng xác suất.\[8\]\[23\]
  - "Reverse Strategy" (chuyển sang đánh thuận xu hướng) và "Open Opposite Trade".\[8\]

### 3. Cơ chế grid và martingale chi tiết

**3.1. Thêm lệnh khi giá chạy ngược**
- **Trade Distance:** khoảng cách tối thiểu tính bằng pip giữa các lệnh averaging.\[3\]\[24\]
- **Smart Distance:** "auto-adjusts the trade distance depending on market volatility".\[3\]\[24\] Vậy grid là **động theo biến động**, không cố định. Tác giả không công bố công thức (ATR hay độ lệch chuẩn), nên chỗ này chưa xác minh được.
- **Pause Between Grid Trades, in min:** thời gian chờ tối thiểu giữa hai lệnh grid.\[8\]
- **Calculate Levels from Initial Trade:** tính các level từ lệnh đầu tiên.\[25\] Có tùy chọn bỏ qua level kèm "Keep Original Profit Level & Lot Size", nhưng bot vẫn cần "marker orders".\[8\]
- **Check Margin for all Grid Levels:** nếu free margin không đủ để mở *toàn bộ* các level thì bot tự giảm lot ban đầu.\[8\] Đây là một chốt an toàn quan trọng.

**3.2. Lot multiplier và chuỗi lot**

Bot có ba nấc multiplier: **2nd Trade Multiplier**, **3rd–5th Trade Multiplier**, **6th– Trade Multiplier**.\[3\] Ngoài ra có **Custom Multipliers** (ví dụ chính thức "1,2,4,8,12,18,27,41"), tham số này được ưu tiên hơn ba nấc kia.\[8\]\[25\] Cấu trúc này giải thích chữ "smart" hay "limited martingale": lệnh thứ 2 tăng ít hoặc không tăng, lệnh 3–5 tăng mạnh (kiểu ×2), rồi từ lệnh 6 tăng chậm lại (khoảng ×1,5). Nhìn chuỗi ví dụ chính thức: 1→2→4→8 là ×2, còn 8→12→18→27→41 là ×1,5. Như vậy đây là martingale *giảm tốc* chứ không phải nhân đôi mãi.

Tác giả không công bố giá trị mặc định ở nơi đọc được. Các trang bán lại ghi mặc định MT4 v4.59 như sau (**chưa xác minh**): Trade Distance = 35 pips; multiplier lần lượt 1.0 / 2.0 / 1.6; Maximum Trades = 9; TP 10 pips (weighted); Max Floating Drawdown = 100%.\[25\]\[26\] Một review 1 sao trên MQL5 cũng nói "max. drawdown parameter which by default is set to 100% DD".\[5\]

**Ví dụ minh họa** (giả định của tôi: multiplier nhân trên lot của lệnh liền trước, khoảng cách cố định 35 pips, chưa tính Smart Distance):

| Lệnh | Lot (×L ban đầu) | Vị trí (pips ngược chiều) |
|---|---|---|
| 1 | 1 | 0 |
| 2 | 1 | 35 |
| 3 | 2 | 70 |
| 4 | 4 | 105 |
| 5 | 8 | 140 |
| 6 | 12,8 | 175 |
| 7 | 20,5 | 210 |
| 8 | 32,8 | 245 |
| 9 | 52,4 | 280 |

Theo các giả định trên:
- **Tổng khối lượng** khoảng 134,5L. Lệnh cuối chiếm khoảng 39% toàn rổ.
- **Giá hòa vốn của rổ** nằm ở khoảng 230 pips. Khi đã có 9 lệnh, giá chỉ cần **hồi khoảng 50 pips trên tổng 280 pips** là rổ thoát hòa. Đây là "phép màu" của martingale: win rate rất cao.
- **Phía ngược lại:** ở level 9, floating loss vào khoảng 6.690 "L-pips". Với L = 0,01 lot trên AUDCAD (1 pip khoảng 0,07 USD), mức lỗ là **khoảng 480 USD** cho mỗi 0,01 lot ban đầu. Sau đó, *mỗi pip đi tiếp* làm mất thêm khoảng 9–10 USD, vì bot giữ 1,345 lot mà không thêm lệnh nữa. Rủi ro tăng phi tuyến theo độ sâu của trend, và lỗ dồn hết về phần đuôi.

**3.3. Đóng rổ**
- **Weighted TP:** nếu "TakeProfit for Grid" = 0 thì TP của rổ được đặt để lợi nhuận bằng TP của lệnh đầu *tính theo tiền*, không theo pips.\[3\]\[18\] Bản mới có tùy chọn "Weighted TakeProfit" riêng, và TP grid giờ nhận được cả giá trị 0 hoặc âm (thoát khẩn cấp).\[8\]
- **Break Even after this Level:** khi rổ đã tới level N, bot thoát ở mức hòa vốn ngay khi có cơ hội. Tác giả gợi ý dùng tham số này để giới hạn rủi ro.\[27\]
- **TakeProfit #1/2/3 after this Level / after X bars:** giảm dần mục tiêu lợi nhuận khi rổ đã sâu hoặc đã kéo dài lâu, cho phép giá trị âm hoặc 0 (tức chấp nhận cắt lỗ nhỏ).\[8\]
- **Trailing SL** dựa trên "grid breakeven point (= weighted average entry price of all orders including SWAPs and commissions)".\[8\]
- **Cover Swaps:** nới TP để bù phần swap.\[8\] Điều này quan trọng vì rổ có thể giữ hàng tuần.
- **Force Basket Closure once the Initial Order is Closed.**\[8\]
- **Partial closing** (đóng từng phần, kiểu đóng lệnh đầu và lệnh cuối để bù nhau) **không thấy được ghi nhận** trong tài liệu chính thức.

**3.4. Tham số rủi ro chính mà người dùng chỉnh được**
- **Lot-sizing Method:** Fixed Lots, Dynamic Lots (số balance/equity cho mỗi 0,01 lot), Deposit Load % hoặc 4 preset rủi ro.\[3\]\[21\] Theo tác giả, mức medium tương ứng khoảng 0,5% deposit load ("I had 11% DD with 0.5% deposit load").\[27\] Mức low được một review 1 sao trên trang MQL5 MT5 (product 70283) nêu rõ là 0,25%: "I can only run the Waka Waka EA on low risk, 0.25% deposit load"; mức high là 1,5% theo bài blog tháng 11/2022 của tác giả ("high risks (1.5% DL)").
- **Maximum Lot, Max Open Lots (Filter), Maximum Symbols at a time, Allow Hedging, Allow Buy/Sell.**\[3\]\[5\]\[8\]
- **Maximum (Floating) Drawdown Percent:** đóng toàn bộ lệnh khi floating DD vượt ngưỡng; có bản riêng cho từng symbol và có "Max Drawdown Action", ví dụ đóng lệnh rồi dừng 24 giờ.\[3\]\[5\]\[8\]\[25\]
- **Max Daily Drawdown Limit (% hoặc tiền) theo luật FTMO**, cho tài khoản prop.\[8\]
- **StopLoss for Grid** (bằng 1000 pips nếu để 0) và **Hide StopLoss.**\[18\]\[28\]

### 4. Quản lý rủi ro trong thực tế

- **Hard stop loss:** về mặt kỹ thuật có, nhưng mặc định khoảng 1000 pips (nếu để 0).\[18\]\[28\] Mức này quá xa để có ý nghĩa với các cặp chéo vốn dao động hẹp. Lớp phòng vệ thực sự là **Max Floating Drawdown %**, và theo nguồn bán lại thì mặc định đặt ở 100%, tức là tắt.\[25\]
- **Quan điểm của tác giả:** "Any grid trading system will eventually blow up the account if no risk management is applied".\[29\] Bà khuyên lấy max drawdown lịch sử nhân 2 rồi chọn mức rủi ro sao cho chịu được con số đó, và đặt Max DD trong khoảng 1x–2x (có lúc bà nói 1,5x) so với drawdown lịch sử. Theo bà, với mức medium, backtest có "up to 20% drawdowns".\[11\]\[27\]\[30\] Bà cũng nói thẳng rằng việc đổi multiplier thành 1-1-1 hay nới TP "will interfere with the trading logic", và kể lại nhiều trường hợp người dùng mất tiền thật vì làm vậy.\[11\]\[31\]
- **Mâu thuẫn quan trọng:** tác giả nói "If you use correct risk management, losing the account with Waka Waka is impossible".\[5\] Câu này *đúng về mặt hình thức* (bot đóng ở mức DD đã đặt nên không về 0), nhưng ở ngưỡng 30–50% thì về kinh tế vẫn là một khoản lỗ rất nặng. Một reviewer đã chỉ ra đúng điểm này: "we do not blow the account but we incur in losses".\[3\]\[5\]

**Các đợt drawdown đã được ghi nhận:**
- **Tháng 11/2022 (AUDCAD trend):** tác giả nói tài khoản của bà DD 11% ở 0,5% deposit load, còn người dùng 1,5% DL chịu khoảng 50% DD. Bà công nhận nó "caused losses in cases where people used too high risks".\[27\] Bộ lọc Stock Market Crash Filter tránh được đợt này.\[27\]
- **Tháng 6/2023 (AUDCAD):** một người dùng báo "account with High risk was washed out (AUDCAD from 2023-06-12 to 2023-06-23)".\[3\] Tác giả viết rằng thị trường "rocky" đã gây ra "painful and unfortunate losses".\[11\]
- **Tháng 3–6/2024 (AUDNZD, NZDCAD trend):** đây là đợt nặng nhất. Thông tin chính:
  - Diễn đàn Myfxbook ghi "65% DD after some recovery" vào ngày 02/04/2024. Một review MQL5 viết "Historical drawdown of 50% has been reached recently on the developer account".\[3\]\[32\]
  - Tài khoản chính có thêm một khoản **nạp 3.000 USD**. Theo ForexRobotNation thì khoản nạp diễn ra "in May", khi tài khoản đang trong drawdown.\[33\] Trên Myfxbook có người viết "She made a 3000$ deposit. All her clients who didnt add funds blew their accounts".\[34\] Câu sau là cáo buộc của người dùng, chưa được kiểm chứng.
  - Ngày 06/01/2024, trong bình luận #145 trên trang MQL5 MT5, Valeriia Mishchenko khuyên một người dùng: "I suggest you try to save your Waka account, there's no harm to deposit on a account with high drawdown. This is a part of trading skills." Về bản chất, đây là nạp thêm margin để cứu martingale.
- **Hiện tại (tháng 9–10/2026):** MQL5 liên tục gắn cảnh báo "High current drawdown in 30–32% indicates the absence of risk limitation". Ngày 14/10/2026 signal hiển thị "Subscription not permitted. Current drawdown is dangerous for subscribers". Lúc đó equity là 6.029 USD trên balance 15.901 USD, tức floating DD khoảng 62%.\[4\] Myfxbook ngày 12–13/10 ghi equity bằng 42,15% balance.\[10\] **Đây là số liệu ở thời điểm viết báo cáo, có thể thay đổi rất nhanh.**

### 5. Bằng chứng hiệu suất: phần nào đã xác minh, phần nào là marketing

**Myfxbook "Waka Waka" #11662830** (real, ICE-FX/IC Markets, 1:300, MT4):
- Gain +12.859%, abs gain +478,66%, max DD 66,54%, trung bình 4,89%/tháng.\[9\]\[10\]
- Deposits 3.500 USD, withdrawals 4.352 USD, profit 16.753 USD, interest (swap) −615 USD.\[4\]\[10\]
- 5.509 lệnh, win rate theo phiên khoảng 73–75%.\[4\]\[10\]

**MQL5 signal 995287:**
- Growth 383,95%, 435 tuần theo dõi (bắt đầu theo dõi từ 06/05/2021). Profit factor 1,70, Sharpe 0,11.\[4\]
- Avg win 10,00 USD so với avg loss −16,81 USD. Win rate 74,04%.\[4\]
- Max deposit load 72,13%. Drawdown theo balance 13,88%, **theo equity 72,42%**.\[4\]
- Ghi chú của chính MQL5: "Trading style has changed. Trading history before the start of monitoring is not included".\[4\]

**Diễn giải các con số:**
- "+12.000%" là **time-weighted gain**. Phép tính này loại bỏ ảnh hưởng của nạp/rút, và vì vốn gốc chỉ 500 USD nên tỷ lệ phình rất lớn. Theo dòng tiền thật: nạp 3.500 USD, đã rút 4.352 USD, equity hiện khoảng 6.000–6.700 USD.\[4\]\[10\] Như vậy chủ tài khoản vẫn đang lời, nhưng ở mức hàng nghìn USD trong 8 năm chứ không phải 120 lần vốn.
- Khoảng cách giữa **balance DD (14%) và equity DD (72%)** cho thấy rõ bản chất martingale. Các khoản lỗ hiếm khi được chốt nên đường balance trông rất mượt, còn rủi ro thật nằm trong equity.
- Trang valerytrading.com/products/waka-waka ghi "Account growth: 11772.44%… Maximum drawdown: 66.54%… Consecutive months in profit: 88 of 89" (dữ liệu 2018–2025); một phiên bản khác của trang ghi "96/99 months profitable". Các câu này, cùng "world records" hay "1474+ satisfied clients", là **marketing của tác giả**. Tháng có lời tính theo balance không phản ánh mức floating DD bên trong tháng đó.
- Tác giả đã xóa vài hệ thống Myfxbook cũ ("Waka Waka EA" #8379251, "Waka Smart TP"). Biến thể "Waka Close" có DD 90,58%.\[9\]\[35\] Việc giữ lại tài khoản tốt nhất và xóa các tài khoản khác tạo ra **survivorship bias** khi nhìn vào hồ sơ.
- Tài khoản của người dùng khác cho kết quả rất phân tán. Ví dụ tài khoản Seyoxx (Eightcap) có DD 43,95% với gain 56%. Một số tài khoản prop dùng rủi ro rất thấp (DD 1–1,5%, lời khoảng 1%/tháng).\[36\]\[37\]

### 6. Phân tích phản biện

**Điểm mạnh**
- Edge có cơ sở: các cặp chéo AUD, NZD, CAD có tính mean-reversion thật. Bot lời theo pips trong dài hạn ("profitable in pips"), tức là không chỉ sống nhờ grid.\[4\]
- Martingale *giảm tốc* (×2 ở các lệnh đầu, sau đó khoảng ×1,5), số lệnh tối đa có giới hạn, grid giãn theo biến động và có kiểm tra margin cho toàn bộ level. Tất cả an toàn hơn kiểu nhân đôi vô hạn.
- Bộ công cụ thoát lệnh phong phú (breakeven theo level, TP âm, trailing quanh điểm hòa vốn, Max DD, giới hạn ngày kiểu FTMO). Lịch sử live dài và minh bạch hơn phần lớn EA trên thị trường.

**Điểm yếu**
- **Risk of ruin không bằng 0.** Martingale không thay đổi expectancy của tín hiệu gốc, nó chỉ biến phân phối lợi nhuận thành dạng "nhiều lần lời nhỏ, một lần lỗ rất lớn" (negative skew). Gọi p là xác suất một rổ chạm Max DD. Với N rổ độc lập, xác suất gặp ít nhất một lần lỗ lớn là 1 − (1 − p)^N, tiến về 1 khi N lớn. Thực tế còn tệ hơn vì các trend trên AUD, NZD, CAD có **tương quan** với nhau (ba cặp dùng chung ba đồng tiền), nên cú sốc có thể đánh vào nhiều rổ cùng lúc.
- **Ngưỡng Max DD là bài toán đánh đổi khó.** Đặt chặt thì bị kích hoạt liên tục, như chính tác giả cảnh báo.\[11\]\[31\] Đặt lỏng (mặc định 100%) thì một trend kéo dài có thể quét gần hết tài khoản.
- **Chi phí ẩn:** swap âm khi giữ rổ nhiều tuần (−615 USD trên tài khoản chính).\[4\] Kết quả còn phụ thuộc broker: "you might open a trade while other users will not", như một review trên Slashdot nhận xét.\[38\]
- **Backtest dễ gây hiểu nhầm:** tác giả có 20 năm backtest, nhưng cả các đợt 2022, 2023 và 2024 đều đưa DD live vượt mức người dùng kỳ vọng từ backtest. Bản thân tác giả cũng thừa nhận "any trading system will eventually surpass the historical drawdown".\[27\]\[30\]

**Tổng hợp ý kiến cộng đồng**
- Review trên MQL5 phân cực: phần lớn 5 sao viết vào giai đoạn yên ả (2021–2023), các review 1 sao tập trung sau tháng 3/2024.
- Diễn đàn Myfxbook có các nhận xét như "Martingale Ea's will go bust once the trend does not revert".\[34\]
- Các diễn đàn nhỏ (forexforum.co) chủ yếu đi kèm bản crack. Gần như không có thảo luận có giá trị trên Reddit hay Forex Factory: chỉ có một thread xếp hạng trên Forex Factory, không phân tích gì.\[39\]\[40\]\[41\]

## Recommendations

1. **Coi đây là chiến lược short-volatility hoặc bán tail risk**, không phải "thu nhập ổn định". Với nền tảng định lượng, hãy đánh giá bằng phân phối equity DD trong từng rổ, không bằng win rate hay số tháng có lời.
2. **Trước khi mua, chạy 14 ngày trial hoặc demo trên MQL5** kèm backtest bằng tick data. Ghi lại (a) giá trị mặc định thật của Trade Distance, các multiplier và Maximum Trades trong bản 4.59, và (b) mức floating DD lớn nhất cho từng preset. Đây là các số tác giả không công bố công khai ở nơi đọc được.
3. **Nếu vẫn dùng:**
   - Chọn preset low (khoảng 0,25% deposit load) hoặc thấp hơn, với đòn bẩy từ 1:100 trở lên.
   - Đặt Max Floating Drawdown % ở mức bạn chấp nhận *mất thật*, ví dụ 25–35%, đồng thời tính trước rằng ngưỡng này sẽ bị chạm.
   - Bật Break Even after Level (ví dụ level 6–7) và Stock Market Crash Filter.
   - Giữ "Maximum Symbols at a time" thấp để tránh rủi ro tương quan.
   - Đừng sửa multiplier và TP nếu chưa backtest lại.
4. **Không nạp thêm tiền để "cứu" một rổ martingale.** Làm vậy là tăng cược đúng lúc xác suất bất lợi đang cao nhất.
5. **Không tải hay dùng bản crack.** Các bản này không được cập nhật (news filter, GMT), có thể chứa mã độc, và vi phạm bản quyền.

## Caveats

- Các giá trị mặc định (35 pips, 1.0/2.0/1.6, 9 lệnh, Max DD 100%) chỉ lấy từ trang bán lại và từ một review. Bảng 4 preset (trang 57 của PDF guide) không đọc được. Ví dụ tính chuỗi lot ở trên dựa vào giả định của tôi về cách áp multiplier.
- Công thức của Smart Distance và của bộ lọc ML không được công bố.
- Thời điểm và mức đáy chính xác của đợt drawdown 2024, cũng như ngày nạp 3.000 USD, chỉ lấy từ nguồn thứ cấp. Các chỉ số Myfxbook và MQL5 là ảnh chụp ngày 12–14/10/2026 và thay đổi theo từng giờ.
- Hồ sơ MQL5 ghi tác giả ở Russia, còn một trang review ghi Ukraine.\[4\]\[5\]\[6\]\[42\] Thông tin này không ảnh hưởng tới phân tích.
- Báo cáo này không phải lời khuyên đầu tư. Forex có đòn bẩy và grid/martingale đều có rủi ro mất toàn bộ vốn.

## Sources

1. [Waka Waka EA Review (MT4 + MT5) — 7-Year Live Signal Honest](https://cheaperforex.com/waka-waka-ea-mt4-download-guide/)
2. [Waka Waka EA Review: Still Worth The \$2,500 Price Tag In 2026?](https://newyorkcityservers.com/blog/waka-waka-ea-review)
3. [Waka Waka EA MT5 | Buy Trading Robot (Expert Advisor) for MetaTrader 5](https://www.mql5.com/en/market/product/70283)
4. [Trading Signals for MetaTrader 4: Waka Waka EA](https://www.mql5.com/en/signals/995287)
5. [Waka Waka EA | Buy Trading Robot (Expert Advisor) for MetaTrader 4](https://www.mql5.com/en/market/product/66317)
6. [Waka Waka EA MT5 Download — Grid EA Review](https://fxtoolsai.com/waka-waka-ea-mt5/)
7. [Gold Extractor](https://www.mql5.com/en/market/product/72194)
8. [Help Center - ValeryTrading](https://help.valerytrading.com/catgory/wquery/waka-waka-ea/cat_id-pjcxbwcs-71468/r-pjcxbwcs-2)
9. [Waka Waka EA Forex Trading System by Forex Trader MischenkoValeria](https://www.myfxbook.com/members/MischenkoValeria/waka-waka-ea/8379251)
10. [Waka Waka Forex Trading System by Forex Trader MischenkoValeria](https://www.myfxbook.com/members/MischenkoValeria/waka-waka/11662830)
11. [Valery Trading](https://t.me/s/valeryEAs?after=430)
12. [Waka Waka EA by ValeryTrading Review](https://benzinga.com/money/waka-waka-ea-by-valerytrading-review)
13. [Waka Waka EA Review 2026](https://eatested.com/waka-waka-ea-review/)
14. [Waka Waka Review and Rating 2026](https://www.forex-ratings.com/forex-advisors-rating/waka-waka/)
15. [thetradelovers.gumroad.com](https://thetradelovers.gumroad.com/l/WakaWakaSource)
16. [thetradelovers.gumroad.com](https://thetradelovers.gumroad.com/l/WakaWakaEA)
17. [atomfxstore.gumroad.com](https://atomfxstore.gumroad.com/l/WakaWakaEAV443MT4)
18. [Waka Waka EA - Strategy](https://help.valerytrading.com/article/wquery/waka-waka-ea---strategy/article_id-pjcxbwcs-195094)
19. [Waka Waka EA Review 2026: Analyzing the Grid Strategy Risks…](https://trade-charts.net/forex-robots/waka-waka-ea-review)
20. [Waka Waka EA: Explore the Features of This Forex Trading Robot](https://algotradingspace.com/robots/forex/waka-waka-ea-the-grid-trading-expert-advisor-for-minor-cross-pairs)
21. [Waka Waka EA: MM & Risk settings](https://help.valerytrading.com/article/wquery/waka-waka-ea-mm-risk-settings/article_id-pjcxbwcs-196199)
22. [Comments](https://www.mql5.com/en/market/product/70283/comments/page8)
23. [Waka Waka EA MT4 v4.59 with SetFiles (1473+) - FREE DOWNLOAD](https://eafxstore.com/product/waka-waka-ea-mt4/)
24. [Waka Waka EA: Grid settings](https://help.valerytrading.com/article/wquery/waka-waka-ea-grid-settings/article_id-pjcxbwcs-196200)
25. [Waka Waka EA MT4 v4.59 with SetFiles (1473+) - FREE DOWNLOAD](https://eafxstore.com/blog/waka-waka-ea-settings/)
26. [Waka Waka EA MT4](https://shopforexea.com/waka-waka-ea/)
27. [Waka Waka EA recent drawdown on AUDCAD pair❗️](https://shop.valerytrading.com/blogs/updates/%E2%9D%97%EF%B8%8Fwaka-waka-ea-recent-drawdown-on-audcad-pair)
28. [Waka Waka EA SOURCE CODE MT4 V 2.12](https://fxprisma.com/products/waka-waka-ea-source-code-mt4-v-2-12)
29. [Comments](https://www.mql5.com/en/market/product/66317/comments/page13)
30. [Help Center - ValeryTrading](https://help.valerytrading.com/article/wquery/waka-waka-ea-risk-management/article_id-pjcxbwcs-196197)
31. [Help Center - ValeryTrading](https://help.valerytrading.com/article/wquery/waka-waka-ea-grid-trading-systems-risks/article_id-pjcxbwcs-196196)
32. [Waka Waka EA (MischenkoValeria) Discussion](https://www.myfxbook.com/community/trading-systems/waka-waka-ea/2752729,1)
33. [Waka Waka EA Review (Worth \$2,600 Price Tag?) - ForexRobotNation.com - Best Forex Robot & Expert Advisor Reviews](https://forexrobotnation.com/waka-waka-ea-review-worth-2600-price-tag/)
34. [Waka Waka EA (MischenkoValeria) Discussion](https://www.myfxbook.com/community/trading-systems/waka-waka-ea/2752729,2)
35. [Waka Smart TP Forex Trading System by Forex Trader MischenkoValeria](https://www.myfxbook.com/members/MischenkoValeria/waka-smart-tp/9322919)
36. [Waka Waka EA live account Forex Trading System by Forex Trader Seyoxx](https://www.myfxbook.com/members/Seyoxx/waka-waka-ea-live-account/10505890)
37. [Waka Waka - \$200k Funded FX2Funding Forex Trading System by Forex Trader PetkoA](https://www.myfxbook.com/members/PetkoA/waka-waka-200k-funded-fx2funding/10627201)
38. [Waka Waka EA Reviews - 2026](https://slashdot.org/software/p/Waka-Waka-EA/)
39. [a good martignle EA, WAKA WAKA](https://www.forexforum.co/threads/a-good-martignle-ea-waka-waka.25090/)
40. [Waka Waka EA? Is it good? - Top Forex Forum](https://www.forexforum.co/threads/waka-waka-ea-is-it-good.28335/)
41. [Best Expert Advisor forex Ranking](https://www.forexfactory.com/thread/1257817-best-expert-advisor-forex-ranking)
42. [Waka Waka EA Review 2026 - Trading Bots](https://www.scamfreefx.com/trading-bot/waka-waka-ea)
