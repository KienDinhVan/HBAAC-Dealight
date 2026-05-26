# Quy Trinh Compute Cho AI Agent Tao Bao Cao HBAAC Dealight

## 1. Muc dich va nguyen tac su dung

Tai lieu nay mo ta toan bo luong compute da dung de xu ly du lieu, danh gia baseline, huan luyen mo hinh, tao submission va dien giai ket qua cho bai toan du bao nhu cau theo SKU.

AI Agent tao bao cao nen phan biet ba loai thong tin:

- **Da kiem chung trong workspace**: ma nguon, file artifact, tham so chay va ket qua baseline co the doc truc tiep tu repository.
- **Ket qua leaderboard do nguoi dung da ghi nhan**: diem public cua cac file da submit.
- **Dien giai**: nhan xet ve ly do mo hinh hieu qua hoac gioi han, can viet nhu lap luan thay vi su that tuyet doi.

Khong duoc khang dinh diem private leaderboard neu khong co bang chung rieng.

## 2. Tong quan bai toan va du lieu dau vao

### Input chinh

| Duong dan | Vai tro |
| --- | --- |
| `data/raw/train.csv` | Du lieu giao dich lich su theo ngay va ma hang |
| `data/raw/sample_submission.csv` | Khuon output can nop, gom cac dong `_validation` va `_evaluation` |
| `data/processed/mapping_skus.csv` | Mapping ma cu sang ma thay the, chi dung trong nhanh post-process cua submission thu hai |

### Cot du lieu train quan trong

| Cot | Y nghia trong compute |
| --- | --- |
| `Date` | Ngay giao dich |
| `ItemCode` | SKU |
| `Quantity` | So luong; gia tri am la return |
| `SalesAmount` | Doanh thu |
| `Cost Amount` | Chi phi |

### Moc thoi gian du bao cuoi

| Noi dung | Khoang ngay |
| --- | --- |
| Ngay train cuoi dung de forecast | `2025-09-05` |
| Nua dau horizon, gan vao dong `_validation` | `2025-09-06` den `2025-10-03` |
| Nua sau horizon, gan vao dong `_evaluation` | `2025-10-04` den `2025-10-31` |
| Tong horizon mo hinh | 56 ngay |

Output submission co cot `id, F1, ..., F28`. Moi SKU co hai dong: mot dong `_validation` chua 28 ngay dau va mot dong `_evaluation` chua 28 ngay sau.

## 3. So do compute end-to-end

```text
train.csv
  -> parse so thap phan Viet Nam va tach return
  -> tong hop nhu cau hang ngay theo SKU
  -> ma tran demand SKU x Date
  -> ho so SKU va profit weights
  -> baseline rolling cross-validation + WRMSSE
  -> chon 300 SKU active/recent/top profit
  -> tao feature theo origin va horizon
  -> LightGBM classifier (co ban hay khong)
  -> LightGBM Poisson regressor (so luong neu co ban)
  -> sale_prob * qty_if_sale
  -> blend voi baseline recent median
  -> zero Sundays + zero end-of-selling
  -> post-process cautious cho SKU chu chot
  -> submission chinh

Nhanh submission thu hai:
  mo hinh seed 2053
  -> ensemble voi forecast chinh
  -> blend alpha 0.575
  -> cautious key-SKU adjustment
  -> mapping SKU cu sang SKU moi
  -> submission thu hai da submit
```

## 4. Buoc 1 - Nap va chuan hoa du lieu giao dich

Ma lien quan: `src/hbacc_prj/data.py`.

### 4.1. Chuan hoa gia tri so

Du lieu tai chinh co the dung dinh dang Viet Nam. Ham `parse_vn_decimal` xu ly theo quy tac:

1. Neu cot da la kieu so, ep sang `float64`.
2. Neu la chuoi, bo khoang trang.
3. Bo dau `.` phan cach hang nghin.
4. Doi dau `,` thap phan thanh `.`.
5. Chuyen thanh so, gia tri khong hop le thanh missing.

### 4.2. Dinh nghia sale va return

Moi dong du lieu duoc bien doi nhu sau:

```text
is_return  = Quantity < 0 AND SalesAmount < 0 AND Cost Amount < 0
sales_qty  = max(Quantity, 0)
return_qty = -Quantity neu is_return, nguoc lai 0
net_qty    = Quantity
profit     = SalesAmount - Cost Amount
```

Target du bao cua mo hinh la `sales_qty`, tuc luong ban duong, khong phai `net_qty`.

