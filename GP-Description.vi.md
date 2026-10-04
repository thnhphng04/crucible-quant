# Engine C-gp — giải thích từng tham số

> Tài liệu tham khảo cá nhân, viết theo code trên `main` tại commit `0860052` (đã có P3-53 / ADR-0041).
> Không phải nguồn sự thật: thiết kế nằm ở `research_docs/Architecture_Design.md` (§3.1.3–§3.1.11),
> quyết định D20 và các ADR-0024/0026/0029. Khi code đổi, file này có thể lỗi thời.

## 0. Bức tranh chung

C-gp là **Genetic Programming có kiểu** (typed GP). Mỗi cá thể là một *genome*: cây quy tắc nhỏ gồm
quy tắc vào lệnh, stop và (tuỳ chọn) take-profit. Genome được render thành code chiến lược
có các `TUNABLE`. Quần thể chia thành **5 đảo**, mỗi đảo có **archive MAP-Elites** riêng. Mỗi lần
gọi `next()` sinh **một** đề xuất cho **một** đảo. Engine **không bao giờ tự đánh giá**: nó chỉ đề
xuất, còn pipeline gate ①a → ④ chấm. Mọi trạng thái (quần thể, điểm, di cư, đã đề xuất gì) được
dựng lại từ **ledger** ở mỗi bước, nên chạy lại sau khi dừng sẽ tiếp tục đúng chỗ cũ.

| Thành phần | File |
|---|---|
| Vòng lặp engine | [src/quantcrucible/agent/engines/gp_search.py](src/quantcrucible/agent/engines/gp_search.py) |
| Grammar, lấy mẫu genome | [src/quantcrucible/agent/grammar.py](src/quantcrucible/agent/grammar.py) |
| Kiểu genome, render code | [src/quantcrucible/core/strategy/genome.py](src/quantcrucible/core/strategy/genome.py) |
| Toán tử di truyền | [src/quantcrucible/agent/evolution/operators.py](src/quantcrucible/agent/evolution/operators.py) |
| Chọn cha / mate | [src/quantcrucible/agent/evolution/sampling.py](src/quantcrucible/agent/evolution/sampling.py) |
| Đảo, di cư | [src/quantcrucible/agent/evolution/islands.py](src/quantcrucible/agent/evolution/islands.py) |
| Archive MAP-Elites | [src/quantcrucible/agent/evolution/archive.py](src/quantcrucible/agent/evolution/archive.py) |
| Feature map | [src/quantcrucible/agent/evolution/feature_map.py](src/quantcrucible/agent/evolution/feature_map.py) |
| Điểm xếp hạng | [src/quantcrucible/agent/evolution/ranking.py](src/quantcrucible/agent/evolution/ranking.py) |
| Nối config → engine | [src/quantcrucible/agent/run.py](src/quantcrucible/agent/run.py) |

Tham số chia làm 3 nhóm theo **chỗ bạn đổi được**:

1. **Cấu hình campaign** — trong `config/user.yaml`, khoá theo campaign (mục 1).
2. **Suy ra từ lock** — không đặt trực tiếp, được tính khi campaign mở (mục 2).
3. **Hằng số trong code** — muốn đổi phải sửa code (+ ADR nếu là quyết định triển khai) (mục 3–9).

---

## 1. Tham số cấu hình campaign (`config/user.yaml`, nhóm `research`)

Các khoá này được chép vào `config/evaluation.lock.yaml` khi campaign mở. Đổi giữa campaign bị từ chối.

### `research.engines.gp` — mặc định `0.5`
Tỷ trọng của engine GP trong ngân sách trial. Với `trial_budget: 600` và `gp: 0.5, random: 0.5`,
GP nhận khoảng 300 trial, chia tiếp cho các seed và các scope (instrument × direction).
`random` (C-random) là baseline lấy mẫu i.i.d. từ **cùng grammar** — GP phải thắng nó mới có giá trị.

### `research.gp.param_only_max` — mặc định `0.30`, ràng buộc `[0, 1]` (D20)
Tỷ lệ tối đa con cháu **chỉ đổi giá trị tham số** (code render ra trùng `strategy_hash` với cha).

- Tại sao cần: một con chỉ đổi tham số là *biến thể của cùng một code*. Nếu để không giới hạn,
  GP sẽ thoái hoá thành một bộ tối ưu tham số trá hình — điều kiến trúc cấm trong vòng tiến hoá
  (§3.3.1: không optimizer trong evolution loop).
- Áp ở **hai chỗ**:
  1. `choose_kind`: chỉ chọn toán tử `param` khi `param_only + 1 ≤ param_only_max × (children + 1)`.
  2. `_accept`: kiểm tra lại bằng **code đã render** chứ không tin nhãn toán tử — vì `subtree`
     có thể thay một chỉ báo bằng chính nó với period khác, ra cùng code ⇒ cũng là param-only.
- `0` ⇒ không bao giờ có con chỉ đổi tham số; `1` ⇒ không giới hạn.

### `research.gp.plateau_threshold` — mặc định `0.5`, ràng buộc `(0, 1]` (D20)
GP **không** đọc trực tiếp. Gate ④ (PBO) dùng nó khi chạy lưới cấu hình quanh ứng viên:
`plateau` = tỷ lệ cấu hình hàng xóm có Sharpe IS ≥ `plateau_threshold × Sharpe ứng viên`.
`plateau` sau đó được ghi vào metric `public` và quay lại làm **điểm cộng** trong ranking (mục 8).
Ngưỡng cao hơn ⇒ khó đạt plateau hơn ⇒ ranking thưởng ít ứng viên hơn.

