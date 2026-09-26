# 02 — Quy ước

Quy tắc cho mọi code trong `src/`, `tests/` và `scripts/`. Công cụ cưỡng chế được gì thì cưỡng chế (`pyproject.toml`); phần còn lại do review.

## Bố cục & công cụ

- Python **3.12**, quản lý bằng **uv** (`uv sync`, `uv add <pkg>`, commit cả `uv.lock`).
- src layout: `src/quantcrucible/…`. Module đặt ở đâu và được import gì: [04-BAN-DO-MODULE.md](04-BAN-DO-MODULE.md) (cưỡng chế bằng import-linter).
- `ruff` (lint + format, độ dài dòng 100), `mypy --strict` trên `src`, `tests`, `scripts`. Không dùng `# type: ignore` nếu không có chú thích lý do.
- CI chạy đúng các lệnh trong [CLAUDE.md](../CLAUDE.md). Task chưa xong khi các lệnh đó chưa đạt.

## Kiểu & hợp đồng

- Hợp đồng là `Protocol`; đối tượng giá trị là `@dataclass(frozen=True, slots=True)`. Kiểm tra bất biến trong `__post_init__` (ví dụ `Signal.strength ∈ [0,1]`).
- Tên khớp với kiến trúc: `Signal`, `EvaluationReport`, `GateResult`, `portfolio_hash`, `campaign_id`, tên bảng/cột ở §4. Mã cổng: `g0_drift_static`, `g1a_static`, `g0_drift_trace`, `g1b_dynamic`, `g2_minbtl`, `g3_is`, `g4_pbo`, `g5_dsr`, `g6p_robustness`, `g6_holdout`, `g7_dryrun`.
- Không để `dict[str, Any]` đi qua ranh giới module, trừ chỗ kiến trúc lưu JSON (`params`, `detail`).

## Thời gian, dữ liệu, con số

- **Timestamp luôn có múi giờ, dùng UTC** (`datetime.now(UTC)`); ruff `DTZ` cấm datetime không múi giờ. Bar được đánh chỉ số theo thời điểm **đóng** bar.
- **Tính nhân quả:** giá trị tại thời điểm *t* chỉ được dùng dữ liệu có timestamp ≤ *t*. Mọi indicator đi kèm test nhân quả chung (P0-04).
- Tín hiệu từ bar *t* được khớp ở bar *t+1* (P0-10). Không bao giờ giao dịch trên chính bar sinh ra tín hiệu.
- Float cho thống kê và lợi nhuận. `Decimal` chỉ dùng ở ranh giới với sàn (`round_to_lot`, khối lượng lệnh, giá gửi sang Nautilus).
- Lợi nhuận là lợi nhuận đơn trừ khi tên hàm có `log_`. Hệ số năm hóa phải ghi rõ (`periods_per_year`), không bao giờ âm thầm suy ra.

## Tính tất định & tái lập

- Không dùng RNG toàn cục. Mọi thứ ngẫu nhiên nhận `seed: int` (hoặc một `numpy.random.Generator`) và seed được ghi vào ledger (cột `seed`).
- `strategy_hash` = SHA256 của code **đã chuẩn hóa** (AST dump bỏ comment/khoảng trắng), để việc format lại không thành một trial mới.
- Một backtest tái lập được từ dòng ledger của nó: hash code, params, universe, timeframe, timerange, seed, hash lock.

## Lỗi & logging

- Cổng **fail closed**: mọi exception ⇒ `REJECT` + dòng ledger có traceback trong `detail`. Không bao giờ nuốt exception trong code validation.
- Ném exception cụ thể (`HoldoutAccessError`, `LockMismatchError`, `LedgerAppendOnlyError`…), định nghĩa cạnh code ném ra nó.
- Chỉ dùng `logging` của stdlib (`logger = logging.getLogger(__name__)`); `print` bị cấm trong `src/` (ruff `T20`). Log không phải kênh dữ liệu — mọi thứ hệ thống dựa vào để ra quyết định đều đi qua ledger.
- Không bao giờ log giá trị holdout, log metric `private` vào bất cứ đâu bộ dựng prompt đọc được, hay log bí mật.

