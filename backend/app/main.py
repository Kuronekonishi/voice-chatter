"""FastAPI アプリケーション: Raspberry Pi との音声対話バックエンド."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from google.cloud import speech_v1p1beta1 as speech
from google.cloud import texttospeech
from google.oauth2 import service_account
from pydantic import BaseModel

try:
    from vertexai.preview.generative_models import GenerativeModel
    import vertexai
except ImportError:  # pragma: no cover - デプロイ前に requirements で解決する
    GenerativeModel = None  # type: ignore
    vertexai = None  # type: ignore


class Settings(BaseModel):
    project_id: str
    location: str = "asia-northeast1"
    api_token: str
    gcp_credentials_file: Optional[str] = None

    @classmethod
    def load(cls) -> "Settings":
        try:
            return cls(
                project_id=os.environ["GCP_PROJECT_ID"],
                location=os.environ.get("GCP_LOCATION", "asia-northeast1"),
                api_token=os.environ["API_TOKEN"],
                gcp_credentials_file=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
            )
        except KeyError as exc:  # pragma: no cover - 起動時エラー用
            missing = ", ".join(exc.args)
            raise RuntimeError(f"環境変数 {missing} が設定されていません") from exc


settings = Settings.load()


def load_credentials() -> Optional[service_account.Credentials]:
    """サービスアカウント認証情報を読み込む."""
    if settings.gcp_credentials_file:
        return service_account.Credentials.from_service_account_file(settings.gcp_credentials_file)
    return None


def get_speech_client() -> speech.SpeechClient:
    creds = load_credentials()
    return speech.SpeechClient(credentials=creds)


def get_tts_client() -> texttospeech.TextToSpeechClient:
    creds = load_credentials()
    return texttospeech.TextToSpeechClient(credentials=creds)


def get_generative_model() -> GenerativeModel:
    if GenerativeModel is None or vertexai is None:
        raise RuntimeError("vertexai ライブラリがインストールされていません")
    creds = load_credentials()
    vertexai.init(project=settings.project_id, location=settings.location, credentials=creds)
    return GenerativeModel("gemini-1.0-pro-vision")


app = FastAPI(title="Voice Chatter Backend", version="0.1.0")


@app.get("/health")
def health_check() -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def enforce_token(token: str) -> None:
    if token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid token")


async def websocket_auth(websocket: WebSocket, token: Optional[str]) -> None:
    if token != settings.api_token:
        await websocket.close(code=4401)
        raise WebSocketDisconnect


async def _consume_audio(audio_queue: "asyncio.Queue[Optional[bytes]]") -> dict:
    sample_rate = 16000
    speech_client = get_speech_client()
    loop = asyncio.get_running_loop()

    def recognize_sync() -> str:
        streaming_config = speech.StreamingRecognitionConfig(
            config=speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                language_code="ja-JP",
                sample_rate_hertz=sample_rate,
                enable_automatic_punctuation=True,
            ),
            interim_results=True,
            single_utterance=True,
        )

        def request_iter():
            yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
            while True:
                fut = asyncio.run_coroutine_threadsafe(audio_queue.get(), loop)
                chunk = fut.result()
                if chunk is None:
                    break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        responses = speech_client.streaming_recognize(requests=request_iter())
        final_text = ""
        for response in responses:
            for result in response.results:
                if result.is_final:
                    final_text = result.alternatives[0].transcript
        return final_text

    transcript = await loop.run_in_executor(None, recognize_sync)
    return {"transcript": transcript, "sample_rate": sample_rate}


async def _generate_response(prompt: str) -> str:
    model = get_generative_model()
    persona_instruction = (
        "あなたはアンパンマンみたいに優しく、元気で、子どもに話しかけるような口調で日本語だけで返答します。"
    )
    response = model.generate_content(
        [
            {
                "role": "user",
                "parts": [
                    {"text": persona_instruction},
                    {"text": f"利用者の発話: {prompt}"},
                    {"text": "利用者への返答を一つの短い段落で作成してください。"},
                ],
            }
        ]
    )
    return response.candidates[0].content.parts[0].text  # type: ignore[index]


async def _synthesize_speech(text: str) -> bytes:
    tts_client = get_tts_client()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(language_code="ja-JP", name="ja-JP-Wavenet-D")
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        speaking_rate=1.1,
        pitch=2.0,
    )
    response = tts_client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config,
    )
    return response.audio_content


@app.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    await websocket.accept()
    await websocket_auth(websocket, token)

    audio_queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()

    async def receive_loop() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    await audio_queue.put(None)
                    break
                if "text" in message and message["text"]:
                    payload = json.loads(message["text"])
                    if payload.get("event") == "end":
                        await audio_queue.put(None)
                        break
                    continue
                data = message.get("bytes")
                if data:
                    await audio_queue.put(data)
        except WebSocketDisconnect:
            await audio_queue.put(None)

    receiver = asyncio.create_task(receive_loop())

    stt_result = await _consume_audio(audio_queue)
    await receiver

    transcript = stt_result["transcript"]
    if not transcript:
        await websocket.send_text(json.dumps({"event": "error", "message": "音声を認識できませんでした"}))
        await websocket.close()
        return

    response_text = await _generate_response(transcript)
    audio_bytes = await _synthesize_speech(response_text)

    await websocket.send_text(
        json.dumps(
            {
                "event": "result",
                "transcript": transcript,
                "response_text": response_text,
                "audio_format": "LINEAR16",
                "sample_rate": stt_result["sample_rate"],
                "audio_base64": base64.b64encode(audio_bytes).decode("utf-8"),
            }
        )
    )
    await websocket.close()


@app.post("/auth-check")
async def auth_check(token: str) -> JSONResponse:
    await enforce_token(token)
    return JSONResponse({"status": "authorized"})
