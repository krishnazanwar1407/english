import json

import requests

# pip install requests
API_VERSION = "2024-11-15"


def transcribe_fast(endpoint, key, audio_file, language="en-US", max_speakers=2, output_file="transcript_fast.txt"):
    """Transcribe an audio file with the Fast transcription API and speaker diarization."""
    url = f"{endpoint.rstrip('/')}/speechtotext/transcriptions:transcribe?api-version={API_VERSION}"
    definition = {
        "locales": [language],
        "diarization": {"enabled": True},
    }

    with open(audio_file, "rb") as f:
        response = requests.post(
            url,
            headers={"Ocp-Apim-Subscription-Key": key},
            files={
                "audio": (audio_file, f, "audio/wav"),
                "definition": (None, json.dumps(definition), "application/json"),
            },
            timeout=3600,
        )
    response.raise_for_status()

    lines = []
    for phrase in response.json()["phrases"]:
        line = f"Speaker {phrase['speaker']}: {phrase['text']}"
        print(line)
        lines.append(line)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return lines


if __name__ == "__main__":
    transcribe_fast(
        endpoint="https://eastus.api.cognitive.microsoft.com/",
        key="",
        audio_file=r"C:\Users\kzsg9\Desktop\azure_speech\two_person_calls\call_small.wav",
        language="en-US",
    )