## Bí mật & cấu hình

- API key chỉ qua biến môi trường / `.env` (đã gitignore). Không bao giờ để trong `config/user.yaml`, trong ledger, hay truyền vào sandbox.
- Tham số nghiên cứu lúc chạy **chỉ** lấy từ `evaluation.lock.yaml` của campaign — không đọc thẳng từ `user.yaml`, không lấy từ hằng số trong code.

## Test

- pytest; `tests/` phản chiếu `src/quantcrucible/` (`tests/ledger/test_db.py` ↔ `ledger/db.py`). Test đầu-cuối ở `tests/e2e/`.
- Marker: `slow`, `docker` (cần Docker daemon), `network` (gọi internet — không bao giờ chạy trên CI). CI chạy `-m "not docker and not network and not slow"`; chạy test docker trên máy trước khi merge thay đổi sandbox.
- **Code thống kê được test với giá trị tham chiếu đã công bố** (ví dụ số trong paper) và tính chất mô phỏng (ví dụ PBO ≈ 0.5 trên nhiễu) — không bao giờ so với con số chính code đó tạo ra.
- Bất biến an toàn có test cố **phá** chúng (DELETE bằng SQL thô, lật một byte ở vùng cố định, mở holdout lần hai).
- Test không bao giờ chạm vào `holdout/`, `data/` hay ledger thật: dùng fixture `tmp_path` và dữ liệu tổng hợp.

## Dependency

Chỉ thêm dependency trong task cần đến nó, kèm:
1. khoảng phiên bản được ghim trong `pyproject.toml` (`uv add 'pkg>=x.y,<x.(y+1)'` cho thư viện 0.x);
2. kiểm tra license — **không AGPL/GPL** trong `src/` (ví dụ loại `pypbo`); đánh dấu Commons Clause (`vectorbt`: chỉ để sàng lọc thô, không bao giờ nằm trong đường validation);
3. kiểm tra API với source đã cài trước khi bọc lại (các đoạn code trong kiến trúc chưa được kiểm chứng, §10);
4. một module bọc mỏng để phần còn lại của code không import trực tiếp (ví dụ `validation/statistical.py` bọc `purgedcv`).

SDK của LLM chỉ được import dưới `quantcrucible.agent` (A4; `tests/test_architecture_boundaries.py`).

## Front-end (`ui/`, ADR-0029)

- TypeScript strict, không dùng `any` trong code ứng dụng; payload API được khai kiểu một lần trong `src/lib/types.ts`.
- Mỗi file một component; `src/lib` giữ phần fetch và format, `src/components` giữ các mảnh dùng chung, `src/screens` mỗi tab một file.
- Phiên bản npm phải **chính xác** (không `^`, không `latest`) và license MIT/Apache-2.0 — cùng quy tắc với dependency Python.
- Chữ hiển thị bằng tiếng Việt; dữ liệu thiếu đọc là `Chưa có` và không bao giờ được vẽ thành `0`.
- `npm run build` kiểm kiểu, `npm test` chạy Vitest, `npm run test:e2e` chạy Playwright trên `scripts/review_demo_server.py` — không bao giờ trên ledger thật.

## Git

- Mỗi task một nhánh: `p0-02-ledger`, `p1-06-sizing`, `chore/…`, `docs/…`.
- Commit message: dòng tiêu đề dạng mệnh lệnh ≤ 72 ký tự, ghi mã task (`P0-02: add append-only triggers`).
- Không có dòng `Co-Authored-By` hay bất kỳ dòng ghi công AI nào trong commit hoặc mô tả PR.
- Chỉ commit/push khi người dùng yêu cầu.
