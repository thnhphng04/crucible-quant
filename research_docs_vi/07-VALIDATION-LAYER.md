# Validation Layer — giải thích chi tiết

> Lớp quyết định dự án này có giá trị hay không. Cả ngành đang thiếu nó — QuantEvolve, RD-Agent(Q), AlphaAgent đều **không có**.
> Ngày: 2026-09-20 · Liên quan: [[00-TONG-HOP-NGHIEN-CUU]] · [[05-SMOOTH-FLOW]]

---

## ⚠️ 0. Đính chính quan trọng về ngưỡng DSR

Các tài liệu trước trong thư mục này ghi **"DSR > 0"** (kế thừa từ báo cáo gốc). **Sai.**

**DSR là một XÁC SUẤT, giá trị từ 0 đến 1.** Nó là Probabilistic Sharpe Ratio với benchmark được điều chỉnh theo số trial.

| Giá trị DSR    | Ý nghĩa                                                                                       |
| ---------------- | ----------------------------------------------------------------------------------------------- |
| **> 0.95** | Bằng chứng mạnh strategy có alpha thật                                                     |
| 0.50 – 0.95     | Vùng xám, chưa đủ kết luận                                                               |
| **< 0.50** | **Không có bằng chứng gì vượt ngoài may rủi**, xét theo cường độ tìm kiếm |

→ Ngưỡng đúng là **DSR > 0.95**. "DSR > 0" luôn đúng một cách vô nghĩa vì xác suất không bao giờ âm.

---

## 1. Vấn đề: backtest nói dối theo BA cách khác nhau

Đây là điểm quan trọng nhất và hay bị hiểu nhầm. Người ta thường gộp tất cả thành "overfitting", nhưng thực ra có **ba loại lỗi độc lập**, và **mỗi loại cần một công cụ riêng**.

| #           | Loại lỗi               | Bản chất                                                                 | Ví dụ                                                       |
| ----------- | ------------------------ | -------------------------------------------------------------------------- | ------------------------------------------------------------- |
| **1** | **Leakage**        | Dùng dữ liệu**không được phép dùng** tại thời điểm đó | Dùng nến chưa đóng; indicator repaint; label chồng lấn |
| **2** | **Selection bias** | Thử**quá nhiều lần**, chọn cái may mắn nhất                  | Hyperopt 200 epoch, chọn epoch tốt nhất                    |
| **3** | **Memorization**   | LLM**đã thấy** giai đoạn test trong pretraining                 | Test 2017–2020 với model cutoff 2024                        |

> 🔴 **Điểm chết người:** DSR và PBO **chỉ giải quyết loại 2**. Chúng **hoàn toàn mù** với loại 1 và loại 3.
>
> Bằng chứng (arXiv 2608.27734): một **"leaky oracle"** cố tình rò rỉ dữ liệu tương lai, Sharpe 34.7, **vẫn đạt DSR = 1.00**.

Vì vậy validation layer phải có **ba lớp**, không phải một.

---

## 2. Kiến trúc ba lớp

```
┌─ LỚP 1: STRUCTURAL GUARDRAIL ──────── chống LEAKAGE ──────┐
│  • Whitelist indicator (không repaint)                    │
│  • Cấm truy cập index tương lai (.shift(-n))              │
│  • Purging + embargo khi chia fold                        │
│  • PIT data layer, fail-loud khi thiếu                    │
│  ⚡ Chạy TRƯỚC backtest — rẻ, loại sớm                    │
└───────────────────────────┬───────────────────────────────┘
                            ↓
┌─ LỚP 2: STATISTICAL CORRECTION ─── chống SELECTION BIAS ──┐
│  • CPCV → phân phối Sharpe (không phải 1 điểm)            │
│  • PBO qua CSCV → xác suất quy tắc chọn bị overfit        │
│  • DSR → deflate theo TỔNG số trial                       │
│  • MinBTL → backtest có đủ dài không                      │
│  ⚡ Chạy SAU backtest, TRƯỚC khi nhìn holdout             │
└───────────────────────────┬───────────────────────────────┘
                            ↓
┌─ LỚP 3: TEMPORAL ISOLATION ──── chống MEMORIZATION ───────┐
│  • Holdout đóng băng, chạy ĐÚNG MỘT LẦN                   │
│  • Test window sau knowledge cutoff (nếu dùng LLM)        │
│  • Đo IS→OOS decay (>50% → nghi memorization)             │
│  • Dry-run / forward test                                 │
│  ⚡ Cuối cùng, không thể quay lại                         │
└───────────────────────────────────────────────────────────┘
```

