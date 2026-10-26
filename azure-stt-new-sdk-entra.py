"""
Azure Speech - Fast transcription using the Speech Transcription SDK (azure-ai-transcription).

Authenticates with a Microsoft Entra ID service principal (client ID + client secret).

Environment variables:
    AZURE_TENANT_ID       Directory (tenant) ID
    AZURE_CLIENT_ID       Application (client) ID
    AZURE_CLIENT_SECRET   Client secret value
    SPEECH_ENDPOINT       Endpoint of the Speech resource,
                          e.g. https://<resource-name>.cognitiveservices.azure.com

Install (into the py12 env):
    pip install azure-ai-transcription azure-identity
"""

import os

from azure.ai.transcription import TranscriptionClient
from azure.ai.transcription.models import (
    TranscriptionContent,
    TranscriptionDiarizationOptions,
    TranscriptionOptions,
)
from azure.identity import ClientSecretCredential

AUDIO_PATH = "audio.wav"
LOCALES = ["en-US"]
MAX_SPEAKERS = 5

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
    client = TranscriptionClient(
        endpoint=os.environ["SPEECH_ENDPOINT"],
        credential=credential,
        connection_verify=VERIFY_SSL,
    )

    with open(AUDIO_PATH, "rb") as audio_file:
        options = TranscriptionOptions(
            locales=LOCALES,
            diarization_options=TranscriptionDiarizationOptions(max_speakers=MAX_SPEAKERS),
        )
        request = TranscriptionContent(definition=options, audio=audio_file)
        result = client.transcribe(request)

    for phrase in result.phrases:
        print(f"Speaker {phrase.speaker}: {phrase.text}")


if __name__ == "__main__":
    main()
