"""
Print the claims of the Entra token used by fast_transcribe_sdk2.py.

Shows only aud, iss, tid, appid and oid. The token and the client secret are not printed.

Environment variables (same as fast_transcribe_sdk2.py):
    AZURE_TENANT_ID
    AZURE_CLIENT_ID
    AZURE_CLIENT_SECRET
"""

import base64
import json
import os

from azure.identity import ClientSecretCredential

from fast_transcribe_sdk2 import VERIFY_SSL


def decode_claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def main() -> None:
    credential = ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
        connection_verify=VERIFY_SSL,
    )
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    claims = decode_claims(token)
    for key in ["aud", "iss", "tid", "appid", "oid"]:
        print(f"{key}: {claims.get(key)}")


if __name__ == "__main__":
    main()