Ba lớp **không thay thế nhau**. Bỏ lớp nào cũng để lọt một loại lỗi.

---

## 3. LỚP 1 — Structural guardrail

### 3.1. Purging — vì sao cross-validation thường bị hỏng

Đây là khái niệm ít người biết nhất nhưng quan trọng nhất.

**Tình huống:** bạn dự đoán return 5 phiên tới tại thời điểm `t`. Label của mẫu `t` được tính bằng dữ liệu tới `t+5`.

```
Train set                    Test set
├────────────────────────┤ ├──────────────────┤
                    ↑ t              
                    └──── label của t dùng dữ liệu tới t+5
                         └─── ĐÈ LÊN test set → LEAK
```

Mẫu train tại `t` **đã "nhìn thấy"** một phần test set. K-Fold tiêu chuẩn của sklearn **không biết gì về chuyện này**.

**Purging** = xóa các mẫu train có label chồng lấn khoảng thời gian test.

### 3.2. Embargo — vì sao purging vẫn chưa đủ

Sau khi purge, mẫu train nằm **ngay sau** test set vẫn bị nhiễm vì **serial correlation** — giá hôm nay tương quan với giá hôm qua.

**Embargo** = xóa thêm một vùng đệm sau test set.

```
├─── train ───┤ ✂purge✂ ├─ TEST ─┤ ✂embargo✂ ├─── train ───┤
```

### 3.3. Những thứ khác ở lớp này

| Guardrail              | Cách làm                                                                  |
| ---------------------- | --------------------------------------------------------------------------- |
| Indicator whitelist    | Chỉ cho agent dùng indicator đã kiểm tra không repaint                |
| Cấm nhìn tương lai | Reject code chứa`.shift(-n)`, `iloc[i+1:]`, `.rolling(...).shift(-)` |
| Nến chưa đóng      | Chỉ cho đọc bar đã đóng —`df.iloc[-2]` chứ không phải `[-1]` |
| Fail-loud              | Thiếu dữ liệu →**ném exception**, không fillna âm thầm        |

---

## 4. LỚP 2 — Statistical correction

### 4.1. CPCV — từ MỘT đường OOS thành NHIỀU đường

Walk-forward truyền thống cho bạn **một** đường OOS duy nhất. Sharpe của nó là **một con số** — không biết nó may hay xui.

**Combinatorial Purged CV** chia dữ liệu thành N block, lấy K block làm test, chạy tất cả tổ hợp:

```
N=6 block, K=2 test → C(6,2) = 15 fold → 5 backtest path độc lập
```

Kết quả: **phân phối Sharpe**, không phải điểm. Bạn thấy được Sharpe trung vị, độ phân tán, và tỷ lệ path âm.

> 💡 Đây cũng là nguyên liệu để tính PBO.

### 4.2. PBO qua CSCV — cơ chế

**Probability of Backtest Overfitting** trả lời câu: *"Xác suất bao nhiêu để cấu hình tốt nhất in-sample lại rớt dưới trung vị out-of-sample?"*

Cách nó chạy:

```
1. Lập ma trận M: T hàng (thời gian) × N cột (cấu hình strategy)
2. Chia T thành S nhóm đều nhau
3. Với MỖI tổ hợp chọn S/2 nhóm làm IS (phần còn lại là OOS):
   a. Tìm cấu hình tốt nhất trong IS  →  n*
   b. Tính thứ hạng tương đối của n* trong OOS  →  ω ∈ (0,1)
   c. λ = log( ω / (1-ω) )          ← logit
4. PBO = tỷ lệ tổ hợp có λ < 0
        = tỷ lệ lần "quán quân IS rớt dưới median OOS"
```

