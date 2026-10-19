import json
import threading

import azure.cognitiveservices.speech as speechsdk


def format_time(milliseconds):
    """Convert milliseconds to HH:MM:SS.cc."""
    total_cs = milliseconds // 10
    hours, rem = divmod(total_cs, 360000)
    minutes, rem = divmod(rem, 6000)
    seconds, centis = divmod(rem, 100)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def transcribe_with_diarization(endpoint, key, audio_file, language="en-US", output_file="transcript.json"):
    """Transcribe a long audio file with speaker diarization and save the result as JSON."""
    speech_config = speechsdk.SpeechConfig(endpoint=endpoint, subscription=key)
    speech_config.speech_recognition_language = language
    audio_config = speechsdk.audio.AudioConfig(filename=audio_file)

    transcriber = speechsdk.transcription.ConversationTranscriber(
        speech_config=speech_config, audio_config=audio_config
    )

    done = threading.Event()
    detailed = []
    speaker_labels = {}

    def on_transcribed(evt):
        if not evt.result.text:
            return
        # SDK speaker ids look like "Guest-1"; map them to "Speaker 0", "Speaker 1", ... in order of appearance
        speaker_id = evt.result.speaker_id
        if speaker_id not in speaker_labels:
            speaker_labels[speaker_id] = f"Speaker {len(speaker_labels)}"
        start_ms = evt.result.offset // 10_000
        end_ms = (evt.result.offset + evt.result.duration) // 10_000
        detailed.append({
            "Speaker": speaker_labels[speaker_id],
            "Start Time": format_time(start_ms),
            "End Time": format_time(end_ms),
            "Transcribed Content": evt.result.text,
        })

    transcriber.transcribed.connect(on_transcribed)
    transcriber.session_stopped.connect(lambda evt: done.set())
    transcriber.canceled.connect(lambda evt: done.set())

    transcriber.start_transcribing_async().get()
    done.wait()
    transcriber.stop_transcribing_async().get()

    result = {
        "transcription": " ".join(item["Transcribed Content"] for item in detailed),
        "detailed_transcription": detailed,
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=4)
    return result


if __name__ == "__main__":
    transcribe_with_diarization(
        endpoint="https://eastus.api.cognitive.microsoft.com/",  # e.g. https://<your-resource>.cognitiveservices.azure.com/
        key="",
        audio_file=r"C:\Users\kzsg9\Desktop\azure_speech\two_person_calls\call_small.wav",
        language="en-US",  # must match the spoken language, see notes below
    )
