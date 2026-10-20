# pip install "azure-cognitiveservices-speech>=1.52.0"
import os
import threading
import azure.cognitiveservices.speech as speechsdk

speech_key = os.environ["AZURE_SPEECH_KEY"]
endpoint = os.environ["AZURE_SPEECH_ENDPOINT"].rstrip("/") + "/"  # https://<custom-name>.cognitiveservices.azure.com/

def speech_recognize_continuous_from_file():
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, endpoint=endpoint)
    speech_config.model = "mai-transcribe-2-streaming"

    audio_config = speechsdk.audio.AudioConfig(filename="your_file_name.wav")
    speech_recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    done = threading.Event()

    def on_canceled(evt):
        details = speechsdk.CancellationDetails(evt.result)
        print("CANCELED")
        print("  reason:        {}".format(details.reason))
        print("  code:          {}".format(details.code))
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
