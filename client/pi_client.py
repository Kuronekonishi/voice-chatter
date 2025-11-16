"""Raspberry Pi 用 WebSocket 音声クライアント.

- マイク音声を Google Cloud 上の FastAPI バックエンドへストリーミングする
- LLM の応答を受け取り、Text-to-Speech の音声を再生する

使用前準備:
    pip install -r client/requirements.txt
    cp sample.env .env
    python client/pi_client.py
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
import websockets

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_MS = 100
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_MS / 1000)


def load_config(env_path: Optional[Path] = None) -> dict:
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()
    config = {
        "backend_ws_url": os.environ.get("BACKEND_WS_URL"),
        "api_token": os.environ.get("API_TOKEN"),
    }
    missing = [key for key, value in config.items() if not value]
    if missing:
        raise RuntimeError(f"環境変数が不足しています: {', '.join(missing)}")
    return config


async def stream_audio_once(backend_ws_url: str, api_token: str) -> None:
    uri = f"{backend_ws_url}?token={api_token}"
    logging.info("WebSocket 接続先: %s", uri)
    async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
        loop = asyncio.get_running_loop()
        audio_queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        stop_event = asyncio.Event()

        def audio_callback(indata, frames, time, status):  # type: ignore[override]
            if status:
                logging.warning("入力ストリームのステータス: %s", status)
            loop.call_soon_threadsafe(audio_queue.put_nowait, bytes(indata))

        async def sender() -> None:
            while True:
                if stop_event.is_set() and audio_queue.empty():
                    break
                chunk = await audio_queue.get()
                await ws.send(chunk)

        sender_task = asyncio.create_task(sender())

        logging.info("録音を開始します。話し終えたら Enter キーを押してください。")
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_SIZE,
            dtype="int16",
            channels=CHANNELS,
            callback=audio_callback,
        ):
            await loop.run_in_executor(None, input)
            stop_event.set()

        await sender_task
        await ws.send(json.dumps({"event": "end"}))

        logging.info("バックエンドからの応答を待機しています…")
        response_text: Optional[str] = None
        while True:
            message = await ws.recv()
            if isinstance(message, bytes):
                logging.debug("未処理のバイナリメッセージを受信")
                continue
            payload = json.loads(message)
            if payload.get("event") == "result":
                response_text = payload.get("response_text")
                audio_base64 = payload.get("audio_base64")
                if audio_base64:
                    audio_bytes = base64.b64decode(audio_base64)
                    play_audio(audio_bytes)
                transcript = payload.get("transcript")
                logging.info("ユーザー: %s", transcript)
                logging.info("アンパンマン風応答: %s", response_text)
                break
            elif payload.get("event") == "error":
                logging.error("バックエンドエラー: %s", payload.get("message"))
                break

        logging.info("会話を終了します。")


def play_audio(audio_bytes: bytes) -> None:
    logging.info("音声応答を再生します。")
    audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
    with sd.RawOutputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SIZE,
        dtype="int16",
        channels=CHANNELS,
    ) as stream:
        stream.write(audio_array.tobytes())


async def main_async(env_path: Optional[Path]) -> None:
    config = load_config(env_path)
    while True:
        logging.info("Enter キーで録音を開始します (Ctrl+C で終了)。")
        await asyncio.get_running_loop().run_in_executor(None, input)
        try:
            await stream_audio_once(config["backend_ws_url"], config["api_token"])
        except websockets.WebSocketException as exc:
            logging.error("WebSocket 通信エラー: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logging.exception("想定外のエラーが発生しました: %s", exc)
        finally:
            logging.info("再び Enter キーで新しい会話を開始できます。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Raspberry Pi 音声対話クライアント")
    parser.add_argument("--env", type=Path, default=None, help=".env ファイルへのパス")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(main_async(args.env))
    except KeyboardInterrupt:
        logging.info("ユーザー操作により終了しました。")


if __name__ == "__main__":
    main()
