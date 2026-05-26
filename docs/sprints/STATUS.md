# Trạng thái triển khai theo sprint

Tài liệu này là nguồn theo dõi thứ tự nghiệm thu. Code thử nghiệm có thể tồn tại
trước một sprint, nhưng không được tính là sprint hoàn thành khi các gate trước
đó chưa đóng.

| Sprint | Trạng thái | Gate để đóng sprint |
|---|---|---|
| Sprint 0 - Kickoff & Planning | Hoàn thành - nghiệm thu 2026-05-26 | Scope, metric chính và data contract trong `SPRINT_00_KICKOFF.md` đã được phê duyệt |
| Sprint 1 - Foundation & Local Infrastructure | Hoàn thành - nghiệm thu 2026-05-26 | Deliverables/evidence tại `SPRINT_01_FOUNDATION.md` |
| Sprint 2 - Bronze/Silver/Gold Data Pipeline | Hoàn thành nghiệm thu chức năng 2026-05-26; rollout pending | Pipeline/DAG pass; blocker Docker host tại `SPRINT_02_DATA_PIPELINE.md` |
| Sprint 3 - Feature Engineering | Hoàn thành nghiệm thu chức năng 2026-05-26; rollout pending | Deliverables/evidence tại `SPRINT_03_FEATURE_ENGINEERING.md` |
| Sprint 4 - Training & MLflow | Hoàn thành nghiệm thu chức năng 2026-05-26; rollout pending | Deliverables/evidence tại `SPRINT_04_TRAINING_MLFLOW.md` |
| Sprint 5 - Batch Forecasting | Hoàn thành nghiệm thu chức năng 2026-05-27; rollout pending | Deliverables/evidence tại `SPRINT_05_BATCH_FORECASTING.md` |
| Sprint 6 - FastAPI Serving | Chưa nghiệm thu | Có PoC API, nhưng phụ thuộc Sprint 5 chính thức |
| Sprint 7 - Monitoring & Drift | Chưa bắt đầu | Sprint 6 hoàn thành |
| Sprint 8 - CI/CD, Deployment & UAT | Chưa nghiệm thu | Có Compose PoC local, chưa qua các sprint trước |

## Quy tắc làm việc

1. Mỗi lần chỉ đóng một sprint sau khi deliverables và acceptance criteria có bằng chứng.
2. Phần code được dựng sớm để khảo sát kỹ thuật được giữ lại, nhưng mang nhãn PoC.
3. Sprint 2 chỉ được đóng sau khi pipeline tạo được `gold.daily_sku_sales`,
   chạy lại idempotent và toàn bộ data quality/integration test pass.
4. Runtime image mới của Sprint 2-4 được nghiệm thu bằng one-off Airflow DAG
   run; thay long-running Airflow/API containers vẫn chờ Docker host cho phép
   stop/recreate container hiện hành.