**Đọc PBO cho đúng** *(đính chính 21/9/2026 — bản trước viết "PBO tiến tới 1 khi N tăng, kể cả khi không có edge". **Sai.**)*

- Khi **không có edge** và thứ hạng OOS độc lập với IS, quán quân IS rơi vào vị trí ngẫu nhiên đều trong OOS ⇒ **PBO ≈ 0.5**, bất kể N lớn cỡ nào. N lớn **không** tự đẩy PBO về 1.
- PBO **> 0.5** xảy ra khi thứ hạng IS và OOS **tương quan âm** — ví dụ cấu hình khớp nhiễu IS càng tốt thì càng tệ OOS (tham số bám vào pattern đảo chiều, overfit cấu trúc).
- PBO **< 0.5** xảy ra khi có thành phần tín hiệu ổn định khiến cấu hình tốt IS có xu hướng tốt OOS.
- Cái tăng theo N là **Sharpe IS kỳ vọng của quán quân** dưới giả thuyết không — đó là việc của **DSR** (§4.3), không phải PBO.

**Hệ quả:** PBO đo *quy tắc chọn trong một tập cấu hình cho trước* có ổn định OOS không. Nó **không** phải thuộc tính của một chuỗi returns đơn lẻ — phải định nghĩa rõ ma trận `M` gồm những cấu hình nào và quy tắc chọn winner là gì. Trong [Architecture_Design.md](Architecture_Design.md) §3.2: `M` = lưới tham số đăng ký trước quanh ứng viên + các biến thể refine đã thử; quy tắc = Sharpe IS cao nhất.

| PBO              | Kết luận                                                 |
| ---------------- | ---------------------------------------------------------- |
| < 0.2            | Tốt                                                       |
| 0.2 – 0.5       | Chấp nhận được, thận trọng                          |
| **≥ 0.5** | **Strategy là nhiễu. Loại — KHÔNG tinh chỉnh** |

> ⚠️ "Không tinh chỉnh" nghĩa là: đừng sửa tham số rồi thử lại. Mỗi lần thử lại **tăng N của DSR** (ngưỡng Sharpe phải vượt cao lên), và việc chọn lại sau khi đã thấy PBO chính là một vòng selection mới mà PBO cũ không đo. Loại hẳn.

### 4.3. DSR — cơ chế

**Vấn đề:** nếu bạn thử 1.000 strategy hoàn toàn vô dụng, cái tốt nhất vẫn sẽ có Sharpe dương khá cao — **thuần do may mắn**.

**Ý tưởng DSR:**

```
1. Tính Sharpe kỳ vọng LỚN NHẤT của N trial KHÔNG CÓ SKILL
   → phụ thuộc N và độ phân tán Sharpe giữa các trial
   → đây là "benchmark deflated"

2. DSR = P( Sharpe thật > benchmark đó )
   → có hiệu chỉnh cho skew, kurtosis, độ dài mẫu
```

DSR chính là **PSR với benchmark nâng lên theo số trial**.

**Ba tham số quyết định kết quả:**

| Tham số                 | Ảnh hưởng                                            |
| ------------------------ | ------------------------------------------------------- |
| **N (số trial độc lập)**  | Càng lớn → benchmark càng cao → DSR càng thấp. Paper phân biệt số trial *thực tế* với số trial *độc lập* — trial tương quan cao không tính đủ |
| **V[SR] (phương sai Sharpe giữa các trial)** | Càng lớn → benchmark càng cao. Phải lưu Sharpe **của mọi trial**, không chỉ của winner |
| **Skew âm**       | Phạt nặng (chiến lược "nhặt xu trước máy ủi") |
| **Độ dài mẫu** | Càng ngắn → càng không tin được                 |

> 🔑 **N là tham số bạn dễ khai gian nhất với chính mình.** Xem mục 6.

### 4.4. MinBTL — gate rẻ tiền nhưng hiệu quả

**Minimum Backtest Length:** với N trial đã thực hiện, backtest phải dài tối thiểu bao nhiêu năm để một Sharpe quan sát được là đáng tin?