## 5. Buoc 2 - Tao du lieu processed va profile SKU

Lenh compute co ban:

```bash
uv run python -m hbacc_prj.run_foundation
```

Neu moi truong chua cai package theo editable mode, dung:

```bash
PYTHONPATH=src uv run python -m hbacc_prj.run_foundation
```

### 5.1. Daily sales

Du lieu duoc aggregate theo `(Date, ItemCode)`:

```text
sales_qty, return_qty, net_qty, SalesAmount, Cost Amount, profit, line_count
```

Artifact:

```text
data/processed/daily_sales.pkl
```

### 5.2. Daily demand matrix

Pivot du lieu thanh ma tran:

```text
row    = ItemCode
column = moi ngay lien tuc trong history
value  = sales_qty, fill missing bang 0
```

Artifact:

```text
data/processed/daily_demand_matrix.pkl
```

### 5.3. SKU profile va weight

Profile SKU duoc tao tu history, co the cat theo ngay `as_of` trong cross-validation de tranh leakage. Cac chi so chinh:

| Bien | Cong thuc / Y nghia |
| --- | --- |
| `total_sales_qty` | Tong `sales_qty` |
| `active_days` | So ngay co ban |
| `zero_ratio` | Ty le ngay khong ban |
| `avg_daily_qty` | Trung binh so luong theo tat ca ngay |
| `avg_qty_when_active` | Trung binh tren cac ngay co ban |
| `total_profit` | Tong profit |
| `positive_profit` | `max(total_profit, 0)` |
| `profit_weight` | `positive_profit / sum(positive_profit)` |
| `return_qty` | Tong luong return |
| `return_ratio` | Ty le return |
| `days_since_last_sale` | So ngay tu lan ban cuoi den moc tinh |

Artifact:

```text
data/artifacts/sku_profile.pkl
```

## 6. Buoc 3 - Metric va baseline validation

Ma lien quan:

```text
src/hbacc_prj/metrics.py
src/hbacc_prj/baselines.py
```

### 6.1. WRMSSE

Metric su dung WRMSSE voi trong so profit:

```text
scale_i  = mean((y_t - y_(t-1))^2) sau lan ban khac 0 dau tien cua SKU i
rmsse_i  = sqrt(MSE_i / scale_i)
WRMSSE    = sum(profit_weight_i * rmsse_i)
```

Neu series khong du bien dong hop le, denominator fallback la `1.0`.

### 6.2. Rolling folds

Baseline duoc danh gia tren 3 fold, horizon moi fold la 56 ngay, buoc dich la 28 ngay:

| Fold | Train end | Validation range |
| --- | --- | --- |
| 1 | `2025-05-16` | `2025-05-17` den `2025-07-11` |
| 2 | `2025-06-13` | `2025-06-14` den `2025-08-08` |
| 3 | `2025-07-11` | `2025-07-12` den `2025-09-05` |

### 6.3. Baseline da tinh

Artifact:

```text
data/artifacts/baseline_cv_scores.csv
```

Ket qua compute hien co:

| Model | WRMSSE 28 ngay | WRMSSE 56 ngay |
| --- | ---: | ---: |
| `conservative_sparse` | 0.474384 | 0.511085 |
| `median_56` | 0.474384 | 0.511085 |
| `mean_28` | 0.482318 | 0.517612 |
| `mean_56` | 0.486705 | 0.518953 |
| `blend_mean_weekday` | 0.484823 | 0.523095 |
| `zero` | 0.544008 | 0.580627 |
| `same_weekday_8w` | 0.534479 | 0.602370 |

Baseline `median_56` duoc dung tiep trong buoc blend voi du bao mo hinh.

## 7. Buoc 4 - Chon SKU can du bao bang mo hinh

Ma lien quan: `src/hbacc_prj/segments.py`.

Chien luoc cuoi cung:

```text
strategy             = active_recent_top_profit
min_active_days      = 50
max_days_since_sale  = 56
top_n_skus           = 300
ranking              = profit_weight giam dan
```

Y nghia:

1. Chi train mo hinh phuc tap tren cac SKU hoat dong du va vua co giao dich gan day.
2. Trong nhom hop le, uu tien 300 SKU co dong gop profit cao.
3. SKU con lai duoc giu o du bao baseline, giam do on va chi phi train.

## 8. Buoc 5 - Tao feature supervised theo origin/horizon

Ma lien quan: `src/hbacc_prj/model_lgbm.py`.

### 8.1. Tham so tap train cuoi