### `research.seeds` — mặc định `3`
Số lần chạy GP độc lập. Mỗi `(engine, seed, instrument, direction)` là một **đơn vị tìm kiếm**
(`Key`) với RNG riêng (`engine_seed` băm từ các thành phần đó) và archive riêng. Nhiều seed giúp
phân biệt "GP tốt" với "GP gặp may".

### `research.engine_mode` — mặc định `isolated`
`isolated`: mỗi engine/seed chỉ nhìn ledger của chính mình, không dùng chung archive.

### `research.campaign.trial_budget` — ví dụ `600`
Trần số trial của campaign. Bắt buộc phải có để chạy evolve.

### `research.exit.tp_sl_ratio` — mặc định `1.1`
Khi campaign dùng exit protocol `bracket_timeout_v1`, take-profit luôn bằng
`tp_sl_ratio × stop` (cố định cho cả campaign, **không** phải gene). Xem mục 2.

### `research.exit.stop_kinds` — mặc định `[atr]` (D25, ADR-0042)
Các loại stop genome được dùng. `[atr, bollinger]` bật lại stop Bollinger cho campaign mới. Tắt
mặc định vì P3-54 đo được stop Bollinger `<= 0` (guard `ready` chặn tín hiệu) đúng lúc clause mean
reversion kích hoạt. Gate ①a từ chối mọi `ind.boll_*` khi lock không cho phép. Lock cũ thiếu khoá
giữ loại stop đã chạy (bracket: cả hai).

### `research.exit.max_holding_bars` — mặc định `100`
Thoát theo thời gian của bracket. Không thuộc genome, GP không tiến hoá nó.

---

## 2. Tham số suy ra từ lock (`grammar_config` trong [run.py](src/quantcrucible/agent/run.py))

### `boll_stop_probability` — `0.5` nếu exit là `bracket_timeout_v1` **và** lock cho phép `bollinger`, ngược lại `0.0`
Xác suất genome mới dùng **stop Bollinger** thay cho **stop ATR**. Từ P3-55 (ADR-0042) campaign
mới mặc định `stop_kinds: [atr]` ⇒ xác suất `0`; lock cũ thiếu khoá vẫn `0.5` (bracket).

- ATR: `stop = k_stop × ATR(14)` — lock `stop_period: tunable_v1`: `k_stop × ATR(n_stop)`.
- Bollinger: `stop = k_stop × (close − boll_lower(close, 8))` cho long,
  `k_stop × (boll_upper(close, 8) − close)` cho short — lock `tunable_v1`: period dải là `n_stop`.

Khác 0 cũng mở khoá nhánh đổi kiểu stop trong toán tử `point` (mục 4).

### `tp_sl_ratio` — `research.exit.tp_sl_ratio` nếu bracket, ngược lại `None`
Khi khác `None`: TP render ra `tp_sl_ratio * stop` và **thắng** mọi gene `k_tp`.

### `direction` — `long` / `short` theo scope (`long` cho key legacy)
Code render ra chỉ phát tín hiệu theo đúng phía này hoặc đứng ngoài (INV-91).

### `stop_period` và `max_params` — từ `derived.stop_period` / `derived.max_tunables` (ADR-0041)
Lock `stop_period: tunable_v1` (mọi campaign mở từ P3-53): mọi genome mang gene `n_stop`, trần
TUNABLE là **7**. Lock cũ không có khoá ⇒ không có gene, trần **6**. Cả C-gp và C-random đều đọc.

### Feature map — `derived.feature_map` trong lock
Đọc từ hằng `FEATURE_MAP` trong [validation/run.py](src/quantcrucible/validation/run.py), chép vào
lock khi mở campaign. Chi tiết ở mục 7.

---

## 3. Grammar — `GrammarConfig` và các khoảng giá trị

### 3.1 Các trường của `GrammarConfig`

| Trường | Mặc định | Ý nghĩa |
|---|---|---|
| `clause_count_weights` | `(0.5, 0.35, 0.15)` | Xác suất quy tắc vào lệnh có 1 / 2 / 3 clause. Thiên về quy tắc đơn giản |
| `or_probability` | `0.3` | Khi ≥ 2 clause: xác suất ghép bằng `or` (ngược lại `and`). `and` làm tín hiệu thưa, `or` làm dày |
| `take_profit_probability` | `0.0` | Xác suất sinh gene `k_tp`. Đang tắt: chế độ bracket dùng `tp_sl_ratio`, chế độ cũ thì execution bỏ qua TP (O19) — một `k_tp` khi đó là TUNABLE vô tác dụng |
| `boll_stop_probability` | `0.0` (bracket + `bollinger` trong `stop_kinds`: `0.5`) | Xem mục 2 |
| `tp_sl_ratio` | `None` | Xem mục 2 |
| `direction` | `"long"` | Xem mục 2 |
| `stop_period` | `False` (lock `tunable_v1`: `True`) | Mỗi genome có gene `n_stop` — period của stop (mục 3.3, 3.4) |
| `max_params` | `6` (lock `tunable_v1`: `7`) | Trần số TUNABLE duy nhất. Genome vượt trần bị lấy mẫu lại; con vượt trần bị toán tử bỏ |
| `clause_types` | cả 7 loại (lock v5: 9) | Loại clause được phép lấy mẫu. Mỗi đảo có bản riêng khi seed (mục 6) |

