FROM python:3.12-slim

RUN pip install --no-cache-dir \
    mlflow==2.22.1 \
    boto3==1.38.33 \
    psycopg2-binary==2.9.10

EXPOSE 5000

ENTRYPOINT ["mlflow"]