Dùng nó như **gate sớm**: nếu backtest của bạn ngắn hơn MinBTL ứng với số trial đã chạy → **không cần tính gì thêm, loại luôn**. Rẻ hơn nhiều so với chạy CPCV.

### 4.5. MinTRL

**Minimum Track Record Length:** cần bao nhiêu quan sát để khẳng định Sharpe quan sát được vượt một benchmark ở mức tin cậy cho trước. Dùng để quyết định **dry-run bao lâu là đủ**.

---

## 5. LỚP 3 — Temporal isolation

| Công cụ                      | Chống gì                | Quy tắc                                                                 |
| ------------------------------ | ------------------------- | ------------------------------------------------------------------------ |
| **Holdout đóng băng** | Selection bias tích lũy | Chạy**đúng một lần**. Nhìn rồi là cháy — vĩnh viễn     |
| **Test sau cutoff**      | LLM memorization          | Nếu dùng LLM sinh strategy, ưu tiên test window sau knowledge cutoff |
| **IS→OOS decay**        | Memorization + overfit    | Decay > 50% → nghi ngờ (benchmark Profit Mirage)                       |
| **Dry-run**              | Mọi thứ còn sót       | 4–6 tuần tối thiểu, spread/phí thật                                |

> ⚠️ **Holdout chỉ dùng được MỘT lần.** Nếu bạn chạy holdout, thấy kết quả xấu, sửa strategy rồi chạy lại — holdout đó **đã chết**, nó trở thành in-sample. Đây là lỗi kỷ luật, không phải lỗi kỹ thuật, và không có công cụ nào cứu được.

---

## 6. Trial ledger — chỗ dễ tự lừa dối nhất

DSR phụ thuộc trực tiếp vào **N**. Khai N nhỏ → DSR đẹp giả tạo.

### Đếm SAI

```
"Tôi chạy hyperopt 200 epoch"  →  N = 200
```

### Đếm ĐÚNG

```
N = tổng MỌI lần một cấu hình được đánh giá, tích lũy toàn dự án:

  50 strategy do agent sinh
× 200 epoch hyperopt mỗi cái
+ 30 lần bạn tự sửa tay rồi backtest lại
+ 12 lần thử timeframe khác
+ 8 lần đổi universe
─────────────────────────────
= 10.050 trial
```

**Và N không bao giờ reset.** Nó tích lũy suốt vòng đời dự án.

> 🆕 **Nhưng không phải mọi dòng log đều là trial** *(bổ sung 21/9/2026)*. Trial = một cấu hình **đã được đo hiệu suất trên dữ liệu**. Một lời gọi LLM, một file không compile, một lần reviewer đọc báo cáo — ghi lại để audit, nhưng **không** là phép thử có thể sinh selection bias. Trộn chúng vào `N` làm DSR sai. Ngược lại, trial tương quan cao (cùng ý tưởng, tham số lân cận) cũng không phải trial độc lập — dùng `N_eff` từ gom cụm và luôn báo cáo kèm `N_raw`. Thiết kế hai sổ: [Architecture_Design.md](Architecture_Design.md) §4.1.

> 💡 Đây chính là điều arXiv 2608.27734 gọi là **search ledger**, và là lý do agent-loop nguy hiểm hơn người làm tay: *"productivity của agent trở thành bias amplifier"*. Agent chạy hàng nghìn trial ngầm trước khi bạn kịp thấy một con số.

**Schema tối thiểu:**

```sql
CREATE TABLE trials (
    id            INTEGER PRIMARY KEY,
    ts            TIMESTAMP,
    strategy_hash TEXT,      -- hash của code, phát hiện trùng lặp
    params        JSON,
    universe      TEXT,
    timeframe     TEXT,
    timerange     TEXT,
    sharpe_is     REAL,
    returns_path  TEXT,      -- đường dẫn file returns để tính DSR sau
    verdict       TEXT       -- PASS / REJECT_PBO / REJECT_DSR / REJECT_GUARDRAIL
);
```

**Quy tắc sắt:** mọi backtest đều phải đi qua ledger. **Không có đường vòng.** Nếu có thể chạy backtest mà không ghi ledger, sớm muộn bạn sẽ làm thế.