### 3.2 Bảy loại clause

TUNABLE được đặt tên theo loại: period → `n1, n2…`, level → `lv1…`, hệ số → `k1…`;
stop luôn là `k_stop`, period của stop là `n_stop` (khi có gene), TP là `k_tp`.

| Clause | Dạng code | Số | Danh mục (`clause_category`) |
|---|---|---|---|
| `Compare(a, op, b)` | `a > b` / `a < b` | period của a, b (nếu là chỉ báo) | trend |
| `Cross(a, up, b)` | `cross_up(a, b)` / `cross_down` | period của a, b | trend |
| `Threshold(osc, op, lv)` | `rsi(n) > lv` | period + level | `>` momentum, `<` mean_reversion |
| `CrossLevel(osc, up, lv)` | `cross_up(rsi(n), lv)` | period + level | up momentum, down mean_reversion |
| `Distance(a, b, op, k)` | `(a − b)/ATR > k` | period a, b + k | `>` trend, `<` mean_reversion |
| `Breakout(up, n)` | `close > rolling_max(close, n).ago(1)` | period | up breakout, down mean_reversion |
| `Slope(s, up)` | `s > s.ago(1)` | period của s | oscillator → momentum, giá → trend |

Cột danh mục trên là **grammar v1** (lock cũ). Từ **grammar v2** (P3-56, ADR-0043, lock mới có
`derived.grammar_version: 2`) danh mục tính theo hướng của scope:

- **Họ:** `Compare`, `Cross`, `Distance`, `Slope` trên giá → trend; `Threshold`, `CrossLevel`,
  `Slope` trên oscillator → momentum; `Breakout` → breakout.
- **Cực:** +1 nếu clause đúng khi giá đi lên (`>`, `up`), −1 nếu ngược lại. Clause hai chuỗi được
  đọc theo "nhanh − chậm" (`close` nhanh nhất, period nhỏ hơn nhanh hơn, cùng period thì `ema`
  trước `sma`), nên `a > b` và `b < a` có cùng cực.
- **Danh mục** = họ nếu cực × hướng > 0 (long = +1, short = −1), ngược lại là `mean_reversion`.

Ví dụ: `rsi > 70` → long momentum, short mean_reversion; `Breakout(down)` → long mean_reversion,
short breakout; `close < sma(200)` → long mean_reversion, short trend. Đảo có danh mục chỉ seed
clause đúng danh mục của nó (đảo mean_reversion dùng cả 7 loại clause).

Toán hạng (công thức và khoảng period của từng chỉ báo ở mục 3.4):
- **Chuỗi giá**: `close` (xác suất `0.3`), còn lại `sma` hoặc `ema` (mỗi cái 0.35). Hai chuỗi giá
  của một clause phải khác nhau và không được cùng là `close`.
- **Oscillator**: `rsi` hoặc `zscore`, đều 0.5.
- `Slope`: 60% chuỗi giá, 40% oscillator; nếu bốc trúng `close` thì đổi thành `ema`.
- Hướng (`up`) và phép so sánh (`>`/`<`) đều 50/50.

Mọi so sánh đều **bất biến thang giá**: giá so với giá, oscillator so với mức, khoảng cách giá chia
ATR. Code luôn có guard `ready` trên ATR và stop ⇒ mọi genome qua gate ①a ngay từ cấu trúc.

### 3.3 Khoảng giá trị (mọi số được lấy đều trong khoảng)

| Hằng | Giá trị | Áp cho |
|---|---|---|
| `PERIOD_RANGE` | `(2, 300)` | Period, rồi **giao** với khoảng riêng của toán tử |
| — sma, ema, rolling_max/min | `[2, 300]` | |
| — rsi | `[2, 100]` | |
| — zscore | `[5, 300]` | |
| `LEVEL_RANGES[rsi, >]` | `[50, 90]` | Ngưỡng RSI phía trên |
| `LEVEL_RANGES[rsi, <]` | `[10, 50]` | Ngưỡng RSI phía dưới |
| `LEVEL_RANGES[zscore, >]` | `[0, 3]` | |
| `LEVEL_RANGES[zscore, <]` | `[-3, 0]` | |
| `DISTANCE_RANGE` | `[-3, 3]` | `k` của `Distance`, đơn vị ATR |
| `STOP_RANGE` | `[0.5, 5]` | `k_stop` |
| `TP_RANGE` | `[0.5, 10]` | `k_tp` (đang không dùng) |
| `STOP_PERIOD_RANGE` | `[5, 50]` | `n_stop` (lock `tunable_v1`): period ATR của stop ATR, hoặc period dải của stop Bollinger |
| ATR period của guard / `Distance` | `14` (cố định) | Không phải TUNABLE, ở mọi lock |
| Period stop khi không có gene | ATR `14` / Bollinger `8` | Lock cũ: không phải TUNABLE |

Level và hệ số được làm tròn 4 chữ số. Các bound này **đi theo TUNABLE** vào code, nên toán tử
`param` và lưới PBO ở gate ④ cũng bị kẹp trong đúng khoảng này.

**Grammar v3** (P3-57, ADR-0044, lock mới có `derived.grammar_version: 3`):
- Period của clause lấy **log-uniform** trong bound (`round(exp(U(ln lo, ln hi)))`): trên `[2, 300]`
  chỉ ~22% số lần bốc trên 100 bar, thay vì ~67% khi lấy đều. Bound giữ nguyên.
