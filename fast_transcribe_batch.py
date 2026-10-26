"""
Azure Speech - Fast transcription over REST with diarization, for every WAV in a folder.

Each file gets one JSON output in the format of the evaluation transcripts.
The output is written to OUTPUT_FOLDER. Nothing is uploaded to blob storage;
the blob URLs in the JSON are only strings.

Environment variables:
    AZURE_TENANT_ID       Directory (tenant) ID
    AZURE_CLIENT_ID       Application (client) ID
    AZURE_CLIENT_SECRET   Client secret value
    SPEECH_ENDPOINT       Endpoint of the Speech resource,
                          e.g. https://<resource-name>.cognitiveservices.azure.com

Install (into the py12 env):
    pip install requests azure-identity
"""

import datetime as dt
import json
import os
import uuid
from pathlib import Path

import requests
from azure.identity import ClientSecretCredential

AUDIO_FOLDER = Path("audio")
OUTPUT_FOLDER = Path("output")
LOCALES = ["en-US"]
MAX_SPEAKERS = 2
API_VERSION = "2025-10-15"
SPEECH_SCOPE = "https://cognitiveservices.azure.com/.default"

BLOB_BASE = "https://.blob.core.windows.net/evaluation"
REGION = "apac"

# False turns off TLS certificate verification for both the token request and the
# transcription call. The bearer token can be intercepted on the network. Dev/test only.
VERIFY_SSL = False


def format_timestamp(ms: int) -> str:
    """Format milliseconds as HH:MM:SS.cc (hundredths of a second)."""
    centiseconds = int(ms) // 10
    seconds, cs = divmod(centiseconds, 100)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def call_transcribe(credential: ClientSecretCredential, endpoint: str, audio_path: Path) -> dict:
    token = credential.get_token(SPEECH_SCOPE).token
    url = f"{endpoint.rstrip('/')}/speechtotext/transcriptions:transcribe"
    definition = {
        "locales": LOCALES,
        "diarization": {"maxSpeakers": MAX_SPEAKERS, "enabled": True},
    }
    with open(audio_path, "rb") as audio_file:
        files = {
            "audio": (audio_path.name, audio_file, "application/octet-stream"),
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
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def build_output(audio_path: Path, api_result: dict, run_timestamp: str) -> dict:
    stem = audio_path.stem
    phrases = api_result.get("phrases", [])
    created_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"

    return {
        "transcription": " ".join(p["text"] for p in phrases),
        "detailed_transcription": [
            {
                "Speaker": f"Speaker {p.get('speaker')}",
                "Start Time": format_timestamp(p["offsetMilliseconds"]),
                "End Time": format_timestamp(p["offsetMilliseconds"] + p["durationMilliseconds"]),
                "Transcribed Content": p["text"],
            }
            for p in phrases
        ],
        "source_reference_id": stem,
        "request_id": str(uuid.uuid4()),
        "audio_file_blob_path": f"{BLOB_BASE}/evaluation-audio/{REGION}/{LOCALES[0]}/{audio_path.name}",
        "output_blob_url": f"{BLOB_BASE}/evaluation-model-output-transcripts/{REGION}/{run_timestamp}/{stem}.json",
        "created_at": created_at,
        "progress": {"status": "completed"},
    }


def main() -> None:
    credential = ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
        connection_verify=VERIFY_SSL,
    )
    endpoint = os.environ["SPEECH_ENDPOINT"]

    audio_files = sorted(AUDIO_FOLDER.glob("*.wav"))
    if not audio_files:
        raise SystemExit(f"No .wav files found in {AUDIO_FOLDER.resolve()}")

    OUTPUT_FOLDER.mkdir(exist_ok=True)
    run_timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    for audio_path in audio_files:
        try:
            api_result = call_transcribe(credential, endpoint, audio_path)
            output = build_output(audio_path, api_result, run_timestamp)
            out_path = OUTPUT_FOLDER / f"{audio_path.stem}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=4)
            print(f"OK    {audio_path.name} -> {out_path}")
        except Exception as exc:
            print(f"FAIL  {audio_path.name}: {exc}")


if __name__ == "__main__":
    main()
