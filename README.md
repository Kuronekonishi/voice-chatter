# Voice Chatter プロトタイプ

## システムアーキテクチャ概要
- **Raspberry Pi クライアント**: `sounddevice` でマイクから 16 kHz のリニア PCM を取得し、WebSocket でクラウドへ連続送信。バックエンドから受け取った音声をスピーカーで再生。
- **Google Cloud Run バックエンド (FastAPI)**: 受信した音声を Google Cloud Speech-to-Text (Streaming) で文字起こしし、Vertex AI Gemini にアンパンマン風の口調での応答生成を指示。得られたテキストを Google Cloud Text-to-Speech で音声化し、クライアントへ返却。
- **セキュリティ/設定**: サービスアカウント JSON を環境変数 `GOOGLE_APPLICATION_CREDENTIALS` で読み込み、WebSocket 接続時にトークン検証 (`API_TOKEN`) を実施。

```mermaid
flowchart LR
    subgraph Pi["Raspberry Pi\n(sounddevice / WebSocket client)"]
        mic[(マイク)] --> encoder["16 kHz LINEAR16\nオーディオチャンク"]
        encoder --> ws_send["WebSocket 送信"]
        speaker[(スピーカー)] <-- audio_back["音声再生"]
    end

    subgraph Cloud["Google Cloud Run\nFastAPI バックエンド"]
        ws_recv["WebSocket 受信\n(token 検証)"] --> stt["Google Cloud\nSpeech-to-Text (ja-JP)\nストリーミング"]
        stt --> llm["Vertex AI Gemini\nアンパンマン口調 指示付き"]
        llm --> tts["Google Cloud\nText-to-Speech (ja-JP)"]
        tts --> ws_resp["音声レスポンス\nWebSocket 送信"]
    end

    Pi -->|API_TOKEN| Cloud
    ws_resp --> audio_back
```

## ファイル構成
```
backend/
  ├─ app/main.py          # FastAPI アプリケーション
  ├─ requirements.txt     # Cloud Run 用依存関係
  └─ Dockerfile           # Cloud Build/Run 用
client/
  ├─ pi_client.py         # Raspberry Pi 用 WebSocket クライアント
  └─ requirements.txt     # Pi 側依存関係
sample.env                # 共通の環境変数サンプル
```

## Persona 指示の注入方法
`backend/app/main.py` の `_generate_response` 内で、Vertex AI Gemini 呼び出し時に以下のようなインストラクションを埋め込み、アンパンマンのような優しく元気な口調を強制します。

```python
persona_instruction = (
    "あなたはアンパンマンみたいに優しく、元気で、子どもに話しかけるような口調で日本語だけで返答します。"
)
response = model.generate_content([
    {
        "role": "user",
        "parts": [
            {"text": persona_instruction},
            {"text": f"利用者の発話: {prompt}"},
            {"text": "利用者への返答を一つの短い段落で作成してください。"},
        ],
    }
])
```

## Raspberry Pi クライアントのセットアップ
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv portaudio19-dev
python3 -m venv venv
source venv/bin/activate
pip install -r client/requirements.txt
cp sample.env .env  # BACKEND_WS_URL と API_TOKEN を編集
python client/pi_client.py
```
- Enter キーで録音開始 -> 話し終えたら Enter キーで停止 -> LLM/音声応答を再生。
- 例外はログに出力され、連続会話にも対応。

## バックエンドのローカル実行
```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export GCP_PROJECT_ID=your-project
export API_TOKEN=please_change_me
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```
- `ws://localhost:8080/ws/voice?token=API_TOKEN` に WebSocket で接続。
- `/health` でヘルスチェック、`/auth-check` でトークン検証テストが可能。

## Cloud Run へのデプロイ例（ステップバイステップ）