- `rsi` period giới hạn `[2, 30]` (RSI dài bám quanh 50, không chạm level); `zscore` period
  giới hạn `[10, 300]` (z-score của n giá trị không vượt `(n − 1)/√n`, chỉ 1,79 khi n = 5).
- Toán tử `point` đổi `rsi ↔ zscore` sẽ kẹp period vào khoảng của oscillator mới.
- `n_stop`, level và `k` không đổi.

**Grammar v4** (P3-58, ADR-0045, lock mới có `derived.grammar_version: 4`): `Distance` chia cho
**độ lệch chuẩn của chính spread** thay vì ATR(14):

```
(a − b) / spread_stdev(a, b, n)  op  k        n = period của b (của a nếu b là close)
spread_stdev = √( (1/n) Σ (s_i − s̄)² ),  s = a − b   (= 0 ⇒ NaN ⇒ clause False)
```

- Không thêm TUNABLE; `k = 0` vẫn tương đương `Compare`. `k ∈ [-3, 3]` giữ nguyên.
- Period của toán hạng `Distance` giới hạn `[2, 200]`: warm-up `max(n_a, n_b) + n − 1 ≤ 399` vừa
  trong lookback 400 bar.
- Trên random walk: `Distance` suy biến 17,5% (v3) → 0%; trên dữ liệu IS (P3-54) 1,6%.

**Grammar v5** (P3-61, ADR-0047, lock mới có `derived.grammar_version: 5`): thêm hai clause **chế độ
biến động**, mỗi clause là **một** feature so với mức `k` (giống `Threshold`):

| Clause | Dạng code | Số | Danh mục |
|---|---|---|---|
| `VolRatio(fast, slow, op, k)` | `atr_ratio(bars, fast, slow) > k` | `fast` ∈ `[5, 50]`, `slow` ∈ `[60, 300]`, `k` ∈ `[0.6, 1.6]` | `volatility` (cả long lẫn short) |
| `Bandwidth(fast, slow, op, k)` | `band_ratio(close, fast, slow) > k` | `fast` ∈ `[5, 50]`, `slow` ∈ `[60, 300]`, `k` ∈ `[0.3, 2.5]` | `volatility` |

- `> k`: biến động đang **giãn** so với nền dài; `< k`: đang **co** (squeeze).
- Hai khoảng period rời nhau nên `fast` luôn nhỏ hơn `slow`, kể cả sau toán tử `param`. Period lấy
  log-uniform. Mỗi clause tốn 3 TUNABLE.
- Khoảng `k` lấy từ phân vị q05–q95 trên dữ liệu IS 1d và perp 1h. Tỷ lệ clause suy biến (đúng < 1%
  hoặc > 99% số bar) ở 1d / 1h: `VolRatio` 2,7% / 7,9%, `Bandwidth` 4,1% / 3,5%.
- **Một feature, không phải hai:** cổng ③ loại chiến lược có hai feature mà thay đổi từng bar tương
  quan > `max_indicator_corr` 0,9. `atr(fast)` và `atr(slow)` tương quan 0,80–0,98, nên một tỷ lệ
  viết thành hai feature gần như luôn trượt.
- Danh mục thứ 5 `volatility` ⇒ **6 đảo** (mục 6). Toán tử `point` lật `>`/`<`, giữ nguyên `k`.

**Một thước ATR** (ADR-0047, mọi genome có gene `n_stop` và stop ATR): ATR duy nhất trong code là
`"atr": ind.atr(bars, self.p.n_stop)` — guard `ready`, stop và `k_tp` cùng đọc nó; không còn
`atr_stop` và `ATR(14)`. Lý do: ATR(14) đứng cạnh ATR(n_stop) tương quan 0,94–0,996, làm 39/40 genome
trượt cổng ③. Ngoại lệ: genome có `Distance` chia ATR (không còn được lấy mẫu từ v4) giữ cả hai.

### 3.4 Các chỉ báo (indicator) dùng trong grammar

Mọi chỉ báo nằm trong whitelist `ind` ở [registry.py](src/quantcrucible/core/strategy/registry.py)
(`OPS`). Gate ①a chỉ cho gọi `ind.<tên>` có trong `INDICATORS`. Whitelist có 15 mục (11 ban đầu,
`spread_stdev` từ v4, `bandwidth`/`band_ratio`/`atr_ratio` từ v5); ngoài ra có chuỗi thô `close`
(không phải chỉ báo). Mọi chỉ báo chuỗi đều **nhân quả**
(giá trị tại bar t chỉ phụ thuộc bar ≤ t, kiểm bởi `tests/core/strategy/test_registry.py`), trả NaN
trong giai đoạn warm-up. Tất cả tính trên `bars.close`, trừ `atr`/`atr_ratio` dùng thêm `high`/`low`;
`open` và `volume` không được grammar dùng.

**Chuỗi giá** (đơn vị giá — chỉ so với chuỗi giá khác hoặc chia ATR):

