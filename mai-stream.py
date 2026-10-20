# pip install "azure-cognitiveservices-speech>=1.52.0" azure-identity
import os
import threading
import azure.cognitiveservices.speech as speechsdk
from azure.identity import ClientSecretCredential

tenant_id = os.environ["AZURE_TENANT_ID"]
client_id = os.environ["AZURE_CLIENT_ID"]
client_secret = os.environ["AZURE_CLIENT_SECRET"]
endpoint = os.environ["AZURE_SPEECH_ENDPOINT"].rstrip("/") + "/"  # https://<custom-name>.cognitiveservices.azure.com/

credential = ClientSecretCredential(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret,
)

def speech_recognize_continuous_from_file():
    speech_config = speechsdk.SpeechConfig(token_credential=credential, endpoint=endpoint)
    speech_config.model = "mai-transcribe-2-streaming"

    audio_config = speechsdk.audio.AudioConfig(filename="your_file_name.wav")
    speech_recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    done = threading.Event()

    def on_canceled(evt):
        details = evt.cancellation_details
        print("CANCELED")
        print("  reason:        {}".format(details.reason))
        print("  error_code:    {}".format(details.error_code))
        print("  error_details: {}".format(details.error_details))
        done.set()

    def on_session_stopped(evt):
        print("SESSION STOPPED")
        done.set()

    speech_recognizer.recognizing.connect(lambda evt: print("RECOGNIZING: {}".format(evt.result.text)))
    speech_recognizer.recognized.connect(lambda evt: print("RECOGNIZED: {}".format(evt.result.text)))
    speech_recognizer.session_started.connect(lambda evt: print("SESSION STARTED"))
    speech_recognizer.session_stopped.connect(on_session_stopped)
    speech_recognizer.canceled.connect(on_canceled)

    speech_recognizer.start_continuous_recognition()
    done.wait()
    speech_recognizer.stop_continuous_recognition()

speech_recognize_continuous_from_file()
