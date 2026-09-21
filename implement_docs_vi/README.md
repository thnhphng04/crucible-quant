# Tài liệu triển khai

Cách **xây dựng** Crucible Quant. Bản thân thiết kế — xây cái gì và vì sao — nằm trong tài liệu kiến trúc ([VI, bản gốc](../research_docs_vi/Architecture_Design.md) · [EN](../research_docs/Architecture_Design.md)). Bộ tài liệu này không chép lại kiến trúc; nó trỏ tới các mục (`§3.4`) và chỉ bổ sung những gì kiến trúc để ngỏ: thứ tự task, test nghiệm thu, quy ước code, và các quyết định ở mức triển khai.

Bản tiếng Anh ([implement_docs/](../implement_docs/README.md)) là bản gốc; thư mục này là bản dịch tiếng Việt 1:1 (cùng số dòng và heading, được kiểm tra bằng `scripts/check_doc_mirror.py`).

| File | Dùng khi |
|---|---|
| [01-LO-TRINH-TASK.md](01-LO-TRINH-TASK.md) | Chọn task tiếp theo. GĐ 0–1 đã chia thành task kèm test nghiệm thu; GĐ 2–6 là các mốc, sẽ chia nhỏ khi bắt đầu giai đoạn |
| [02-QUY-UOC.md](02-QUY-UOC.md) | Viết bất kỳ đoạn code nào: bố cục, kiểu, tính tất định, thời gian, tiền, lỗi, test, dependency |
| [03-BAN-DO-BAT-BIEN-TEST.md](03-BAN-DO-BAT-BIEN-TEST.md) | Kiểm tra một quy tắc của kiến trúc có thực sự được cưỡng chế không — mỗi bất biến → cơ chế → test |
| [04-BAN-DO-MODULE.md](04-BAN-DO-MODULE.md) | Quyết định code đặt ở đâu và được import những gì |
| [05-VAN-DE-MO.md](05-VAN-DE-MO.md) | Gặp điều chưa biết. Mỗi mục ghi rõ task nào sẽ giải quyết nó |
| [adr/](adr/) | Ghi lại một quyết định triển khai (`/new-adr`). Quyết định ở mức kiến trúc vẫn nằm trong sổ quyết định §10 |

Hướng dẫn cho agent: [../CLAUDE.md](../CLAUDE.md). Lệnh hỗ trợ trong Claude Code: `/phase-check`, `/sync-docs`, `/new-adr` (trong `.claude/commands/`).

## Giữ tài liệu trung thực

- Khi một task hoàn thành: đánh dấu ở `01`, điền tên test vào `03`, đóng mục `05` mà nó giải quyết — trong cùng một thay đổi.
- Mọi chỉnh sửa phải có ở cả hai ngôn ngữ trong cùng một commit: sửa bản tiếng Anh trước, rồi `implement_docs_vi/` theo từng dòng.
- Một test được nêu tên trong `03` phải tồn tại và chạy đạt thì dòng đó mới được đánh ✅.
- Nếu khi triển khai thấy kiến trúc sai, đừng vá lách: mở một mục trong `05` và báo cho người dùng.
