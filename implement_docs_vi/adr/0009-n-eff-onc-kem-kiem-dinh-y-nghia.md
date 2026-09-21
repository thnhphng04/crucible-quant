# ADR-0009 — `N_eff`: ONC kèm bước bảo vệ ý nghĩa thống kê

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-05 · **Kiến trúc:** §4.1 ("Ước lượng `N_eff`"), §3.1.6 · **Vấn đề mở:** O10 (đã đóng)

## Bối cảnh

§4.1 yêu cầu `N_eff` từ phân cụm ONC trên chuỗi lợi nhuận của các trial (khoảng cách `√(½(1−ρ))`, số cụm theo silhouette) và cảnh báo rằng `N_eff` chỉ được hạ ngưỡng khi có cơ sở. Bản cài đặt ONC thuần đầu tiên vi phạm cảnh báo đó trong test: k-means gán **mọi** trial vào một cụm nào đó, nên 12 chuỗi nhiễu độc lập ra 3–4 cụm, và một trial mới không liên quan bị gộp vào cụm cũ — cả hai đều hạ benchmark DSR mà không có bằng chứng.

## Quyết định

1. **Tự cài đặt** trong `validation/n_eff.py` (không thư viện nào có ONC): `clusterKMeansBase` (k = 2…min(n−1, 50), `n_init = 3` — giới hạn cho nhanh ở P1-08; bước bảo vệ chỉ có thể tách thêm, chất lượng = mean/std của silhouette) + `clusterKMeansTop` (phân cụm lại các cụm có t-stat silhouette dưới trung bình; chỉ giữ cách tách nếu nó cải thiện chúng), theo López de Prado & Lewis (2019). scikit-learn (`KMeans`, `silhouette_samples`, BSD-3, đã được `purgedcv` kéo theo) chạy k-means. Có seed ⇒ tất định.
2. **Bước bảo vệ ý nghĩa** sau ONC: một trial chỉ ở lại cụm khi tương quan trung bình của nó với các thành viên khác vượt `3/√T` (T = số quan sát chung trung bình; chuỗi độc lập có ρ ~ N(0, 1/T)). Loại thành viên yếu nhất trước, lặp tới khi ổn định; trial bị loại thành một cụm riêng.
3. **Biên bảo thủ:** ít hơn 3 trial ⇒ mỗi trial một cụm (ONC cần k ≥ 2 và silhouette); các chuỗi giống hệt ⇒ một cụm. Tương quan tính trên các mốc thời gian chung; dưới 30 quan sát chung, hoặc chuỗi phẳng, ⇒ ρ = 0.
4. `update_n_eff(ledger)` phân cụm **mọi** trial (mọi campaign) và thêm một dòng `clustering_runs` (`method = 'onc-v1'`). Chạy lại ở mỗi lần đánh giá danh mục (P1-07/P1-08). Hàm đọc mới `Ledger.trials()` cấp dữ liệu cho nó.

## Hệ quả

- Test: k ∈ {2, 4, 7} nguồn được cài sẵn được tìm lại chính xác; 12 chuỗi nhiễu ⇒ 12 cụm; một trial mới không liên quan vẫn tách riêng sau khi phân cụm lại.
- Chi phí: ≤ n_init · 50 lần fit k-means mỗi tầng ONC — ~8 giây cho 200 trial. Với hàng nghìn (GĐ 2), phân cụm tăng dần; xem lại lúc đó.
- Bước bảo vệ là phần bổ sung vào ONC đã công bố; nó chỉ có thể tăng `N_eff`, không bao giờ giảm.

## Phương án đã cân nhắc

- **ONC thuần:** loại — hạ `N_eff` trên nhiễu (xem Bối cảnh).
- **`purgedcv.effective_n_trials`:** đã loại ở ADR-0007 — phụ thuộc thứ tự trial, không phải tương quan lợi nhuận.
- **Phân cụm phân cấp với ngưỡng ρ cố định:** loại — ngưỡng là tham số tự do mà §4.1 không cho; chọn theo silhouette mới là cách được chỉ định.
