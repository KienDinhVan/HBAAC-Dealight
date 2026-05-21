# HBAAC-Dealight — Demand Forecasting

Dự án dự báo nhu cầu (demand forecasting) ở mức SKU cho 56 ngày tiếp theo, sử dụng mô hình **two-stage LightGBM** (phân loại có bán / hồi quy số lượng nếu bán) blend với baseline **recent-median**, sau đó hậu xử lý cho một số "key SKU".

Output mục tiêu của tài liệu này: tái hiện file submission

```
data/artifacts/submission_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05_alpha0.60_keysku_cautious.csv
```

---

## 1. Cấu trúc dự án

```
HBAAC-Dealight/
├── data/
│   ├── raw/                        # Dữ liệu gốc (train.csv, sample_submission.csv)
│   ├── processed/                  # Dữ liệu trung gian (.pkl) sinh ra từ run_foundation
│   └── artifacts/                  # Model + submission đầu ra
├── src/hbacc_prj/
│   ├── data.py                     # Load + biến đổi train.csv, tạo demand matrix, SKU profile
│   ├── baselines.py                # Recent-median baseline + tiện ích đánh giá
│   ├── metrics.py                  # WRMSSE
│   ├── segments.py                 # Chiến lược chọn SKU (top_profit, active_recent_top_profit, ...)
│   ├── model_lgbm.py               # Feature engineering / cấu hình LightGBM dùng chung
│   ├── model_twostage.py           # Two-stage classifier + regressor + blend với baseline
│   ├── postprocess_key_skus.py     # Hậu xử lý cho SKU-00002 / SKU-00003 (key SKUs)
│   └── run_foundation.py           # Pipeline foundation (sinh processed pickles + sku_profile)
├── pyproject.toml                  # Dependencies (Python >= 3.13)
└── uv.lock
```

Dữ liệu đầu vào cần đặt sẵn:

- [data/raw/train.csv](data/raw/train.csv) — lịch sử giao dịch (cột: `Date, Stt, ItemCode, Quantity, UnitPrice, SalesAmount, Unit Cost, Cost Amount`).
- [data/raw/sample_submission.csv](data/raw/sample_submission.csv) — định dạng nộp bài (`id, F1..F28` với hai biến thể `_validation` / `_evaluation`).

---

## 2. Cài đặt môi trường

Dự án dùng Python **3.13** và quản lý dependency bằng [`uv`](https://github.com/astral-sh/uv) (đã có sẵn `uv.lock`).

```bash
# Cài uv (nếu chưa có)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Tạo venv và cài đủ deps từ uv.lock
cd HBAAC-Dealight
uv sync
```

```bash
uv run python -m hbacc_prj.run_foundation
```

(hoặc `source .venv/bin/activate` rồi `python -m ...`).

---

## 3. Pipeline tổng quát

```
data/raw/train.csv
        │
        ▼
[run_foundation]  ──►  data/processed/daily_sales.pkl
                       data/processed/daily_demand_matrix.pkl
                       data/artifacts/sku_profile.pkl
                       data/artifacts/baseline_cv_scores.csv
        │
        ▼
[model_twostage --mode future]  ──►  classifier_future_<run>.txt
                                     regressor_future_<run>.txt
                                     twostage_future_top_sku_forecast_<run>.pkl
                                     submission_<run>_alpha0.60.csv
        │
        ▼
[postprocess_key_skus]  ──►  submission_<run>_alpha0.60_keysku_cautious.csv
```

---

## 4. Các bước tái hiện file submission


### Bước 1 — Sinh dữ liệu nền (chỉ cần chạy 1 lần)

```bash
uv run python -m hbacc_prj.run_foundation
```

Sinh ra:

- `data/processed/daily_sales.pkl`
- `data/processed/daily_demand_matrix.pkl`
- `data/artifacts/sku_profile.pkl`
- `data/artifacts/baseline_cv_scores.csv`

### Bước 2 — Train two-stage và xuất submission (mode `future`, alpha=0.60)

```bash
uv run python -m hbacc_prj.model_twostage \
  --mode future \
  --sku-strategy active_recent_top_profit \
  --top-n-skus 300 \
  --min-active-days 50 \
  --max-days-since-last-sale 56 \
  --lookback-days 730 \
  --origin-stride 7 \
  --num-boost-round 900 \
  --early-stopping-rounds 50 \
  --valid-train-end 2025-09-05 \
  --end-of-selling-since 2025-01-01 \
  --alphas 0.60
```

### Bước 3 — Hậu xử lý "cautious" cho key SKUs

```bash
RUN="twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05"

uv run python -m hbacc_prj.postprocess_key_skus \
  --input  data/artifacts/submission_${RUN}_alpha0.60.csv \
  --output data/artifacts/submission_${RUN}_alpha0.60_keysku_cautious.csv \
  --train-end 2025-09-05 \
  --sku2-alpha 0.0 \
  --sku3-alpha 0.15
```

Biến thể "cautious" giữ nguyên SKU-00002 (`alpha=0`) và pha 15% reference weekday vào SKU-00003 (`alpha=0.15`) — đây là default trong [src/hbacc_prj/postprocess_key_skus.py](src/hbacc_prj/postprocess_key_skus.py#L123-L131).

---

## 5. Một số chi tiết kỹ thuật

- **Demand matrix**: pivot `(ItemCode × Date)` của `sales_qty` (đã clip về ≥ 0). Được dùng làm input chung cho mọi module ([data.py](src/hbacc_prj/data.py#L64-L85)).
- **SKU profile**: tính `active_days`, `days_since_last_sale`, `profit_weight`, ... với tuỳ chọn `time_consistent` (re-compute theo `as_of`) — quan trọng vì `profit_weight` cũng là trọng số WRMSSE.
- **Two-stage**:
  - Classifier (`objective=binary`) học `P(qty > 0)`.
  - Regressor (`objective=poisson`) học `qty | qty > 0`.
  - Forecast = `clip(P × E[qty|qty>0], 0, ∞)` ([model_twostage.py:153-168](src/hbacc_prj/model_twostage.py#L153-L168)).
- **Blend**: `forecast = α · twostage + (1 - α) · recent_median` chỉ trên các SKU được chọn; phần còn lại giữ baseline.
- **Postprocess**:
  - `zero_sundays`: ép `F` thuộc Chủ Nhật về 0 (do EDA cho thấy không có giao dịch CN từ 2023+).
  - `zero_end_of_selling`: ép về 0 các SKU không bán kể từ `end_of_selling_since`.
  - `postprocess_key_skus`: blend lại SKU-00002 / SKU-00003 với reference theo cùng weekday + factor tháng + xu hướng 56/112 ngày.

---