---

## 7. Triển khai với `purgedcv`

API dưới đây lấy từ tài liệu chính thức của package.

> ⚠️ **Chưa kiểm chứng với mã nguồn** *(21/9/2026)*. Chữ ký bên dưới chép từ tài liệu, chưa đối chiếu code thật, chưa pin phiên bản. Coi là **minh họa**. Kế hoạch kiểm chứng và phương án dự phòng: [Architecture_Design.md](Architecture_Design.md) §6.

```python
from purgedcv import (
    WalkForwardSplit,
    PurgedKFold,
    PurgedGroupKFold,
    CombinatorialPurgedCV,
    probabilistic_sharpe_ratio,
    deflated_sharpe_ratio,
    deflated_sharpe_ratio_full,      # trả về DSRDiagnostics
    min_track_record_length,
    minimum_backtest_length,
    probability_of_backtest_overfitting,   # trả về PBOResult
)
```

### 7.1. Purged K-Fold với embargo

```python
cv = PurgedKFold(
    n_splits=4,
    prediction_times=pred,      # thời điểm ra tín hiệu
    evaluation_times=evalu,     # thời điểm label được chốt
    purge_horizon="2D",
    embargo="2D",               # hoặc embargo_observations=10 / embargo_fraction=0.01
)
```

### 7.2. Walk-forward

```python
cv = WalkForwardSplit(
    n_splits=3,
    test_size=4,
    window="expanding",         # hoặc "sliding" (khi đó cần train_size)
    prediction_times=pred,
    evaluation_times=evalu,
    purge_horizon="2D",
)
for train_idx, test_idx in cv.split(X):
    ...
```

### 7.3. CPCV — sinh nhiều backtest path

```python
cv = CombinatorialPurgedCV(
    n_splits=6,                 # N block
    n_test_groups=2,            # K block test
    prediction_times=pred,
    evaluation_times=evalu,
)
# C(6,2) = 15 fold → 5 backtest path
paths = cv.backtest_paths(model, X, y)
```

### 7.4. PSR và MinTRL

```python
psr = probabilistic_sharpe_ratio(strategy_returns, benchmark_skill=0.0)

n_min = min_track_record_length(
    observed_sharpe=0.7,
    target_sharpe=0.5,
    alpha=0.05,
    skew=0.0,
    kurtosis=3.0,
)
```

⚠️ **Chữ ký chính xác của `deflated_sharpe_ratio` và `probability_of_backtest_overfitting` mình chưa xác minh được** — đọc docstring trong package trước khi dùng. Hai hàm đó tồn tại và có trong danh sách export; chỉ tham số là chưa chắc.

---

## 8. Đặt gate ở đâu — thứ tự quan trọng

Xếp gate **rẻ trước, đắt sau**. Loại sớm tiết kiệm compute *và* giảm N.

```
[Agent sinh strategy]
        ↓
  ① GUARDRAIL         ← rẻ nhất, không cần backtest
     • AST similarity vs zoo
     • Quét .shift(-n), truy cập index tương lai
     • Indicator có trong whitelist?
        ↓ pass
  ② MinBTL            ← chỉ cần N và độ dài backtest
     • Backtest đủ dài cho N trial hiện tại?
        ↓ pass
  ③ BACKTEST IS       ← bắt đầu tốn compute
     • Edge còn sống khi siết phí?
        ↓ pass
  ④ CPCV + PBO        ← đắt nhất
     • PBO < 0.5?
        ↓ pass
  ⑤ DSR               ← deflate theo TỔNG N từ ledger
     • DSR > 0.95?
        ↓ pass
  ⑥ HOLDOUT ĐÓNG BĂNG ← MỘT LẦN DUY NHẤT
     • Sharpe OOS ≥ 50% Sharpe IS?
        ↓ pass
  ⑦ DRY-RUN 4–6 TUẦN
        ↓ pass
  ⑧ LIVE, size nhỏ nhất
```

**Mọi lần reject đều ghi ledger** — trial bị loại vẫn là trial, vẫn tính vào N.

---

## 9. Leaky oracle test — bài kiểm tra cho chính harness

