# ベースイメージ
FROM python:3.11-slim

# 環境変数
ENV PYTHONUNBUFFERED=1

# 作業ディレクトリ
WORKDIR /app

# システム依存のパッケージ（必要に応じて追加）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 依存関係
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーション
COPY backend/ .

# ポート
ENV PORT=8080
EXPOSE 8080

# 起動
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
