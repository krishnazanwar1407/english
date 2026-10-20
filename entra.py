# pip install requests azure-identity
import json
import os

import requests
from azure.identity import ClientSecretCredential

# Environment variables: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_SPEECH_ENDPOINT
tenant_id = os.environ["AZURE_TENANT_ID"]
client_id = os.environ["AZURE_CLIENT_ID"]
client_secret = os.environ["AZURE_CLIENT_SECRET"]
endpoint = os.environ["AZURE_SPEECH_ENDPOINT"].rstrip("/")  # e.g. https://<your-resource>.cognitiveservices.azure.com

AUDIO_FILE = "sample.wav"  # WAV, MP3, or FLAC

url = f"{endpoint}/speechtotext/transcriptions:transcribe"
params = {"api-version": "2025-10-15"}

definition = {
    "enhancedMode": {
        "enabled": True,
        "model": "MAI-Transcribe-2",
    },
    "diarization": {"enabled": False},
    "modelOptions": {
        "timestamps": "word",
        "transcribeStyle": "clean",
    },
}

credential = ClientSecretCredential(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret,
)
token = credential.get_token("https://cognitiveservices.azure.com/.default").token

headers = {"Authorization": f"Bearer {token}"}

with open(AUDIO_FILE, "rb") as audio:
    files = {
        "audio": (os.path.basename(AUDIO_FILE), audio, "audio/wav"),
        "definition": (None, json.dumps(definition), "application/json"),
    }
    response = requests.post(url, params=params, headers=headers, files=files, timeout=300)

if not response.ok:
    print(response.status_code)
    print(response.text)
    raise SystemExit(1)

result = response.json()
print("Transcript:", result["combinedPhrases"][0]["text"])
for phrase in result.get("phrases", []):
    print(f'[{phrase["offsetMilliseconds"]} ms] {phrase["text"]}')
