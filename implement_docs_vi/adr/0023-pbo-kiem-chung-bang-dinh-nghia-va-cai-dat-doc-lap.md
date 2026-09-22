# ADR-0023 — PBO được kiểm chứng bằng định nghĩa và một cài đặt độc lập

- **Trạng thái:** Đã chấp nhận (người dùng quyết định)
- **Ngày:** 2026-09-22
- **Task:** P1-03, cổng giai đoạn 1 · **Kiến trúc:** §7 (giai đoạn 1), ghi chú §6 về `purgedcv` · **Vấn đề mở:** đóng O18

## Bối cảnh

Cổng giai đoạn 1 từng đòi DSR và PBO khớp ví dụ số trong các paper gốc. DSR được test với một ví dụ số đã công bố. PBO không có fixture công bố: `test_pbo_reference_example` được tính tay theo định nghĩa CSCV của Bailey và cộng sự (2017), còn `test_matches_purgedcv` so với một thư viện khác — câu chữ cũ đã nói quá thành "khớp paper".

## Quyết định

1. **Câu chữ của cổng (kiến trúc §7, sửa bản VI trước, rồi EN):** DSR khớp ví dụ số công bố trong paper; PBO khớp các fixture tính tay theo định nghĩa CSCV/PBO và kết quả của một cài đặt độc lập trên cùng đầu vào.
2. **Fixture tính tay** có kết quả kỳ vọng cố định và giải thích cách tính (`tests/validation/test_pbo.py::test_pbo_reference_example`: hai trường hợp S = 2, PBO = 1 và PBO = 0, kèm các logit).
3. **Phép đối chiếu ghi phiên bản tham chiếu:** `PURGEDCV_VERSION = "0.1.6"` trong `tests/validation/test_pbo.py`; test trượt nếu cài phiên bản khác, nên một tham chiếu mới phải được review trước khi tin.
4. **Tính độc lập được kiểm, không giả định:** `validation/pbo.py` chỉ import numpy/scipy — không `purgedcv`, không module nào của dự án (`test_pbo_is_independent_of_purgedcv`). Nếu PBO của ta gọi lại `purgedcv`, so với nó không được coi là kiểm chứng.
5. Một ví dụ có lời giải đã công bố có thể bổ sung sau như một fixture nữa.

## Hệ quả

- Cổng giai đoạn 1 đạt cho PBO theo câu chữ đã nêu; roadmap và INV-41 dẫn tới nó.
- Nâng cấp `purgedcv` cần chủ động đổi phiên bản đã ghim trong test.

## Phương án đã cân nhắc

- **Tìm trước một ví dụ PBO đã công bố có lời giải:** không bắt buộc để đóng cổng; có thể bổ sung sau.
- **Giữ câu chữ cũ:** loại — nó khẳng định nhiều hơn những gì test cho thấy.