| Tham so | Gia tri |
| --- | ---: |
| Horizon | 56 ngay |
| Origin lookback | 730 ngay |
| Origin stride | 7 ngay |
| SKU mo hinh | 300 |
| Time consistent profile | Bat |

`time_consistent` bat nghia la profile va weight duoc tinh chi tu history kha dung tai tung origin, khong doc tu tuong lai.

### 8.2. Feature lich su tai origin

```text
lags: 1, 7, 14, 28, 56, 84, 112, 168, 364 ngay
rolling windows: 7, 14, 28, 56, 112 ngay
rolling statistics: mean, max, active-day count
days_since_last_sale
origin day-of-week va month
```

### 8.3. Feature theo ngay target

```text
item_id
horizon index
target_dow
target_day
target_month
target_week
```

### 8.4. Feature tinh cua SKU

```text
profit_weight
zero_ratio
active_days
avg_daily_qty
avg_qty_when_active
return_ratio
```

### 8.5. Sample weighting

Trong so sample xap xi duoc tao tu:

```text
profit_weight / rmsse_denominator
```

Sau do duoc chuan hoa de mo hinh uu tien cac SKU quan trong theo metric.

## 9. Buoc 6 - Mo hinh two-stage LightGBM

Ma lien quan: `src/hbacc_prj/model_twostage.py`.

Du bao cuoi cua moi quan sat:

```text
forecast = P(sale > 0) * E(quantity | sale > 0)
```

### 9.1. Model classifier

| Thiet lap | Gia tri |
| --- | --- |
| Objective | `binary` |
| Metric | `binary_logloss` |
| Learning rate | `0.035` |
| Num leaves | `63` |
| Feature fraction | `0.85` |
| Bagging fraction | `0.85` |
| L2 | `2` |
| Min data in leaf | `80` |

Target la co ban hang (`sales_qty > 0`) hay khong.

### 9.2. Model regressor

| Thiet lap | Gia tri |
| --- | --- |
| Objective | `poisson` |
| Metric | `rmse` |
| Learning rate | `0.035` |
| Num leaves | `63` |
| Feature fraction | `0.85` |
| Bagging fraction | `0.85` |
| L2 | `2` |
| Min data in leaf | `50` |

Regressor chi train tren nhung sample co `sales_qty > 0`.

### 9.3. Train future model chinh

De tai tao dung artifact cua submission chinh, dung `1200` boosting rounds va `120` early-stopping rounds:

```bash
PYTHONPATH=src uv run python -m hbacc_prj.model_twostage \
  --mode future \
  --sku-strategy active_recent_top_profit \
  --top-n-skus 300 \
  --min-active-days 50 \
  --max-days-since-last-sale 56 \
  --lookback-days 730 \
  --origin-stride 7 \
  --num-boost-round 1200 \
  --early-stopping-rounds 120 \
  --valid-train-end 2025-09-05 \
  --end-of-selling-since 2025-01-01 \
  --alphas 0.60
```

Luu y quan trong: ten artifact co suffix `_b900`, nhung suffix nay la nhan legacy trong ham dat ten run; no khong chung minh so boosting rounds thuc te. Run chinh da dung `1200` rounds trong command tai tao.

Artifact chinh:

```text
data/artifacts/classifier_future_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05.txt
data/artifacts/regressor_future_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05.txt
data/artifacts/twostage_future_top_sku_forecast_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05.pkl
```

## 10. Buoc 7 - Blend, business rules va submission chinh

### 10.1. Blend voi baseline

Chi forecast cua 300 SKU da chon duoc thay bang blend:

```text
forecast_selected = 0.60 * forecast_twostage + 0.40 * forecast_median_56
```

SKU khong nam trong nhom chon tiep tuc dung baseline.

### 10.2. Quy tac built-in

Trong qua trinh tao future submission:

| Rule | Tac dung |
| --- | --- |
| `zero_sundays = True` | Dat du bao Chu Nhat bang 0 |
| `end_of_selling_since = 2025-01-01` | Neu SKU khong co ban ke tu moc nay, du bao bang 0 |
| Non-negative clipping | Khong cho forecast am |

### 10.3. Dieu chinh cautious cho key SKU

Ma lien quan: `src/hbacc_prj/postprocess_key_skus.py`.

```bash
PYTHONPATH=src uv run python -m hbacc_prj.postprocess_key_skus \
  --input data/artifacts/submission_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05_alpha0.60.csv \
  --output data/artifacts/submission_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05_alpha0.60_keysku_cautious.csv \
  --train-end 2025-09-05 \
  --sku2-alpha 0.0 \
  --sku3-alpha 0.15
```

