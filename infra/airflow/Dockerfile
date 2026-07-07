FROM apache/airflow:2.10.5-python3.12

ARG AIRFLOW_CONSTRAINTS="https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.12.txt"

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    libgomp1 \
    libgnutls30 \
    libsqlite3-0 \
    libssl3 \
    libxml2 \
    openssl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*
USER airflow

RUN pip install --no-cache-dir --constraint "${AIRFLOW_CONSTRAINTS}" \
    "boto3" \
    "google-cloud-storage" \
    "google-cloud-bigquery" \
    "pyarrow" \
    "lightgbm==4.6.0" \
    "pandas" \
    "psycopg[binary]==3.2.9" \
    "scikit-learn" \
    && pip install --no-cache-dir \
    "cryptography>=43.0.1,<45" \
    "cffi<2" \
    "mlflow==3.9.0" \
    "evidently==0.7.21" \
    "Authlib>=1.6.9" \
    "h11>=0.16.0" \
    && pip uninstall -y \
    "apache-airflow-providers-snowflake" \
    "snowflake-connector-python" \
    "snowflake-sqlalchemy" \
    && pip check
