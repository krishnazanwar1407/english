"""
Azure Speech - Fast transcription over REST with speaker diarization.

Authenticates with a Microsoft Entra ID service principal (client ID + client secret)
and calls the Fast Transcription REST API directly with requests.

Environment variables:
    AZURE_TENANT_ID       Directory (tenant) ID
    AZURE_CLIENT_ID       Application (client) ID
    AZURE_CLIENT_SECRET   Client secret value
    SPEECH_ENDPOINT       Endpoint of the Speech resource,
                          e.g. https://<resource-name>.cognitiveservices.azure.com

Install (into the py12 env):
    pip install requests azure-identity
"""

import json
import os

import requests
from azure.identity import ClientSecretCredential

AUDIO_PATH = "audio.wav"
LOCALES = ["en-US"]
MAX_SPEAKERS = 2
API_VERSION = "2025-10-15"
SPEECH_SCOPE = "https://cognitiveservices.azure.com/.default"

# False turns off TLS certificate verification for both the token request and the
# transcription call. The bearer token can be intercepted on the network. Dev/test only.
VERIFY_SSL = False


def main() -> None:
    credential = ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
        connection_verify=VERIFY_SSL,
    )
    token = credential.get_token(SPEECH_SCOPE).token

    url = f"{os.environ['SPEECH_ENDPOINT'].rstrip('/')}/speechtotext/transcriptions:transcribe"
    definition = {
        "locales": LOCALES,
        "diarization": {"maxSpeakers": MAX_SPEAKERS, "enabled": True},
    }

    with open(AUDIO_PATH, "rb") as audio_file:
        files = {
            "audio": (os.path.basename(AUDIO_PATH), audio_file, "application/octet-stream"),
            "definition": (None, json.dumps(definition), "application/json"),
        }
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"api-version": API_VERSION},
            files=files,
            verify=VERIFY_SSL,
            timeout=600,
        )

    if not response.ok:
        raise SystemExit(f"Transcription failed: HTTP {response.status_code}\n{response.text}")

    result = response.json()
    for phrase in result.get("phrases", []):
        speaker = phrase.get("speaker", "not returned")
        print(f"Speaker {speaker}: {phrase.get('text')}")

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\nFull result saved to result.json")


if __name__ == "__main__":
    main()
