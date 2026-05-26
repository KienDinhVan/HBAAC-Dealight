FROM apache/airflow:2.10.5-python3.12

ARG AIRFLOW_CONSTRAINTS="https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.12.txt"

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
USER airflow

RUN pip install --no-cache-dir --constraint "${AIRFLOW_CONSTRAINTS}" \
    "boto3" \
    "lightgbm==4.6.0" \
    "mlflow==2.22.1" \
    "pandas" \
    "psycopg[binary]==3.2.9" \
    "scikit-learn" \
    && pip check