Dieu chinh:

| SKU | Xu ly |
| --- | --- |
| `SKU-00002` | Giu forecast ban dau, `alpha = 0.0` |
| `SKU-00003` | Blend nhe `alpha = 0.15` voi weekday-reference co trend/month adjustment |

### 10.4. Submission chinh da ghi nhan

| Thuoc tinh | Gia tri |
| --- | --- |
| File | `submission_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05_alpha0.60_keysku_cautious.csv` |
| Tong forecast | `31949.41` |
| Public leaderboard score da ghi nhan | `0.48694` |
| Trang thai | Submission tot nhat trong cac ket qua da ghi nhan |

## 11. Buoc 8 - Nhanh submission thu hai da submit

Nhanh nay la mot thu nghiem ensemble va mapping, khong phai submission tot nhat. No can duoc trinh bay trong bao cao nhu mot ablation/experiment.

### 11.1. Train mo hinh seed thu hai

Ma lien quan: `src/hbacc_prj/model_twostage_seeded.py`.

```bash
PYTHONPATH=src uv run python -m hbacc_prj.model_twostage_seeded \
  --mode future \
  --seed-suffix seed2053 \
  --random-seed 2053 \
  --sku-strategy active_recent_top_profit \
  --top-n-skus 300 \
  --min-active-days 50 \
  --max-days-since-last-sale 56 \
  --lookback-days 730 \
  --origin-stride 7 \
  --num-boost-round 1200 \
  --early-stopping-rounds 120 \
  --valid-train-end 2025-09-05 \
  --end-of-selling-since 2025-01-01 \
  --alphas ""
```

Artifact seed thu hai:

```text
data/artifacts/classifier_future_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05_seed2053.txt
data/artifacts/regressor_future_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05_seed2053.txt
data/artifacts/twostage_future_top_sku_forecast_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05_seed2053.pkl
```

### 11.2. Ensemble hai seed

Ma lien quan: `src/hbacc_prj/build_seed_ensemble.py`.

```bash
PYTHONPATH=src uv run python -m hbacc_prj.build_seed_ensemble \
  --first-forecast data/artifacts/twostage_future_top_sku_forecast_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05.pkl \
  --second-forecast data/artifacts/twostage_future_top_sku_forecast_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05_seed2053.pkl \
  --output data/artifacts/submission_seedens20_alpha0.575.csv \
  --seed-weight 0.20 \
  --alpha 0.575
```

Cong thuc:

```text
twostage_ensemble = 0.80 * forecast_seed_chinh + 0.20 * forecast_seed2053
forecast_selected = 0.575 * twostage_ensemble + 0.425 * baseline_median_56
```

### 11.3. Post-process key SKU va mapping ma hang

```bash
PYTHONPATH=src uv run python -m hbacc_prj.postprocess_key_skus \
  --input data/artifacts/submission_seedens20_alpha0.575.csv \
  --output data/artifacts/submission_FINAL_twostage_top300_lb730_s7_b1200_seedens20_alpha0.575_keysku_cautious.csv \
  --train-end 2025-09-05 \
  --sku2-alpha 0.0 \
  --sku3-alpha 0.15

PYTHONPATH=src uv run python -m hbacc_prj.postprocess_mapping_skus \
  --input data/artifacts/submission_FINAL_twostage_top300_lb730_s7_b1200_seedens20_alpha0.575_keysku_cautious.csv \
  --output data/artifacts/submission_FINAL_twostage_top300_lb730_s7_b1200_seedens20_alpha0.575_keysku_cautious_mapoldnew_a0.05.csv \
  --train-end 2025-09-05 \
  --alpha 0.05 \
  --n-same-weekday 8
```

Mapping post-process:

1. Dung mapping ma cu (`dead`) sang ma thay the (`new`).
2. Dat forecast cua SKU cu ve 0.
3. Dieu chinh rat nhe forecast cua SKU moi voi `alpha = 0.05`, tham chieu cung thu trong tuan tren history gop cua ma cu va ma moi.

### 11.4. Submission thu hai da ghi nhan

| Thuoc tinh | Gia tri |
| --- | --- |
| File da submit | `submission_FINAL_twostage_top300_lb730_s7_b1200_seedens20_alpha0.575_keysku_cautious_mapoldnew_a0.05.csv` |
| Tong forecast | `31092.99` |
| Public leaderboard score da ghi nhan | `0.48729` |
| Ket luan thuc nghiem | Kem hon submission chinh `0.48694` mot khoang nho |

