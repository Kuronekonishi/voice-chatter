"""
Raspberry Pi クライアント
- 環境変数は .env から読み込む
- マイクで録音（デフォルト N 秒）して backend の /process-audio に送信
- 再生：返却された WAV バイト列を再生

簡易使い方:
    pip install -r pi/requirements.txt
    cp .env.example .env
    # .env を編集
    python pi/client.py

※ push-to-talk や連続ストリーミングを行いたい場合は、録音ロジックを拡張してください。
"""

import os
import io
import time
import wave
import requests
import argparse
import logging
from dotenv import load_dotenv

try:
    import sounddevice as sd
    import numpy as np
except Exception:
    sd = None

load_dotenv()

API_URL = os.environ.get("API_URL", "http://localhost:8080/process-audio")
API_TOKEN = os.environ.get("API_TOKEN", "changeme")
RECORD_SECONDS = int(os.environ.get("RECORD_SECONDS", "4"))
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "16000"))
CHANNELS = 1

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def record_wav_bytes(duration: int = RECORD_SECONDS, sample_rate: int = SAMPLE_RATE) -> bytes:
    if sd is None:
        raise RuntimeError("sounddevice が利用できません。Pi 上で実行していることを確認してください。")

    logger.info(f"録音を開始します: {duration}s、{sample_rate}Hz")
    recording = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=CHANNELS, dtype='int16')
    sd.wait()
    audio_data = recording.flatten().tobytes()

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data)
    wav_bytes = buf.getvalue()
    logger.info(f"録音完了: {len(wav_bytes)} bytes")
    return wav_bytes


def play_wav_bytes(wav_bytes: bytes):
    if sd is None:
        logger.warning("sounddevice が利用できないため再生できません。")
        return
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, 'rb') as wf:
        nframes = wf.getnframes()
        framerate = wf.getframerate()
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        data = wf.readframes(nframes)
    # numpy に変換して再生
    audio = np.frombuffer(data, dtype=np.int16)
    if nchannels > 1:
        audio = audio.reshape((-1, nchannels))
    sd.play(audio, framerate)
    sd.wait()


def send_audio_and_receive(wav_bytes: bytes):
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    files = {"audio": ("speech.wav", wav_bytes, "audio/wav")}
    try:
        resp = requests.post(API_URL, headers=headers, files=files, stream=True, timeout=60)
    except Exception as e:
        logger.exception("バックエンドへの送信に失敗しました")
        return None
    if resp.status_code != 200:
        logger.error(f"バックエンドエラー: {resp.status_code} {resp.text}")
        return None
    return resp.content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=RECORD_SECONDS, help="録音秒数")
    args = parser.parse_args()

    try:
        wav = record_wav_bytes(args.duration)
        logger.info("サーバに送信します...")
        resp_audio = send_audio_and_receive(wav)
        if resp_audio:
            logger.info("サーバから音声を受信しました。再生します...")
            play_wav_bytes(resp_audio)
        else:
            logger.error("サーバから音声が返されませんでした。")
    except Exception as e:
        logger.exception("エラーが発生しました")


if __name__ == "__main__":
    main()