| Chỉ báo | Công thức | Period (TUNABLE) | Warm-up | Dùng ở |
|---|---|---|---|---|
| `close` | Giá đóng cửa thô | — | 0 | Toán hạng giá (xác suất 0.3); vế so sánh của `Breakout` |
| `sma(close, n)` | Trung bình cộng `n` bar | `n` ∈ `[2, 300]` | `n` bar | `Compare`, `Cross`, `Distance`, `Slope` |
| `ema(close, n)` | Làm mượt mũ, `α = 2/(n+1)`, khởi tạo bằng SMA của `n` giá trị đầu | `n` ∈ `[2, 300]` | `n` bar | Như `sma`; thay `close` khi `Slope` bốc trúng `close` |
| `rolling_max(close, n)` | Max của `n` bar gần nhất | `n` ∈ `[2, 300]` | `n` bar | Chỉ `Breakout(up)`: `close > rolling_max(close, n).ago(1)` |
| `rolling_min(close, n)` | Min của `n` bar gần nhất | `n` ∈ `[2, 300]` | `n` bar | Chỉ `Breakout(down)`: `close < rolling_min(close, n).ago(1)` |

`Breakout` so với giá trị **của bar trước** (`.ago(1)`), nên đỉnh/đáy không bao gồm chính bar hiện tại.

**Oscillator** (không thứ nguyên — so với level, ngưỡng lấy từ `LEVEL_RANGES` mục 3.3):

| Chỉ báo | Công thức | Period (TUNABLE) | Thang giá trị | Warm-up | Dùng ở |
|---|---|---|---|---|---|
| `rsi(close, n)` | RSI Wilder: làm mượt lãi/lỗ với `α = 1/n`; lỗ = 0 ⇒ `100` | `n` ∈ `[2, 100]` | `[0, 100]` | `n + 1` bar | `Threshold`, `CrossLevel`, `Slope` |
| `zscore(close, n)` | `(close − mean_n) / std_n` (độ lệch chuẩn tổng thể); `std = 0` ⇒ NaN | `n` ∈ `[5, 300]` | thường `[-3, 3]` | `n` bar | `Threshold`, `CrossLevel`, `Slope` |

**Thước đo và stop** (lock cũ: period **cố định**, không phải TUNABLE, không tính vào trần 6):

| Chỉ báo | Công thức | Period | Dùng ở |
|---|---|---|---|
| `atr(bars, 14)` | ATR Wilder: true range `max(h − l, abs(h − c₋₁), abs(l − c₋₁))` làm mượt `α = 1/n` | `14` | Luôn có (feature `"atr"`): mẫu số của `Distance`, guard `ready`, và stop ATR `k_stop × ATR` |
| `boll_lower(close, 8)` | `SMA₈ − 2 × std₈` | `8` | Stop Bollinger phía **long**: `k_stop × (close − boll_lower)` |
| `boll_upper(close, 8)` | `SMA₈ + 2 × std₈` | `8` | Stop Bollinger phía **short**: `k_stop × (boll_upper − close)` |

Whitelist cho `atr` khoảng `(2, 100)` nhưng renderer luôn ghi literal `14`; `boll_*` bị khoá ở
`(8, 8)`. ADR-0041 (P3-53, đã merge) biến period
của stop thành gene `n_stop` ∈ `[5, 50]` dưới lock `tunable_v1`: stop ATR dùng feature riêng
`"atr_stop": ind.atr(bars, self.p.n_stop)`, stop Bollinger dùng `n_stop` làm period của band (hệ số
2σ giữ nguyên). `atr(bars, 14)` vẫn là thước đo cho `Distance` và guard `ready`. Lock không có
`stop_period` thì giữ nguyên `14` / `8` như bảng trên.

Chỉ báo thứ 12, từ grammar v4 (P3-58, ADR-0045):

| Chỉ báo | Công thức | Period | Dùng ở |
|---|---|---|---|
| `spread_stdev(a, b, n)` | Độ lệch chuẩn tổng thể của `a − b` trên `n` bar; `= 0` ⇒ NaN | `n` = period của `b` (của `a` nếu `b` là close), không phải TUNABLE riêng | Mẫu số của `Distance` v4: `(a − b) / spread_stdev(a, b, n)` |

Chỉ báo thứ 13–15, từ grammar v5 (P3-61, ADR-0047) — không thứ nguyên, quanh `1`:

| Chỉ báo | Công thức | Period (TUNABLE) | Warm-up | Dùng ở |
|---|---|---|---|---|
| `bandwidth(close, n)` | `4 × std_n / (sma_n × √n)` — độ rộng dải Bollinger **trên √bar**; `sma ≤ 0` hoặc `std = 0` ⇒ NaN | — | `n` bar | Thành phần của `band_ratio` (grammar không dùng trực tiếp) |
| `band_ratio(close, fast, slow)` | `bandwidth(fast) / bandwidth(slow)`; phía chậm ≤ 0 hoặc NaN ⇒ NaN | `fast`, `slow` | `slow` bar | `Bandwidth` |
| `atr_ratio(bars, fast, slow)` | `atr(fast) / atr(slow)`; phía chậm ≤ 0 ⇒ NaN | `fast`, `slow` | `slow` bar | `VolRatio` |

Vì sao chia √n: dải của random walk rộng ra theo `√n` (trung vị độ rộng thô 0,145 ở n = 10, 0,575 ở
n = 100), nên tỷ lệ thô của hai độ rộng chỉ phản ánh `√(fast/slow)`. Chia √n thì tỷ lệ quanh 1 ở mọi
cặp period và ở cả 1d lẫn 1h.

**Vị từ** (trả `bool`, so bar hiện tại với bar trước):

