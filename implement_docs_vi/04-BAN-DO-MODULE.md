# 04 — Bản đồ module

Code đặt ở đâu và được import những gì. Cây thư mục là §5 của kiến trúc chuyển sang src layout ([ADR-0001](adr/0001-src-layout-va-tach-holdout.md)). Phần lớn module bên dưới chưa tồn tại — cột cuối là task sẽ tạo ra chúng.

## Cây thư mục

```
TradingProject/
├── CLAUDE.md                      hướng dẫn cho agent
├── config/
│   ├── user.yaml                  file duy nhất người dùng sửa (§10.1)
│   └── evaluation.lock.yaml       sinh theo campaign — gitignore, chỉ-đọc, có hash
├── holdout/                       🔴 CHỈ DỮ LIỆU — gitignore, chỉ-đọc, chỉ tiến trình đánh giá được đọc
├── holdout.lock                   SHA256 của các file holdout (§4.2 lớp 2)
├── data/                          cache parquet của dữ liệu miễn phí — gitignore
├── docker/                        image sandbox (dự kiến, P0-08)
├── scripts/                       công cụ dev (check_doc_mirror.py)
├── src/quantcrucible/
│   ├── cli.py                     data-fetch (tải + cắt holdout), holdout-reharden        P0-09
│   ├── config/                    loader, schema, khóa campaign                        §10.1   P0-03
│   ├── core/                      ── tầng đáy ──
│   │   ├── strategy/              base (Signal, Strategy), registry (ind), template,    §3.3    P0-04/05
│   │   │                          tunable
│   │   ├── sizing/                vol_target, position_sizer                           §3.4    P1-06
│   │   └── zoo/                   chiến lược kinh điển để so độ giống AST              §3.1.6  GĐ 2
│   ├── data/                      protocol DataSource, nguồn ccxt/Stooq, store,        §6.1    P0-09
│   │                              holdout_split
│   ├── ledger/                    schema.sql, db.py                                    §4      P0-02
│   ├── validation/
│   │   ├── gates.py, report.py    pipeline cổng, EvaluationReport                      §3.2    P0-06
│   │   ├── guardrail.py           cổng ①a tĩnh + ①b động                               §3.2    P0-07/11
│   │   ├── sandbox.py             bộ chạy Docker (host) + sandbox_runner.py (trong container) §3.3.3 P0-08
│   │   ├── statistical.py         MinBTL, DSR, PBO, CPCV (bọc purgedcv)                §3.2    P0-12, P1-0x
│   │   ├── n_eff.py               phân cụm ONC → N_eff                                 §4.1    P1-05
│   │   ├── portfolio.py           xây danh mục §3.2.1 + portfolio_hash                 §3.2.1  P1-07
│   │   ├── portfolio_dsr.py       cổng ⑤                                               §3.2    P1-08
│   │   ├── temporal.py            theo dõi suy giảm                                    §5      sau
│   │   └── oracles/               bộ leaky oracle, cấp 1–4                             07 §9   P0-11
│   ├── agent/                     ── NƠI DUY NHẤT gọi LLM (A4) ──                      §3.1    GĐ 2
│   │   ├── loop.py, data_agent.py, research_agent.py, coding_team.py, evaluation_team.py
│   │   ├── evolution/             feature_map, islands, sampling, archive
│   │   ├── engines/               quantevolve, simple_loop, random_search              §3.1.11
│   │   ├── dsl.py, drift.py, patcher.py, pipeline.py, routing.py, scheduler.py, insights.py
│   │   └── prompts/
│   ├── holdout/                   evaluator_proc.py (tiến trình riêng), campaign.py    §4.2    P1-10
│   └── execution/                 engine.py (NautilusTrader), nautilus_bridge.py       §3.5    P0-10
│       └── adapters/ssi/          adapter VN30F1M                                      §3.5    GĐ 6
└── tests/                         phản chiếu src/quantcrucible/; e2e/ cho cổng giai đoạn
```

## Quy tắc phụ thuộc

| Package | Được import (nội bộ) |
|---|---|
| `core` | — (tầng đáy) |
| `ledger` | — |
| `data` | `core` |
| `config` | `ledger` |
| `execution` | `core`, `data` |
| `validation` | `core`, `data`, `ledger`, `config`, `execution` |
| `agent` | mọi thứ trừ `holdout` |
| `holdout` | `core`, `data`, `ledger`, `config`, `execution`, `validation` — và **không ai import nó** |

Các hướng bị cấm được cưỡng chế bằng contract import-linter trong `pyproject.toml`:

| Contract | Lý do |
|---|---|
| `core` không import gì từ `agent`, `validation`, `execution`, `ledger`, `holdout` | Strategy và sizing độc lập với sàn và với phần nghiên cứu (P3, §3.3 "ranh giới kiến trúc cứng") |
| `validation` không bao giờ import `agent` | Harness chấm điểm agent; nó không được phụ thuộc vào agent (P1) |
| `execution` không bao giờ import `agent`, `validation`, `holdout` | Đường live phải chính là đường backtest, không kèm bộ máy nghiên cứu (P5) |
| Không ai import `holdout` | Chỉ tới được tiến trình đánh giá bằng cách khởi chạy nó (§4.2 lớp 3) |
| `data` không import gì từ `agent`, `validation`, `execution` | Nguồn dữ liệu thay thế được (§6.1 `DataSource`) |
| `validation.sandbox_runner` không import `ledger`, `config`, `agent`, `holdout`, code gate hay code sandbox phía host | Nó chạy trong container cùng code sinh ra; chỉ có thể trả về một báo cáo (§3.3.3) |
| `ledger` không import package `quantcrucible` nào khác; `config` chỉ import `ledger` | Ledger là nguồn sự thật duy nhất (P2) và không được phụ thuộc vào thứ nó ghi lại |

Thêm một test AST cho import bên thứ ba: SDK của LLM (`openai`, `anthropic`, `litellm`, `langchain`, `langgraph`, …) chỉ nằm dưới `agent/` (A4).

Khi một module mới cần một import mà các quy tắc này cấm, nhiều khả năng thiết kế đang sai — hãy nêu ra trước khi thêm ngoại lệ.

## Ranh giới tiến trình lúc chạy

| Tiến trình | Thấy | Không bao giờ thấy |
|---|---|---|
| Nghiên cứu (vòng agent + validation ⓪–⑥′) | dữ liệu IS, ledger, lock | `holdout/` (loader từ chối khoảng đó; hook chặn coding agent) |
| Container sandbox (code được sinh) | dữ liệu IS (chỉ-đọc), thư mục tmp | mạng, biến môi trường, `holdout/`, `config/`, `ledger/` |
| Tiến trình đánh giá holdout | `portfolio_hash` đã đóng băng, dữ liệu holdout, ledger | bộ nhớ của vòng agent; trả về 1 token |
| Live (cổng ⑧) | `core` + `execution` + các strategy đã đóng băng | LLM, `agent/`, `validation/` |