## 12. Ket qua experiment can dua vao bao cao

Bang sau gom cac diem public leaderboard da ghi nhan trong qua trinh thu nghiem:

| Submission / bien the | Public WRMSSE |
| --- | ---: |
| Top300, lookback 365, stride 14, alpha 0.65 | 0.49414 |
| Top300, lookback 365, stride 14, alpha 0.60 | 0.49479 |
| Ensemble thu som `twostage075_direct060_w50_tc` | 0.49483 |
| Them Sunday-zero va end-of-selling, alpha 0.65 | 0.49290 |
| Them cautious key-SKU tren bien the tren | 0.49174 |
| **Mo hinh chinh: lookback 730, stride 7, alpha 0.60, cautious key-SKU** | **0.48694** |
| Ensemble seed + mapping SKU, alpha 0.575 | 0.48729 |

Nhan xet hop ly cho report:

- Tang history training va tang mat do origin tu `lookback=365/stride=14` len `lookback=730/stride=7` trung voi muc cai thien leaderboard lon nhat trong cac thu nghiem da ghi nhan.
- Business rules va key-SKU post-process giup ket qua cua cac bien the som tot hon.
- Ensemble seed va mapping la thu nghiem hop le nhung khong vuot qua submission chinh; khong nen trinh bay no nhu final winner.

## 13. Artifact ledger cho AI Agent

| Artifact | Nguon tao | Vai tro trong bao cao |
| --- | --- | --- |
| `data/processed/daily_sales.pkl` | `run_foundation` | Mo ta du lieu tong hop theo ngay |
| `data/processed/daily_demand_matrix.pkl` | `run_foundation` | Mo ta input dang time series |
| `data/artifacts/sku_profile.pkl` | `run_foundation` | Phan tich SKU, profit weights va segmentation |
| `data/artifacts/baseline_cv_scores.csv` | `run_foundation` | Bang baseline validation |
| `data/artifacts/classifier_future_*.txt` | `model_twostage` | Model xac suat co ban |
| `data/artifacts/regressor_future_*.txt` | `model_twostage` | Model so luong co dieu kien |
| `data/artifacts/twostage_future_top_sku_forecast_*.pkl` | `model_twostage` | Du bao rieng tren top SKU |
| `data/artifacts/submission_*alpha0.60_keysku_cautious.csv` | Main pipeline | File final tot nhat da ghi nhan |
| `data/artifacts/*seed2053*` | Seeded model pipeline | Dau vao cho experiment ensemble |
| `data/processed/mapping_skus.csv` | Input them cho post-process | Dien giai experiment ma cu/ma moi |

## 14. Cau truc bao cao de AI Agent sinh ra

AI Agent co the sinh report theo bo cuc sau:

1. **Executive summary**: bai toan, metric, submission tot nhat `0.48694`.
2. **Data processing**: parse giao dich, dinh nghia sale/return, tao daily matrix.
3. **Metric and validation**: WRMSSE, rolling folds, baseline table.
4. **Modeling approach**: SKU selection, feature engineering, two-stage LightGBM.
5. **Post-processing**: Sunday/end-of-selling va cautious key-SKU.
6. **Experiments and results**: bang leaderboard, so sanh submission chinh voi submission thu hai.
7. **Reproducibility**: commands, artifacts, luu y suffix `_b900`.
8. **Limitations**: chi co public leaderboard; khong duoc suy dien private performance.

## 15. Checklist kiem tra truoc khi tao report hoac nop file

### Du lieu va artifact

- `train.csv` va `sample_submission.csv` ton tai.
- Foundation artifacts da duoc tao.
- Neu trinh bay submission thu hai, mapping file va seed artifacts phai ton tai.

### Submission

- Cot dung format: `id, F1, ..., F28`.
- Khong co gia tri missing.
- Forecast khong am.
- Thu tu `id` trung voi `sample_submission.csv`.
- Rule Sunday-zero va end-of-selling dung voi config da cong bo.
- Report ghi dung file nao thuc su da submit va diem nao la diem duoc ghi nhan.

### Bang chung ket qua

- Submission chinh: total forecast `31949.41`, public score `0.48694`.
- Submission thu hai: total forecast `31092.99`, public score `0.48729`.
- Ket luan cuoi: submission chinh la ket qua tot hon theo public WRMSSE vi metric cang thap cang tot.

