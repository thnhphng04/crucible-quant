# ADR-0005 — Leaky oracle và guardrail động (①b)

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P0-11 · **Kiến trúc:** §3.2 (①a, ①b), §7 GĐ 0, 07-VALIDATION-LAYER §9 · **Vấn đề mở:** O8

## Bối cảnh

Cổng GĐ 0 là pipeline phải bác bỏ bốn leaky oracle: `.shift(-1)`, giá đóng của nến đang hình thành, chỉ báo vẽ lại (repaint), và thông tin được ghép trước khi nó được công bố. Lộ trình dự kiến cấp 1 chết ở ①a và cấp 2–4 ở ①b. Hai sự thật đã thay đổi bức tranh đó:

- Whitelist của ①a (P0-07) chỉ cho phép chỉ báo nhân quả đã đăng ký (INV-33), số học, phép so sánh và `Signal`. Không thể viết rò rỉ nào bên trong nó: oracle nào cũng cần import, slice hoặc lời gọi ngoài whitelist, nên **cả bốn đều chết ở ①a**.
- Mọi strategy chạy qua `step()` trên một cửa sổ kết thúc ở bar hiện tại, nên look-ahead bên trong `indicators()` không chạm được tương lai trong chính backtest; nó chỉ làm tín hiệu thiếu nhất quán.

①b vẫn quan trọng: nó là tuyến phòng thủ thứ hai nếu whitelist có lỗ hổng (một chỉ báo phi nhân quả được thêm vào registry, một phép ghép dữ liệu làm sai).

## Quyết định

1. **Oracle** nằm ở `validation/oracles/level{1..4}_*.py`, dạng template với vùng cố định nguyên vẹn (nên ①a báo `AST_REJECT`, không phải `TEMPLATE_TAMPER`):
   1. giá đóng ngày mai qua `pd.Series.shift(-1)`;
   2. một nến 2 ngày, nhóm theo ngày lịch, có giá đóng cuối cùng được gắn lên cả hai bar của nó;
   3. trung bình trượt 5 bar đặt giữa (`np.convolve`, mode `same`);
   4. một báo cáo tổng hợp về ngày *t* được công bố 3 bar sau nhưng lại được ghép ở *t* (O8: dữ liệu crypto miễn phí không có lịch sử point-in-time, nên độ trễ là tổng hợp).
2. **Phép đo ①b** (`validation/leak_check.py`, job sandbox `leak_check`): với 20 điểm cắt *t* có seed trong `[n/10, n−2]`, tín hiệu tại 20 bar `s ≤ t` được tính từ chỉ báo trên toàn chuỗi, trên `bars[:t+1]` (cắt cụt), và trên một bản sao có các bar sau *t* là random walk có seed với độ biến động trước *t* (nhiễu). Bất kỳ khác biệt nào (hướng, hoặc strength / stop / take-profit lệch quá 1e-9 tương đối) là rò rỉ. Chỉ báo bắt đầu từ bar đầu tiên trong cả ba lần chạy, nên bộ lọc đệ quy (EMA) khớp chính xác.
3. **Gate ①b** (`DynamicGuardrail`) chạy job đó trong sandbox trên universe IS của ứng viên; một rò rỉ ⇒ `LEAK_REJECT` với 10 rò rỉ đầu trong `detail`. Sandbox thất bại thì đi qua `sandbox_failure()` (ADR-0004).
4. **①a báo cả thứ mà chuỗi lời gọi dựa trên:** lời gọi ngoài whitelist cũng được kiểm tra bên trong, nên `pd.Series(x).shift(-1).to_numpy()` được báo là look-ahead `.shift()`, không chỉ là lời gọi bị cấm.
5. **Tiêu chí nghiệm thu, phát biểu lại:** pipeline đầy đủ bác bỏ cả bốn oracle ở ①a kèm dòng ledger; pipeline chỉ có ①b (bỏ ①a, giả lập lỗ hổng) bác bỏ cả bốn ở ①b với dòng `LEAK_REJECT`; EMA crossover qua ①a và ①b.

## Hệ quả

- INV-34 được chứng minh hai lần: `tests/validation/test_oracles.py` (phép đo phía host trên 5 seed và 2 chuỗi, cùng các test gate và pipeline gắn marker docker).
- ①b tốn một job sandbox (≈ 3 s) cộng 41 lượt tính chỉ báo mỗi mã.
- Với cấp 2, việc phát hiện mang tính thống kê (chỉ bar đầu của mỗi nhóm 2 ngày bị rò rỉ); 20 điểm cắt × 20 bar bắt được nó ở mọi seed đã thử.

## Phương án đã cân nhắc

- **Nới ①a để cấp 2–4 đến được ①b trong pipeline đầy đủ:** bác bỏ — ngưỡng gate và guardrail chỉ được siết chặt.
- **Chỉ chạy lại bar cuối với giá đóng bị nhiễu (ý tưởng cấp 2 của lộ trình):** bác bỏ — tín hiệu ở một bar đã đóng phụ thuộc hợp lệ vào giá đóng của bar đó; nhiễu các bar *sau* điểm cắt mới là cách cô lập tương lai.
- **So với `step()` trên cửa sổ lookback:** bác bỏ — chỉ báo đệ quy bắt đầu ở các bar khác nhau sẽ lệch nhẹ và gây báo động giả.
