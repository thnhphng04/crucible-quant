# ADR-0026 — Engine C-gp: toán tử có kiểu và điểm xếp hạng

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-23
- **Task:** P2-11 · **Kiến trúc:** §3.1.11 (C-gp), §3.1.6 #1, §3.3.1, D20 · **Vấn đề mở:** —

## Bối cảnh

Kiến trúc v0.6 đưa C-gp — GP có kiểu trên văn phạm — làm engine chính, và §3.1.6 #1 xếp hạng việc tìm kiếm bằng `DSR(returns, N_eff) − λ₁·AST_sim − λ₂·n_params`, một phép xếp hạng chứ không phải cổng. D20 thêm trung vị SPP và plateau từ lưới cổng ④ làm thành phần phụ và đặt trần cho con chỉ đổi tham số. Hai ràng buộc định hình cách cài: INV-42 không cho phép DSR theo từng chiến lược (chỉ cổng ⑤ tính DSR), và INV-68 chỉ cho điểm xếp hạng đọc metric `public`.

## Quyết định

1. **Toán tử** (`agent/evolution/operators.py`) tác động lên genome có kiểu: `param` (một TUNABLE dịch trong biên — code giữ nguyên, cùng `strategy_hash`), `point` (đảo một phép so sánh, chiều hoặc and/or; đổi sma↔ema hay rsi↔zscore, rút lại mức của oscillator theo thang của nó), `subtree` (lấy mẫu lại một clause), `crossover` (lấy một clause từ cha thứ hai, thay thế hoặc thêm vào tối đa 3 clause; stop lấy từ một trong hai cha). Mọi con giữ ≤ 6 TUNABLE; toán tử không thỏa được thì báo `OperatorFailed`.
2. **Trần con chỉ đổi tham số:** `choose_kind` chọn `param` với xác suất 0,3 nhưng không bao giờ khi điều đó đẩy tỷ lệ con chỉ đổi tham số vượt `gp.param_only_max` (INV-66).
3. **Điểm xếp hạng** (`agent/evolution/ranking.py`): `DSR-rank − 0,10·độ trùng − 0,05·n_params/6 + 0,05·tanh(trung vị SPP) + 0,05·plateau`. DSR-rank theo ADR-0013: PSR của return IS của ứng viên so với ngưỡng đã deflate chung cho campaign SR₀(`N_eff`, `V[SR]`) — một phép xếp hạng, không bao giờ so với `dsr_min`. Độ trùng = Jaccard lớn nhất giữa chữ ký clause với mọi mục trước đó (theo thứ tự trial), nên điểm chỉ phụ thuộc vào ledger.
4. **Đầu vào:** cổng ③ nay báo thêm các moment của return IS (`sr_obs`, `skew_is`, `kurtosis_is`, `n_obs`) dạng `public`; lúc submit ghi `signature` (chữ ký clause) của genome cùng category. Không đọc bất cứ thứ gì trong detail `private` của cổng ④.

## Hệ quả

- Thành phần DSR chiếm ưu thế (khoảng [0, 1]); các thành phần khác, mỗi cái ≤ 0,1, dùng để phân định ngang điểm và đẩy đa dạng. Các λ là mặc định tạm; chỉ chỉnh khi có lý do được ghi lại, không bao giờ dựa vào kết quả holdout.
- Một phép lai có thể dùng lại đối tượng tham số của cha: renderer khai báo nó một lần.

## Các phương án đã cân nhắc

- **Gọi `deflated_sharpe_ratio` cho từng ứng viên:** không chọn — sẽ đưa lại API DSR theo từng chiến lược mà INV-42 cấm; quy ước của ADR-0013 cho cùng thứ tự.
- **Dùng Sharpe IS thô làm fitness:** không chọn — nó thưởng cho các chuỗi ngắn, may mắn (§3.1.6 #1).
