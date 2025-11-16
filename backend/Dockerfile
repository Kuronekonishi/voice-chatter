# syntax=docker/dockerfile:1
# Google Cloud Run 用のサンプル Dockerfile
# ビルド手順:
#   gcloud builds submit --tag gcr.io/PROJECT_ID/voice-chatter-backend backend/
# デプロイ:
#   gcloud run deploy voice-chatter-backend \
#       --image gcr.io/PROJECT_ID/voice-chatter-backend \
#       --platform managed \
#       --region asia-northeast1 \
#       --set-env-vars GCP_PROJECT_ID=PROJECT_ID,API_TOKEN=xxx,GOOGLE_APPLICATION_CREDENTIALS=/var/secrets/google/key.json

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
