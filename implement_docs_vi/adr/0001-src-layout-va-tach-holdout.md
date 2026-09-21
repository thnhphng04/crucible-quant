# ADR-0001 — src layout, và tách dữ liệu holdout khỏi code holdout

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P0-01 · **Kiến trúc:** §5, §4.2, §3.3.1 · **Vấn đề mở:** O7 (cách làm)

## Bối cảnh

§5 của kiến trúc đặt `core/`, `agent/`, `validation/`, `holdout/` … ở gốc repo, trong khi package tên là `quantcrucible` và template ở §3.3.1 import `from core.strategy.base`. Các package cấp cao nhất ở gốc tên `core`, `data`, `config` dễ trùng với thư viện khác và khiến việc đóng gói thành package cài đặt được trở nên vụng về.

§5 cũng đặt `evaluator_proc.py` (code, phải được commit và test) bên trong `holdout/` (dữ liệu, `chmod 0400`, không bao giờ được commit hay bị tiến trình nghiên cứu đọc). Trộn hai thứ này nghĩa là mọi công cụ cần tới code — editor, test, coding agent — đều phải mở thư mục chứa dữ liệu.

`chmod 0400` không có tương đương trực tiếp trên Windows, nơi dự án này chạy (A6).

## Quyết định

1. **src layout:** toàn bộ code nằm dưới `src/quantcrucible/` với cây con giống §5 (`core/`, `agent/`, `validation/`, `ledger/`, `execution/`, `holdout/`, cộng `config/` cho bộ nạp cấu hình và `data/` cho các cài đặt `DataSource`). Import có dạng `quantcrucible.core.strategy.base`, v.v. Vùng cố định của template strategy import từ `quantcrucible.core…`.
2. **Tách holdout:**
   - `holdout/` ở gốc = **chỉ dữ liệu**; gitignore; không bao giờ hiển thị với tiến trình nghiên cứu, sandbox hay coding agent (`.claude/hooks/guard_paths.py`). `holdout.lock` ở gốc chỉ chứa khoảng thời gian và hash SHA256 (không có giá) và **được commit vào git** — một cam kết công khai, có dấu thời gian, rằng holdout không bị sửa về sau (sửa đổi ở P0-09);
   - `src/quantcrucible/holdout/` = **code** (`evaluator_proc.py`, `campaign.py`); không module nào khác import nó (import-linter).
3. **Các thư mục dữ liệu ở gốc được neo trong `.gitignore`** (`/data/`, `/holdout/`, `/results/`) để các package code `src/quantcrucible/data/` và `…/holdout/` không bị bỏ qua.
4. **Holdout chỉ-đọc trên Windows:** đặt thuộc tính read-only và một ACE `icacls` cấm ghi cho user hiện tại; trên POSIX dùng `chmod 0400`. Bảo vệ ở mức OS chỉ là cố gắng tối đa — **SHA256 trong `holdout.lock`**, được tiến trình đánh giá kiểm tra trước mỗi lần dùng, mới là cơ chế phát hiện sửa đổi thực sự (§4.2 lớp 2).

## Hệ quả

- `pip install -e .` / `uv sync` cho ra một package import được bình thường; test import package đã cài, không vô tình import cây làm việc.
- §5 của kiến trúc giờ khác với cây code: một dòng ghi chú dưới §5 (VI và EN) trỏ về đây. [04-BAN-DO-MODULE.md](../04-BAN-DO-MODULE.md) giữ cây thư mục cập nhật.
- Ví dụ template ở §3.3.1 (`from core.strategy.base …`) trong code sẽ thành `from quantcrucible.core.strategy.base …`; đoạn code trong kiến trúc chỉ mang tính minh họa và giữ nguyên.

## Các phương án đã cân nhắc

- **Bố cục phẳng như §5** — loại: tên cấp cao nhất quá chung (`core`, `data`, `config`), và không có ranh giới cài đặt.
- **Giữ `evaluator_proc.py` trong thư mục dữ liệu** — loại: buộc công cụ và agent phải vào thư mục holdout, nơi mà hook bảo vệ phải chặn hoàn toàn.