| Chỉ báo | Định nghĩa | Dùng ở |
|---|---|---|
| `cross_up(a, b)` | `a₋₁ ≤ b₋₁` và `a > b`; `False` nếu có NaN | `Cross(up)` giữa hai chuỗi giá; `CrossLevel(up)` oscillator vượt lên level |
| `cross_down(a, b)` | `a₋₁ ≥ b₋₁` và `a < b`; `False` nếu có NaN | `Cross(down)`; `CrossLevel(down)` |

Khi level là hằng số (`CrossLevel`), `b₋₁ = b`.

Toán tử `point` (mục 4) chỉ đổi chỉ báo **trong cùng họ**: `sma ↔ ema` và `rsi ↔ zscore`.
`rolling_max/min` chỉ đổi qua lại khi lật hướng `Breakout`; `atr`/`boll_*` chỉ đổi khi đổi kiểu stop.

---

## 4. Toán tử di truyền ([operators.py](src/quantcrucible/agent/evolution/operators.py))

| Hằng | Giá trị | Ý nghĩa |
|---|---|---|
| `p_param` | `0.3` | Xác suất chọn `param` (nếu chưa chạm trần `param_only_max`) |
| `STRUCTURAL` | `point, subtree, crossover` | Khi không chọn `param`: bốc đều một trong ba (mỗi cái ≈ 23,3%) |
| `PERIOD_STEP` | `0.2` | Period dịch `N(0, max(1, 20% × giá trị))`, làm tròn, kẹp trong bound |
| `LEVEL_STEP` | `0.1` | Level / hệ số dịch `N(0, 10% × độ rộng khoảng)`, kẹp trong bound |
| `MAX_CLAUSES` | `3` | `crossover` chỉ được **thêm** clause khi đang < 3 |

### `param` — chỉ đổi số
Chọn đều một TUNABLE và dịch nó. Nếu period không đổi sau khi làm tròn thì ép ±1. Nếu vẫn kẹt
ở biên ⇒ `OperatorFailed`. Code giữ nguyên ⇒ cùng `strategy_hash` ⇒ gate ④ coi là biến thể
của cùng code, và tính vào trần `param_only_max`.

### `point` — đổi một điểm của cấu trúc
Bốc `u ~ U(0,1)`:
- `u < 0.15` và `boll_stop_probability > 0`: đổi kiểu stop ATR ↔ Bollinger.
- `u < 0.2` và entry là `Combine`: đổi `and` ↔ `or`.
- `u < 0.6`: **lật** một clause — đảo `>`/`<` hoặc `up`/`down`. Với `Threshold`/`CrossLevel` thì
  level được **lấy lại** vì nó phải nằm đúng phía (ví dụ RSI `>` cần `[50, 90]`).
- còn lại: **đổi chỉ báo cùng họ** — `sma ↔ ema`, `rsi ↔ zscore` (đổi sang oscillator khác thì
  level được lấy lại theo thang mới).

Lưu ý các ngưỡng là cộng dồn và nhánh nào không áp dụng thì rơi xuống nhánh dưới. Ví dụ ở chế độ
không bracket, genome 1 clause có cả khoảng `[0, 0.6)` là "lật". Ở chế độ bracket, genome 1 clause
có `[0, 0.15)` đổi stop, `[0.15, 0.6)` là lật.

### `subtree` — thay cả một clause
Chọn đều một vị trí, thay bằng một clause mới lấy mẫu từ `config.clause_types`. Thử tối đa 20 lần
để vừa trần TUNABLE của lock (6, hoặc 7 dưới `tunable_v1`).

### `crossover` — lấy gene từ mate
Chọn đều một clause của mate. Nếu cha đang < 3 clause thì 50% **thêm** clause đó, còn lại **thay**
một clause của cha. Thêm 50% lấy luôn **stop** (giá trị `k_stop` + kiểu stop + `n_stop`) của mate. Con phải
vừa trần TUNABLE và khác cha; thử tối đa 20 lần. Không có mate ⇒ engine chuyển sang `subtree`.

---

## 5. Chọn cha và mate ([sampling.py](src/quantcrucible/agent/evolution/sampling.py))

### `ALPHA` — `0.5` (arch §3.1.8)
Với xác suất `α`, cha được bốc đều từ **elite** của đảo (mỗi ô MAP-Elites một con giỏi nhất) —
*khai thác*. Ngược lại bốc đều từ **cả quần thể** của đảo — *khám phá*. Đảo chưa có entry hợp lệ ⇒
không có cha ⇒ engine sinh genome mới.

Mate (cho crossover) bốc theo cùng quy tắc, trong cùng đảo, loại trừ chính cha.

---

## 6. Đảo và di cư ([islands.py](src/quantcrucible/agent/evolution/islands.py))

### Số đảo — `N = C + 1 = 5` (không cấu hình được, theo `CATEGORIES`)

| Đảo | Danh mục | `clause_types` dùng khi seed |
|---|---|---|
| `i0` | trend | Compare, Cross, Distance, Slope |
| `i1` | momentum | Threshold, CrossLevel, Slope |
| `i2` | mean_reversion | Threshold, CrossLevel, Distance, Breakout |
| `i3` | breakout | Breakout |
| `i4` | mở | cả 7 loại |

Các đảo xoay vòng: đề xuất thứ `k` thuộc đảo `i(k mod 5)`. **Một thế hệ = 5 đề xuất** (mỗi đảo một).
Con sinh ra thuộc về đảo nó được lai tạo trên.