1. **事前準備**
   - [Cloud SDK](https://cloud.google.com/sdk/docs/install) をローカルにインストールし、`gcloud version` で確認。
   - Google Cloud コンソールで新規プロジェクトを作成、または既存プロジェクトを使用し、ID を `GCP_PROJECT_ID` に控える。
   - 初回のみ `gcloud init` を実行してログイン (`gcloud auth login`) とプロジェクト選択を完了。

2. **必要な API を有効化**
   - Speech-to-Text, Text-to-Speech, Vertex AI, Cloud Run, Cloud Build, Secret Manager を順に有効化します。

   ```bash
   gcloud services enable \
     speech.googleapis.com \
     texttospeech.googleapis.com \
     aiplatform.googleapis.com \
     run.googleapis.com \
     cloudbuild.googleapis.com \
     secretmanager.googleapis.com
   ```

3. **サービスアカウントと権限設定**
   - サービスアカウントを作成し、最低限以下のロールを付与します（Speech/TTS/Vertex/Storage/Secret Manager/Cloud Run 呼び出し用）。

   ```bash
   gcloud iam service-accounts create voice-chatter-sa \
     --project ${GCP_PROJECT_ID} \
     --display-name "Voice Chatter Runtime"

   gcloud projects add-iam-policy-binding ${GCP_PROJECT_ID} \
     --member "serviceAccount:voice-chatter-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
     --role roles/aiplatform.user
   gcloud projects add-iam-policy-binding ${GCP_PROJECT_ID} \
     --member "serviceAccount:voice-chatter-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
     --role roles/texttospeech.admin
   gcloud projects add-iam-policy-binding ${GCP_PROJECT_ID} \
     --member "serviceAccount:voice-chatter-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
     --role roles/speech.client
   gcloud projects add-iam-policy-binding ${GCP_PROJECT_ID} \
     --member "serviceAccount:voice-chatter-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
     --role roles/run.invoker
   gcloud projects add-iam-policy-binding ${GCP_PROJECT_ID} \
     --member "serviceAccount:voice-chatter-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
     --role roles/secretmanager.secretAccessor
   ```

4. **サービスアカウントキーの保護**
   - JSON キーを一時的に発行し、Secret Manager に格納して Cloud Run に渡します。

   ```bash
   gcloud iam service-accounts keys create /tmp/voice-chatter-sa.json \
     --iam-account voice-chatter-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com

   gcloud secrets create voice-chatter-sa \
     --replication-policy automatic
   gcloud secrets versions add voice-chatter-sa \
     --data-file /tmp/voice-chatter-sa.json
   rm /tmp/voice-chatter-sa.json
   ```

5. **環境変数・シークレットの準備**
   - API トークンを生成しておき、Cloud Run の環境変数 `API_TOKEN` に設定します。
   - 地域（例: `asia-northeast1`）を `GCP_LOCATION` に、プロジェクト ID を `GCP_PROJECT_ID` に指定します。

6. **コンテナイメージをビルド**
   - リポジトリの `backend/` に移動し、Cloud Build でコンテナをビルドして Container Registry / Artifact Registry へ push します。

   ```bash
   cd backend
   gcloud builds submit --tag gcr.io/${GCP_PROJECT_ID}/voice-chatter-backend
   ```

7. **Cloud Run へデプロイ**
   - ビルドしたイメージを Cloud Run サービスとして公開し、必要な環境変数と Secret Manager の参照を設定します。

   ```bash
   gcloud run deploy voice-chatter-backend \
     --image gcr.io/${GCP_PROJECT_ID}/voice-chatter-backend \
     --region asia-northeast1 \
     --platform managed \
     --allow-unauthenticated \
     --set-env-vars GCP_PROJECT_ID=${GCP_PROJECT_ID},API_TOKEN=${API_TOKEN},GCP_LOCATION=asia-northeast1 \
     --set-secrets GOOGLE_APPLICATION_CREDENTIALS=projects/${GCP_PROJECT_ID}/secrets/voice-chatter-sa:latest \
     --service-account voice-chatter-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com
   ```

8. **動作確認**
   - デプロイ後に表示される URL に対し `curl https://<service-url>/health` でヘルスチェック。
   - WebSocket 接続用のエンドポイントは `wss://<service-url>/ws/voice?token=${API_TOKEN}`。
   - ログは `gcloud logs read --project ${GCP_PROJECT_ID} --service voice-chatter-backend` で確認できます。

9. **運用のヒント**
   - 費用削減のため、不要時は `gcloud run services update-traffic --to-latest=0` などでトラフィックを止める。
   - 環境変数の更新は `gcloud run services update voice-chatter-backend --set-env-vars ...` で実施。
   - バージョン管理のため、Git のタグや Cloud Build のトリガーを設定して CI/CD 化することも推奨です。

## セキュリティと設定のポイント
- WebSocket 接続は必ず `?token=...` で署名されたトークンを付与し、バックエンド側で照合。
- サービスアカウントキーは Secret Manager からマウントし、`GOOGLE_APPLICATION_CREDENTIALS` で参照。
- Cloud Run 側で CORS 制御や VPC-SC を追加することで、更なる保護も可能。

## テスト戦略のヒント
- Pi 側: `arecord -l` でマイク確認。`python client/pi_client.py` を `--env` オプションで切替可能。
- バックエンド: `pytest` と `fastapi.testclient` で WebSocket のモックテスト、LLM/TTS はスタブ化するとよい。

## 参考
- [Google Cloud Speech-to-Text Streaming](https://cloud.google.com/speech-to-text/docs/streaming-recognize)
- [Google Cloud Text-to-Speech](https://cloud.google.com/text-to-speech/docs/reference/libraries)
- [Vertex AI Gemini](https://cloud.google.com/vertex-ai/docs/generative-ai/start)
