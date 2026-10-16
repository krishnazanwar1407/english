import os
import threading

import azure.cognitiveservices.speech as speechsdk


def transcribe_with_diarization(endpoint, key, audio_file, language="en-US", output_file="transcript.txt"):
    """Transcribe a long audio file with speaker diarization and save it to output_file."""
    speech_config = speechsdk.SpeechConfig(endpoint=endpoint, subscription=key)
    speech_config.speech_recognition_language = language
    audio_config = speechsdk.audio.AudioConfig(filename=audio_file)

    transcriber = speechsdk.transcription.ConversationTranscriber(
        speech_config=speech_config, audio_config=audio_config
    )

    done = threading.Event()
    lines = []

    def on_transcribed(evt):
        if evt.result.text:
            line = f"Speaker {evt.result.speaker_id}: {evt.result.text}"
            print(line)
            lines.append(line)

    transcriber.transcribed.connect(on_transcribed)
    transcriber.session_stopped.connect(lambda evt: done.set())
    transcriber.canceled.connect(lambda evt: done.set())

    transcriber.start_transcribing_async().get()
    done.wait()
    transcriber.stop_transcribing_async().get()

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return lines


if __name__ == "__main__":
    transcribe_with_diarization(
        endpoint="",  # e.g. https://<your-resource>.cognitiveservices.azure.com/
        key="",
        audio_file=r"C:\Users\kzsg9\Desktop\azure_speech\two_person_calls\call_small.wav",
        language="en-US",  # must match the spoken language, see notes below
    )