**Grammar v5** (ADR-0047): thêm danh mục `volatility` ⇒ `N = 6`. `i4` là đảo `volatility` (seed chỉ
`VolRatio`, `Bandwidth`), `i5` là đảo mở (cả 9 loại); một thế hệ = 6 đề xuất. Lock v1–v4 giữ 5 đảo
như bảng trên, nên campaign cũ resume đúng đảo của nó.

### `MIGRATION_INTERVAL` — `10` thế hệ (arch §3.1.8)
Cứ 10 thế hệ (= 50 đề xuất của một đơn vị tìm kiếm), ngay đầu thế hệ, engine lên kế hoạch di cư.

### `MIGRATION_FRACTION` — `0.10` (arch §3.1.5)
Mỗi đảo chép `max(1, ⌊10% × kích thước quần thể⌋)` entry có điểm cao nhất sang đảo kế tiếp theo
vòng tròn `i0 → i1 → … → i4 → i0`, bỏ qua những entry đảo đích đã có. Đây là **sao chép**, không
phải di chuyển. Mỗi lần di cư là một event `MIGRATION` trong ledger, và không bao giờ vượt qua
ranh giới instrument hay direction.

---

## 7. Archive MAP-Elites và feature map

### Điều kiện vào archive
Một trial là entry hợp lệ khi: verdict `PASS`, **qua gate ③** (IS), và **không bị loại ở gate ④**
(PBO ≥ `pbo_max`). Chỉ metric `public` (IS-only) được đọc — không bao giờ PBO, CPCV hay metric
`private` (INV-68).

### Ô (cell) = 1 chiều rời rạc + 5 chiều liên tục

| Chiều | Map v1 (lock cũ) | Map v2 (lock mới, P3-59, ADR-0046) |
|---|---|---|
| Danh mục — bitset trên 4 danh mục (16 tổ hợp) | — | — |
| `trades_per_year` | `[0, 150]` tuyến tính | `[1, 10.000]` **thang log** (mỗi bin ×1,78) |
| `max_drawdown` | `[0, 1]` | `[0, 0,5]` |
| `sharpe_is` | `[-1, 3]` | `[-1, 3]` |
| `sortino_is` | `[-1.5, 4.5]` | `[-1.5, 4.5]` |
| lợi nhuận | `total_return` `[-1, 4]` | `annual_return` (CAGR) `[-0,1; 0,3]` |

Map v2 không phụ thuộc timeframe hay độ dài IS: chiến lược 2 lệnh/năm và 2.000 lệnh/năm đều có
bin riêng, và CAGR không lớn dần theo số năm IS. Bound đối chiếu với 2.473 trial gate ③ của các
campaign 1d (98% drawdown < 0,36; CAGR trong `[-0,05; 0,11]`); sẽ hiệu chỉnh lại sau campaign 1h đầu.

### `bins` — `16`
Mỗi chiều liên tục chia 16 bin đều. Bound **không bao giờ giãn** theo giá trị quan sát; giá trị
ngoài khoảng rơi vào bin biên; thiếu hoặc không hữu hạn rơi vào bin 0.

Mỗi ô giữ entry có **điểm ranking** cao nhất (hoà thì giữ entry cũ hơn). Danh sách elite của đảo
là toàn bộ các ô đang có người.

---

## 8. Điểm xếp hạng ([ranking.py](src/quantcrucible/agent/evolution/ranking.py))

```
score = primary
      − λ_sim    · max Jaccard(chữ ký, chữ ký các entry trước)
      − λ_params · n_params / 6
      + λ_spp    · tanh(spp_median_sharpe)
      + λ_plat   · plateau
```

Đây là **thứ hạng cho tìm kiếm, không phải gate** — không bao giờ so với `dsr_min`; gate ⑤ mới
kiểm DSR, và trên **danh mục hợp nhất**.

| Thành phần | Hằng | Giải thích |
|---|---|---|
| `primary` | — | PSR của lợi nhuận IS so với benchmark khử lệch `SR₀(N_eff, V[SR])` (lấy từ `trial_stats` của ledger), rồi **lấy thứ hạng** trong quần thể, chuẩn hoá `[0, 1]`; hoà chia hạng trung bình; quần thể 1 entry ⇒ `0.5`. Dùng hạng thay giá trị vì khi số trial lớn, SR₀ tăng và PSR bị nén sát 0, làm các số hạng phụ lấn át (ADR-0029) |
| `similarity` | `LAMBDA_SIM = 0.10` | Phạt trùng lặp. Chữ ký = các clause bỏ số, sắp xếp (+ `stop:bollinger` nếu có). So với **các entry trước** theo thứ tự trial |
| `params` | `LAMBDA_PARAMS = 0.05` | Phạt độ phức tạp: 6 TUNABLE mất `0.05`, 3 TUNABLE mất `0.025`. Thang `/6` cố định ở mọi lock: genome 7 TUNABLE (có `n_stop`) mất `0.0583` (ADR-0041) |
| `spp` | `LAMBDA_SPP = 0.05` | Thưởng ổn định: trung vị Sharpe IS trên lưới cấu hình của gate ④ (System Parameter Permutation), nén bằng `tanh` |
| `plateau` | `LAMBDA_PLATEAU = 0.05` | Thưởng vùng phẳng: tỷ lệ hàng xóm đạt ≥ `plateau_threshold` × Sharpe ứng viên |

Thang tối đa: `primary` chạy `[0, 1]`, các số hạng phụ cộng lại tối đa khoảng ±0.25, nên thứ hạng
PSR quyết định là chính. ⚠️ Điểm **chỉ so sánh được trong cùng một lần tính** (cùng quần thể).

