"""
FastAPI サーバー (GCP Cloud Run向け)
概要:
- /process-audio に multipart/form-data で `audio`(WAV 16kHz mono LINEAR16) を受け取る
- Google Speech-to-Text にストリーミング送信して日本語文字起こしを取得
- Vertex AI Gemini を使ってペルソナ指示を注入して応答生成（環境変数で切替、無ければフォールバックロジック）
- Google Text-to-Speech で日本語音声を合成 (LINEAR16) → WAV にパッケージ化して返却
- 簡単なBearerトークン認証を実装

Cloud Run デプロイ例 (コメント内):
    gcloud builds submit --tag gcr.io/$PROJECT_ID/voice-chatter-backend
    gcloud run deploy voice-chatter-backend --image gcr.io/$PROJECT_ID/voice-chatter-backend --platform managed --region us-central1 --allow-unauthenticated --set-env-vars GOOGLE_APPLICATION_CREDENTIALS=/secrets/key.json

注意:
- このサンプルは簡易的な実装です。本番ではエラー処理・認証・レート制限等を強化してください。
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Header
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Optional
import os
import io
import wave
import logging

# GCP クライアント
try:
    from google.cloud import speech_v1p1beta1 as speech
    from google.cloud import texttospeech
    from google.cloud import aiplatform
    HAS_GOOGLE = True
except Exception:
    HAS_GOOGLE = False

app = FastAPI(title="voice-chatter-backend")

# 環境変数
API_TOKEN = os.environ.get("API_TOKEN", "changeme")
USE_VERTEX = os.environ.get("USE_VERTEX", "false").lower() in ("1","true","yes")
PERSONA_INSTRUCTION = (
    "あなたはアンパンマンみたいに優しく、元気で、子どもに話しかけるような口調で返答してください。" 
    "常に日本語で短く分かりやすく、励ますような表現を使ってください。"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 認証デコレータ代わり
async def validate_auth(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    token = authorization.split(" ",1)[1]
    if token != API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


# --- ヘルパー: WAV bytes を STREAMING リクエストに分割 ---

def wav_bytes_to_raw_pcm_chunks(wav_bytes: bytes, chunk_size=3200):
    """WAV バイト列(PCM16) を raw PCM チャンクに分割して返すジェネレータ
    chunk_size はバイト数（例えば 3200 は 100ms @16kHz mono 16bit -> 16000 * 0.1 * 2 = 3200）
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sampwidth = wf.getsampwidth()
        nchannels = wf.getnchannels()
        framerate = wf.getframerate()
        if sampwidth != 2 or nchannels != 1:
            raise ValueError("音声は16bitモノラルである必要があります。サンプル幅: %s, チャンネル: %s" % (sampwidth, nchannels))
        # readframes returns bytes; yield in chunk_size
        while True:
            data = wf.readframes(chunk_size // sampwidth)
            if not data:
                break
            yield data


# --- STT ---

def streaming_stt_from_wav_bytes(wav_bytes: bytes) -> str:
    if not HAS_GOOGLE:
        # フォールバック: 空文字列を返す
        logger.warning("google cloud speech ライブラリが利用できません。フォールバックを使用します。")
        return ""

    client = speech.SpeechClient()

    # 設定
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code="ja-JP",
        enable_automatic_punctuation=True,
    )
    streaming_config = speech.StreamingRecognitionConfig(config=config, interim_results=False)

    # ジェネレータ: 最初のリクエストは config、続いて音声チャンク
    def requests_generator():
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
        for chunk in wav_bytes_to_raw_pcm_chunks(wav_bytes):
            yield speech.StreamingRecognizeRequest(audio_content=chunk)

    responses = client.streaming_recognize(requests=requests_generator())

    # 結果を結合
    final_transcript = []
    for response in responses:
        for result in response.results:
            if result.is_final:
                final_transcript.append(result.alternatives[0].transcript)
    return "\n".join(final_transcript)


# --- LLM 呼び出し（Vertex またはフォールバック） ---

def generate_response_with_persona(user_text: str) -> str:
    """Vertex AI を使ってペルソナを注入して応答を生成します。USE_VERTEX が有効でない場合は簡易フォールバックを実行します。"""
    # ペルソナを明示的にプロンプトへ注入
    system_message = PERSONA_INSTRUCTION
    prompt = f"{system_message}\n\nユーザー: {user_text}\nアシスタント:")

    if USE_VERTEX and HAS_GOOGLE:
        try:
            # 簡易な Vertex 呼び出し例。実運用では Vertex のモデル指定や streaming の扱いを適切に実装してください。
            aiplatform.init()
            model = aiplatform.TextGenerationModel.from_pretrained("text-bison@001")
            resp = model.predict(prompt, max_output_tokens=256)
            return str(resp.text)
        except Exception as e:
            logger.exception("Vertex 呼び出しに失敗しました。フォールバックへ切替")
    # フォールバック: 優しいアンパンマン風の簡易生成
    safe_reply = (
        f"まあまあ、{user_text}って言ったんだね！すごいね！\n"
        "わかったよ、がんばったね。もっと聞きたいことがあったら言ってね！"
    )
    return safe_reply


# --- TTS ---

def synthesize_wav_bytes_from_text_jp(text: str, sample_rate_hz=16000) -> bytes:
    if not HAS_GOOGLE:
        # フォールバック: 簡易的にテキストを返す（音声合成が無いことを示す）
        logger.warning("google cloud tts ライブラリが利用できません。フォールバックのテキストを返します。")
        fake_wav = io.BytesIO()
        with wave.open(fake_wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate_hz)
            # silence
            wf.writeframes(b"\x00\x00" * sample_rate_hz // 10)
        return fake_wav.getvalue()

    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    # 日本語の声を選択
    voice = texttospeech.VoiceSelectionParams(language_code="ja-JP", name="ja-JP-Wavenet-A")
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16, sample_rate_hertz=sample_rate_hz)
    response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
    # response.audio_content は raw LINEAR16 bytes。WAV ヘッダを付けて返す
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate_hz)
        wf.writeframes(response.audio_content)
    return wav_buf.getvalue()


@app.post("/process-audio")
async def process_audio(authorization: Optional[str] = Header(None), audio: UploadFile = File(...)):
    # 認証
    await validate_auth(authorization)

    # 受け取ったファイルを確認
    contents = await audio.read()
    if not contents:
        raise HTTPException(status_code=400, detail="No audio received")

    # STT
    try:
        transcript = streaming_stt_from_wav_bytes(contents)
    except Exception as e:
        logger.exception("STT に失敗")
        raise HTTPException(status_code=500, detail=f"STT error: {e}")

    # LLM（ペルソナ注入）
    try:
        reply_text = generate_response_with_persona(transcript)
    except Exception as e:
        logger.exception("LLM 生成に失敗")
        reply_text = "ごめんね、うまく聞き取れなかったみたい。もう一回言ってくれるかな？"

    # TTS
    try:
        wav_bytes = synthesize_wav_bytes_from_text_jp(reply_text)
    except Exception as e:
        logger.exception("TTS に失敗")
        raise HTTPException(status_code=500, detail=f"TTS error: {e}")

    # ストリーミングで返却
    return StreamingResponse(io.BytesIO(wav_bytes), media_type="audio/wav")


@app.get("/health")
def health():
    return JSONResponse({"status":"ok"})
