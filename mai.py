# pip install requests
import json
import requests
SPEECH_ENDPOINT="https://eastus.api.cognitive.microsoft.com/"  # e.g. https://<your-resource>.cognitiveservices.azure.com/
SPEECH_KEY=""

AUDIO_FILE = r"C:\Users\kzsg9\Desktop\azure_speech\two_person_calls\call_small.wav"  # WAV, MP3, or FLAC; under 2 hours and 250 MB

url = f"{SPEECH_ENDPOINT}/speechtotext/transcriptions:transcribe"
params = {"api-version": "2025-10-15"}

definition = {
    "enhancedMode": {
        "enabled": True,
        "model": "MAI-Transcribe-2",
    },
    "diarization": {"enabled": False},   # set True for speaker labels (needs longer recordings)
    "modelOptions": {
        "timestamps": "word",            # "word" | "segment" | "none"
        "transcribeStyle": "clean",      # "verbatim" (default) | "clean"
    },
    # "locales": ["en-US"],              # optional; omit to auto-detect language
    # "phraseList": {"phrases": ["Contoso", "Azure Speech"]},  # keyword biasing
}

headers = {"Ocp-Apim-Subscription-Key": SPEECH_KEY}

with open(AUDIO_FILE, "rb") as audio:
    files = {
        "audio": (AUDIO_FILE, audio, "audio/wav"),
        "definition": (None, json.dumps(definition), "application/json"),
    }
    response = requests.post(url, params=params, headers=headers, files=files, timeout=300)

response.raise_for_status()
result = response.json()

print("Transcript:", result["combinedPhrases"][0]["text"])
for phrase in result.get("phrases", []):
    print(f'[{phrase["offsetMilliseconds"]} ms] {phrase["text"]}')
