# ADR-0004 — Sandbox Docker cho code được sinh ra

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P0-08 · **Kiến trúc:** §3.3.3, A6 · **Vấn đề mở:** O6

## Bối cảnh

§3.3.3 yêu cầu mọi đoạn code được sinh ra chạy với môi trường rỗng, không mạng, dữ liệu IS chỉ-đọc, chỉ ghi vào thư mục tạm, không thấy `holdout/`, `config/`, `ledger/`, có timeout cùng giới hạn RAM và CPU, và đầu ra chỉ trả về dưới dạng báo cáo JSON với stdout/stderr bị cắt ngắn. Trên Windows (A6) kiến trúc bảo dùng container, không tự viết sandbox bằng Python. Cách ghép các phần — code nào chạy bên trong, dữ liệu vào bằng cách nào, thế nào là vi phạm, và mỗi lần chạy tốn bao nhiêu (O6) — còn bỏ ngỏ.

## Quyết định

1. **Image:** `docker/sandbox.Dockerfile` cài các dependency không-dev đã khoá cùng package vào `/opt/venv` trên `python:3.12-slim`, chạy bằng user không phải root (uid 10001). Build context là danh sách cho phép (`.dockerignore`): `pyproject.toml`, `uv.lock`, `README.md`, `src/` — không dữ liệu, kết quả, ledger, config hay file lock. Tag là `quantcrucible-sandbox:<hash>` trên Dockerfile, metadata dự án, lockfile và `src/` (chuẩn hoá CRLF), nên đổi code là build image mới (`ensure_image`).
2. **Runner bên trong:** `validation/sandbox_runner.py` đọc `/job/in/{strategy.py, job.json, data/*.parquet}`, chạy một job (`signals`, `backtest`; `leak_check` đến cùng ①b ở P0-11) và ghi `/job/out/report.json`. Mọi thất bại, kể cả `SystemExit`, đều thành báo cáo lỗi. Một contract import-linter giữ ledger, config, gate và code sandbox phía host ra khỏi nó.
3. **Môi trường rỗng:** entrypoint là `env -i /opt/venv/bin/python -I -B -m …`. Chặt hơn "`PYTHONPATH` tối thiểu" của §3.3.3: package đã được cài nên không cần đường dẫn nào. Python tự đặt `LC_CTYPE` lúc khởi động (PEP 538); không có gì kế thừa từ host.
4. **Cờ container:** `--rm --network none --read-only --tmpfs /tmp (256 MB) --memory/--memory-swap 2g --cpus 1 --pids-limit 256 --cap-drop ALL --security-opt no-new-privileges --ipc none`; trên host Linux thêm `--user <uid>:<gid>`. Đúng hai mount: `in/` của job (chỉ-đọc) và `out/` (đọc-ghi), cả hai trong một thư mục tạm mới, bị xoá sau khi chạy.
5. **Dữ liệu vào:** host chỉ ghi lát IS của job vào `in/data/` — bản thân thư mục dữ liệu không bao giờ được mount.
6. **Đầu ra:** stdout và stderr được các thread đọc hết, mỗi luồng chỉ giữ 4 KB đầu (đầu ra vô tận không thể làm đầy bộ nhớ host); báo cáo phải là JSON và tối đa 64 MB.
7. **Vi phạm:** timeout (container bị `docker kill`), mã thoát 137 (giới hạn bộ nhớ hoặc PID), thiếu báo cáo, hoặc ngoại lệ của strategy là `OSError` (nó đụng vào mạng hay hệ thống file) → `SandboxResult.violation`. Gate biến nó thành một lần bác bỏ bằng `sandbox_failure()`, ghi log là `SANDBOX_VIOLATION`; ngoại lệ thông thường trong strategy giữ sự kiện bác bỏ riêng của gate.
8. **Chi phí (O6), đo trên Windows 11 + Docker Desktop (WSL2):** khởi động container ≈ 0,6 s; một job điển hình 2,5–4 s. Import NautilusTrader (≈ 2 s) chỉ xảy ra trong job `backtest`. Đủ cho GĐ 0.

## Hệ quả

- Code được sinh ra chỉ được thực thi bởi runner bên trong container; `load_strategy_class` trên host vẫn chỉ dùng cho mã nguồn tin cậy (zoo, test).
- CI có thêm job thứ hai, `sandbox`, build image và chạy `pytest -m docker` (INV-36).
- Build image từ đầu mất khoảng 2,5 phút; thay đổi chỉ ở mã nguồn thì dùng lại layer dependency.

## Phương án đã cân nhắc

- **Container worker sống lâu**, mỗi job một thư mục tạm mới: hoãn đến GĐ 2, nếu khởi động mỗi job thành nút thắt; các giới hạn giữ nguyên.
- **gVisor / `runsc`:** cô lập mạnh hơn, nhưng không có trên Docker Desktop cho Windows.
- **Sandbox mức Python (hạn chế builtins, audit hook):** bị §3.3.3 loại và dễ thoát ra.
