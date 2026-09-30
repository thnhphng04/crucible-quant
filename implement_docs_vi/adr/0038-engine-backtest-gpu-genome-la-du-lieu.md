# ADR-0038 — Engine backtest GPU: genome là dữ liệu, khớp bit với oracle CPU

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-30
- **Task:** P3-27 … P3-30 (engine: P3-32 … P3-51) · **Kiến trúc:** §3.3.3, §3.5, §6 · **Vấn đề mở:** thu hẹp O14

## Bối cảnh

Trên dữ liệu 1h (≈ 67k bar, L = 400), `step()` tính lại mọi indicator trên từng cửa sổ lookback. Việc này chiếm 87–98% một backtest (P3-27, `scripts/bench_backtest.py`), nên một lưới gate ④ tốn 0,4–3,3 giờ một nhân CPU cho mỗi candidate. ADR-0025 từng từ chối backtester lưới vector hóa vì engine thứ hai phá P5. User yêu cầu chuyển backtest sang GPU (RTX 3050 Ti Laptop, 4 GB, WDDM có watchdog, fp32:fp64 = 32:1).

## Quyết định

1. **Genome là dữ liệu.** Kernel do repo này viết chạy trên host và thông dịch genome engine C thành các bảng số. Kernel không bao giờ import, `exec` hay compile source của candidate. Source vẫn chạy trong sandbox Docker cho ①a/①b. Chiến lược không có genome grammar vẫn đi đường sandbox.
2. **Một source, hai đích.** Mỗi kernel là một hàm Python, được compile bằng `numba.njit` (CPU song song) và `numba.cuda.jit` (GPU). CUDA là chính; bản CPU dùng để dự phòng, chạy CI và làm tham chiếu fp32. Bố cục theo `reference_architecture/train_full_grid_gpu.py`: khử trùng lặp instance indicator, mỗi lane là một cặp (instance, bar), mỗi cấu hình là một thread replay.
3. **Khớp bit với oracle CPU** (`registry.py` trên `bars.window(t, L)`, source đã render, `_run_spot_bracket`):
   - phép cộng tái tạo thứ tự pairwise của numpy (8 accumulator, block 128, chia đôi làm tròn về bội 8). Viết không đệ quy, vì `njit` đệ quy có cache đã segfault.
   - `_smooth` tái tạo bước `y = (1−α)·y + α·x` của `lfilter` trong scipy, seed bằng trung bình pairwise đó.
   - trên CUDA, mọi phép fp64 đi qua `libdevice.*_rn` không gộp FMA. Không có `dsub_rn`, nên `a − b` được viết là `dadd_rn(a, −b)`, chính xác tuyệt đối. Toán tử thường bị gộp FMA và lệch 2676/4096 giá trị EMA.
4. **Toolchain.** `numba` 0.67 + `numba-cuda[cu12]` 0.30.4 nằm trong group `gpu` không mặc định; wheel NVIDIA có license độc quyền nhưng được phân phối lại. Runtime CUDA 12.9 chạy trên driver 12.7 nhờ minor-version compat. numpy bị giới hạn `<2.5`, vì numba-cuda vẫn gọi `np.row_stack`.
5. **Parity được cưỡng chế cả lúc chạy** (audit, fail closed). Precision khóa theo campaign ([ADR-0039](0039-precision-backtest-khoa-theo-campaign.md)).

## Hệ quả

- **E7 (CPU):** 0 lệch bit so với `registry.py` cho sma, ema, rsi, atr, zscore, boll, rolling_max, trên 3 seed và 4 chuỗi.
- **Prototype G1, 6 cấu hình × 4 biến thể:** equity giống từng bit `_run_spot_bracket`; số lệnh, từ chối, bar mơ hồ, tín hiệu và bit stop cũng khớp.
- **E0, G1 trên 67k bar, M = 200:** CUDA fused 0,18 s, njit fused 0,38 s, Python ≈ 1.440 s. Với M = 1: hybrid 0,018 s. CUDA chỉ hơn njit ≈ 2,1×, dưới luật 3×, và user giữ CUDA làm chính. Dự kiến chênh lệch lớn hơn với genome có indicator nặng.
- **E2/E5 (P3-43), 8 worker pipeline, genome lấy mẫu, lưới 200 cấu hình, 67k bar:** CUDA tính feature nhanh hơn CPU 5× và tín hiệu 2–3×, nhưng replay tuần tự chậm hơn 6×, nên một job CUDA tính feature và tín hiệu trên thiết bị rồi replay trên CPU; mỗi lúc chỉ các bước GPU của một job giữ thiết bị. Sau khi bỏ hai chi phí phía host (vòng JSON của mỗi report, phép so JSON trong audit), engine đạt ≈ 8,4 lưới/s trên CUDA so với ≈ 3,9 khi chỉ dùng CPU njit — khoảng 0,12 s mỗi lưới, so với 0,4–3,3 giờ trước đây.
- Lý do từ chối của ADR-0025 không còn đúng với genome engine C: engine thứ hai được chứng minh khớp oracle chứ không phải được tin. Test parity và fingerprint oracle trở thành bắt buộc mỗi khi `registry.py`, phần render `genome` hay `engine.py` thay đổi.
- **Kiến trúc cần sửa (P3-31, VI trước, rồi EN, rồi `/sync-docs`):**
  - §3.3.3: host thông dịch, không chạy source.
  - §3.5: engine kernel, oracle CPU, parity và audit.
  - §6: numba và wheel NVIDIA.
  - §10: quyết định D23.
  - §10.1: các khóa mới.
- Sau khi tăng tốc, leak-check ①b trong Docker thành nút thắt tiếp theo (sẽ mở O22).

## Các phương án đã cân nhắc

- **Sinh code CUDA theo từng genome:** nhanh hơn mỗi lần chạy, nhưng mỗi genome phải JIT, và host phải compile code sinh ra.
- **Tensor CuPy/PyTorch:** phụ thuộc nặng, EMA theo cửa sổ khó diễn đạt, và replay vẫn phải tuần tự.
- **njit làm engine chính:** đơn giản hơn (không vướng TDR hay VRAM) và chậm hơn CUDA không quá 2,1×; được giữ làm dự phòng vì user chọn GPU.
- **Dung sai thay cho khớp bit:** không cần, vì E7 đã đạt khớp tuyệt đối.