Đây là ý tưởng hay nhất trong arXiv 2608.27734, và là **gate đầu tiên bạn nên xây**.

**Cách làm:** viết một strategy cố tình gian lận — dùng giá tương lai:

```python
# leaky_oracle.py — KHÔNG BAO GIỜ deploy
def populate_entry_trend(df):
    # Gian lận trắng trợn: nhìn giá ngày mai
    df["enter_long"] = df["close"].shift(-1) > df["close"]
    return df
```

Chạy nó qua toàn bộ pipeline.

| Kết quả                              | Kết luận                                                          |
| -------------------------------------- | ------------------------------------------------------------------- |
| Pipeline**từ chối ở gate ①** | ✅ Guardrail hoạt động                                           |
| Pipeline cho qua, DSR = 1.00, PBO = 0  | 🔴**Harness của bạn hỏng** — đúng như paper cảnh báo |

> Đây là lý do mình nói ở [[05-SMOOTH-FLOW]]: **gate tuần 1 không phải "strategy có lãi không", mà là "harness có từ chối được leaky oracle không"**.

Nên có **một bộ oracle** với nhiều mức tinh vi khác nhau:

1. `.shift(-1)` trắng trợn
2. Dùng `close` của nến đang chạy (chưa đóng)
3. Dùng indicator repaint
4. Dùng thông tin có thật nhưng công bố muộn (mô phỏng lỗi PIT)

Mức 4 là khó nhất và cũng là mức gần với lỗi thật nhất.

---

## 10. Bảng ngưỡng tổng hợp

| Gate                     | Ngưỡng                  | Nếu vi phạm                        |
| ------------------------ | ------------------------- | ------------------------------------ |
| Guardrail                | 0 vi phạm                | Reject, ghi ledger                   |
| **MinBTL**         | Backtest ≥ MinBTL(N)     | Reject hoặc kéo dài dữ liệu     |
| **PBO**            | < 0.5 (lý tưởng < 0.2) | **Reject, KHÔNG tinh chỉnh** |
| **DSR**            | **> 0.95**          | Reject                               |
| IS→OOS Sharpe           | OOS ≥ 50% IS             | Nghi overfit/decay                   |
| Sharpe decay post-cutoff | < 50%                     | Nghi memorization                    |
| Dry-run                  | ≥ MinTRL quan sát       | Chưa đủ dữ liệu để kết luận |

---

## 11. Ba sai lầm phổ biến nhất

**1. Tính DSR/PBO ở cuối như một báo cáo.** Sai — phải đưa vào **hàm fitness của vòng lặp**. AlphaAgent và RD-Agent(Q) đều không làm điều này, và đó là lý do kết quả của họ không đáng tin (xem [[08-LLM-QUANT-RESEARCHER]] mục 5).

**2. Reset N mỗi lần chạy.** N tích lũy suốt vòng đời dự án. Reset = tự lừa dối.

**3. Tin rằng DSR/PBO đủ.** Chúng chỉ chống selection bias. Leakage và memorization cần lớp khác.

---

## 12. Nguồn

- [Bailey &amp; López de Prado — The Deflated Sharpe Ratio (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) · [PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- [Deflated Sharpe ratio — Wikipedia](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio)
- [Quantdare — Deflated Sharpe Ratio: how to avoid being fooled by randomness](https://quantdare.com/deflated-sharpe-ratio-how-to-avoid-been-fooled-by-randomness/)
- [purgedcv trên PyPI](https://pypi.org/project/purgedcv/)
- López de Prado — *Advances in Financial Machine Learning* (2018), chương 7 (purging/embargo) và 11–12 (CPCV, PBO)
- arXiv 2608.27734 — "What survives honest evaluation?" (leaky oracle, search ledger)

---

## Một câu tóm tắt

> Backtest nói dối theo ba cách khác nhau, và **DSR/PBO chỉ chữa được một**. Lớp rẻ nhất (structural guardrail) lại bắt được loại lỗi nguy hiểm nhất. Và tham số quan trọng nhất của toàn bộ hệ thống là **N — con số bạn dễ tự lừa dối nhất**.
