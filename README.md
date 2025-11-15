```markdown
# voice-chatter

このリポジトリは、Raspberry Pi（マイク＋スピーカー）と Google Cloud 上のバックエンドを使った音声チャットのプロトタイプ実装です。

目的:
- Pi で録音 → バックエンドで日本語の Speech-to-Text → LLM（ペルソナ注入）で応答生成 → Text-to-Speech で音声合成 → Piで再生

主要ファイル:
- `backend/main.py`: FastAPI サーバー。STT/TTS/LLM 呼び出しを行う。
- `backend/Dockerfile`, `backend/requirements.txt`: コンテナ化と依存。
- `pi/client.py`: Raspberry Pi 側の録音・送信・再生スクリプト。
- `.env.example`: 環境変数サンプル。

簡単な流れ:
1. Pi で録音（デフォルトは push-to-talk の簡易実装、指定秒数録音）
2. 録音 WAV を `/process-audio` に送信
3. バックエンドが Google Speech-to-Text で文字起こし、日本語で LLM にペルソナ指示を与えて応答生成
4. 生成したテキストを Google Text-to-Speech（日本語）で音声合成し WAV を返却
5. Pi が再生

注記:
- 本 repo はプロトタイプです。実運用には認証・暗号化・エラーハンドリング・リソース管理を強化してください。

デプロイ手順の概略については `backend/main.py` のコメントを参照してください。
```
# voice-chatter