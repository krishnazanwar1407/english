import asyncio
import json
import os
import tempfile

import azure.cognitiveservices.speech as speechsdk
from fastapi import FastAPI, UploadFile, WebSocket
from fastapi.responses import FileResponse, StreamingResponse

from azure_stt_diarization_fn import format_time

SPEECH_ENDPOINT = ""
SPEECH_KEY = ""


app = FastAPI()


def make_transcriber(audio_config, language, loop, queue):
    """Build a ConversationTranscriber whose SDK callbacks push items onto an asyncio queue.

    The SDK fires callbacks on its own threads, so every push goes through call_soon_threadsafe.
    A None item means the session is over.
    """
    speech_config = speechsdk.SpeechConfig(endpoint=SPEECH_ENDPOINT, subscription=SPEECH_KEY)
    speech_config.speech_recognition_language = language
    transcriber = speechsdk.transcription.ConversationTranscriber(
        speech_config=speech_config, audio_config=audio_config
    )
    speaker_labels = {}

    def push(item):
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def on_transcribing(evt):
        if evt.result.text:
            push({"type": "partial", "text": evt.result.text})

    def on_transcribed(evt):
        if not evt.result.text:
            return
        speaker_id = evt.result.speaker_id
        if speaker_id not in speaker_labels:
            speaker_labels[speaker_id] = f"Speaker {len(speaker_labels)}"
        start_ms = evt.result.offset // 10_000
        end_ms = (evt.result.offset + evt.result.duration) // 10_000
        push({
            "type": "final",
            "speaker": speaker_labels[speaker_id],
            "start": format_time(start_ms),
            "end": format_time(end_ms),
            "text": evt.result.text,
        })

    def on_canceled(evt):
        # EndOfStream is the normal way a finished file or closed mic stream ends, so only real errors are reported.
        if evt.cancellation_details.reason == speechsdk.CancellationReason.Error:
            push({"type": "error", "text": evt.cancellation_details.error_details})
        push(None)

    transcriber.transcribing.connect(on_transcribing)
    transcriber.transcribed.connect(on_transcribed)
    transcriber.canceled.connect(on_canceled)
    transcriber.session_stopped.connect(lambda evt: push(None))
    return transcriber


@app.get("/")
async def index():
    return FileResponse("mic_test.html")


@app.post("/transcribe/file")
async def transcribe_file(file: UploadFile, language: str = "en-US"):
    """Upload an audio file, get back newline-delimited JSON as results arrive."""
    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        path = tmp.name

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    audio_config = speechsdk.audio.AudioConfig(filename=path)
    transcriber = make_transcriber(audio_config, language, loop, queue)

    async def events():
        finished = False
        try:
            await asyncio.to_thread(transcriber.start_transcribing_async().get)
            while (item := await queue.get()) is not None:
                yield json.dumps(item, ensure_ascii=False) + "\n"
            finished = True
        finally:
            if not finished:
                # Client disconnected mid-file: stop the SDK so it stops reading the file.
                await asyncio.to_thread(transcriber.stop_transcribing_async().get)
            try:
                os.remove(path)
            except PermissionError:
                # Windows: the SDK can still hold the file handle briefly. The temp file is left for the OS to clean up.
                pass

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.websocket("/transcribe/mic")
async def transcribe_mic(websocket: WebSocket, language: str = "en-US"):
    """Receive raw 16 kHz, 16-bit, mono PCM from the client; send back results as JSON.

    The client sends binary frames of audio, then the text message "end" when it is done.
    """
    await websocket.accept()

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    stream_format = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    transcriber = make_transcriber(audio_config, language, loop, queue)

    async def sender():
        while (item := await queue.get()) is not None:
            await websocket.send_json(item)

    send_task = asyncio.create_task(sender())
    try:
        await asyncio.to_thread(transcriber.start_transcribing_async().get)
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect" or message.get("text") == "end":
                break
            if message.get("bytes"):
                push_stream.write(message["bytes"])
        # Closing the push stream tells Azure no more audio is coming; it flushes the rest and
        # fires session_stopped, which ends the sender.
        push_stream.close()
        await asyncio.wait_for(send_task, timeout=30)
        await websocket.close()
    finally:
        # Closing the push stream ends the SDK session on its own. Calling stop_transcribing after
        # that never returned in testing, so we don't call it here.
        push_stream.close()
        send_task.cancel()