---

## 9. Vòng lặp engine ([gp_search.py](src/quantcrucible/agent/engines/gp_search.py))

### `MAX_TRIES` — `20`
Số lần thử cho một đề xuất. 18 lần đầu: lai tạo (nếu thất bại thì sinh mới). **2 lần cuối** bắt
buộc sinh genome mới (một genome mới không bao giờ bị trần param-only chặn). Hết 20 lần vẫn không
có gì mới ⇒ `RuntimeError`.

### Một lần `next()`
1. Xác định đảo `= proposals mod 5`, thế hệ `= proposals div 5`.
2. Dựng trạng thái từ ledger: entry hợp lệ, điểm ranking, các lần di cư ⇒ quần thể từng đảo.
3. Nếu là đầu thế hệ, thế hệ > 0 và chia hết cho 10 ⇒ di cư, rồi dựng lại trạng thái.
4. Chọn cha (mục 5) → chọn toán tử (mục 4) → chọn mate nếu crossover → lai tạo.
5. `_accept`: bỏ nếu cặp `(code, tham số)` đã từng đề xuất; bỏ nếu là con param-only mà trần
   `param_only_max` đã đầy. Nhận thì cập nhật bộ đếm.

### Bộ đếm (dựng lại từ ledger khi khởi động)
- `proposals` — tổng đề xuất đã nộp.
- `children` — số con lai tạo (có `mutation`).
- `param_only` — số con chỉ đổi tham số (xác định bằng `strategy_hash` của code render).
- `seen` — tập `(source, params)` đã đề xuất, để không bao giờ đề xuất lại.

---

## 10. Bảng tra nhanh

| Tham số | Giá trị | Đổi ở đâu |
|---|---|---|
| `engines.gp` | 0.5 | `config/user.yaml` |
| `gp.param_only_max` | 0.30 | `config/user.yaml` |
| `gp.plateau_threshold` | 0.5 | `config/user.yaml` (dùng ở gate ④) |
| `seeds` | 3 | `config/user.yaml` |
| `exit.tp_sl_ratio` | 1.1 | `config/user.yaml` |
| `exit.stop_kinds` | [atr] | `config/user.yaml` |
| `boll_stop_probability` | 0.5 / 0 | Suy ra từ exit protocol + `stop_kinds` |
| `clause_count_weights` | 0.5 / 0.35 / 0.15 | `grammar.py` |
| `or_probability` | 0.3 | `grammar.py` |
| `take_profit_probability` | 0 | `grammar.py` |
| `max_params` | 6 / 7 (lock `tunable_v1`) | lock `derived.max_tunables`; trần cứng `MAX_TUNABLES = 7` ở `core/strategy/tunable.py` |
| `STOP_PERIOD_RANGE` (`n_stop`) | [5, 50] | `grammar.py` |
| Xác suất `close` | 0.3 | `grammar.py` |
| Khoảng period / level / stop | mục 3.3 | `grammar.py`, `core/strategy/registry.py` |
| Danh sách chỉ báo (whitelist `ind`) | 11 chỉ báo, mục 3.4 | `core/strategy/registry.py` (`OPS`) |
| `p_param` | 0.3 | `operators.py` |
| `PERIOD_STEP` / `LEVEL_STEP` | 0.2 / 0.1 | `operators.py` |
| `MAX_CLAUSES` | 3 | `operators.py` |
| Ngưỡng nội bộ `point` | 0.15 / 0.2 / 0.6 | `operators.py` |
| `ALPHA` | 0.5 | `sampling.py` |
| Số đảo | 5 (lock v5: 6) | Theo `categories_for(version)` |
| `MIGRATION_INTERVAL` | 10 thế hệ | `islands.py` |
| `MIGRATION_FRACTION` | 0.10 | `islands.py` |
| `bins` + bound feature map | 16, mục 7 | `validation/run.py` |
| `λ_sim / λ_params / λ_spp / λ_plat` | 0.10 / 0.05 / 0.05 / 0.05 | `ranking.py` |
| `MAX_TRIES` | 20 | `gp_search.py` |
| ATR / Bollinger period khi không có gene | 14 / 8 | `core/strategy/genome.py` |

---

## 11. Quan sát (chưa kiểm chứng trên dữ liệu)

1. **Danh mục đảo chỉ ràng buộc lúc seed.** `breed()` nhận `self.config` (đủ 7 loại clause), không
   nhận `self.seeding[island]`. Vì vậy `subtree` trên đảo `i3` có thể sinh clause trend/momentum;
   sau vài thế hệ các đảo pha trộn. Nếu muốn đảo giữ chuyên môn thì cần truyền cấu hình của đảo.
2. **Feature map rất thưa.** 16 × 16⁵ ≈ 16,8 triệu ô so với ~300 trial GP ⇒ gần như mỗi entry
   chiếm một ô riêng ⇒ elite ≈ cả quần thể ⇒ `ALPHA` gần như không tạo khác biệt giữa khai thác và
   khám phá, áp lực chọn lọc theo điểm rất yếu. Ranking khi đó chủ yếu chỉ tác động qua **di cư**
   (top 10%) và qua ô nào bị tranh chấp.
3. **`take_profit_probability = 0` và `TP_RANGE`** hiện là mã chết trong luồng bracket, vì
   `tp_sl_ratio` luôn thắng gene `k_tp`.
