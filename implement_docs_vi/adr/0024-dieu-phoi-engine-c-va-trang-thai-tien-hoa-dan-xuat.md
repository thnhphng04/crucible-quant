# ADR-0024 — Điều phối engine C: Python thuần, trạng thái tiến hóa dẫn xuất từ ledger

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-22
- **Task:** P2-01 · **Kiến trúc:** §3.1.11 (v0.6), §3.1.8, §4.1, §6 (tech stack) · **Vấn đề mở:** —

## Bối cảnh

Kiến trúc v0.6 (D19) đưa engine C không LLM lên làm trọng tâm của giai đoạn 2: C-gp (GP có kiểu) và C-random (mẫu i.i.d.), engine A/B hoãn. §6 chọn LangGraph để điều phối agent, vì trạng thái và vòng lặp của các agent LLM. Engine C không có LLM và chạy một thuật toán cố định (lấy mẫu hoặc lai tạo → submit → archive), nên câu hỏi điều phối mở lại. Vòng lặp cũng phải chịu được việc bị ngắt mà không mất hay nhân đôi trial.

## Quyết định

1. **Điều phối bằng Python thuần.** Producer, hàng đợi prefetch và K slot đánh giá dùng thư viện chuẩn (`threading`, `queue`, `concurrent.futures`). Không dùng LangGraph ở giai đoạn 2. Xem lại khi mở lại engine A/B.
2. **Ledger là trạng thái duy nhất.** Feature map, archive từng engine, đảo và tập cha được dựng lại từ `trials`, `gate_results`, `generation_log` và kho source chiến lược (`results/strategies/<hash>.py`). Không có database tiến hóa riêng; run khởi động lại sẽ tiếp tục từ ledger.
3. **Engine chỉ sinh source.** Mọi ứng viên đi qua `research_run.submit` → `candidate_pipeline().run`; engine không bao giờ import backtester hay sandbox (import-linter đã cấm).
4. **Lập lịch đếm trial thống kê.** Hạn mức theo (engine, seed) được đối chiếu với các dòng `trials`, không bao giờ với số lời gọi hay thời gian chạy.
5. **Không có cổng ⓪ drift ở giai đoạn 2.** Drift canh các lượt refine của LLM so với một hypothesis; engine C không có cả hai. ID của các cổng này vẫn được giữ chỗ.

## Hệ quả

- Vòng lặp tất định, test được; không có gì phải đối soát sau khi crash.
- Dựng lại trạng thái tốn một lần đọc ledger mỗi thế hệ — ổn với vài nghìn trial; xem lại cùng O9 nếu thành vấn đề.
- Kiến trúc §6 ghi LangGraph: khi A/B quay lại, hoặc ADR này bị thay thế, hoặc sửa §6 (VI trước) để nói LangGraph chỉ áp dụng cho engine LLM.

## Các phương án đã cân nhắc

- **Dùng LangGraph ngay:** không chọn — thêm dependency cho những state machine mà engine C không có.
- **Một database tiến hóa riêng:** không chọn — một nguồn sự thật thứ hai bên cạnh ledger (P2) và có thể lệch khỏi nó.
