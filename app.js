const WS_URL = "ws://127.0.0.1:8000/transcribe/mic";
const SAMPLE_RATE = 16000;

const button = document.getElementById("mic-button");
const statusEl = document.getElementById("status");
const transcript = document.getElementById("transcript");
const partialEl = document.getElementById("partial");

// Holds everything that must be released when listening stops. Null when idle.
let session = null;

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

function setListening(listening) {
  button.textContent = listening ? "Stop" : "Start listening";
  button.classList.toggle("listening", listening);
  button.disabled = false;
}

// Convert float samples (-1..1) from the browser into 16-bit little-endian PCM.
function toPcm16(samples) {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return pcm.buffer;
}

function showFinal(msg) {
  partialEl.textContent = "";
  const p = document.createElement("p");

  const speaker = document.createElement("span");
  speaker.className = "speaker";
  speaker.textContent = msg.speaker;

  const time = document.createElement("span");
  time.className = "time";
  time.textContent = ` ${msg.start} – ${msg.end}  `;

  p.append(speaker, time, document.createTextNode(msg.text));
  transcript.append(p);
}

function handleServerMessage(event) {
  const msg = JSON.parse(event.data);
  if (msg.type === "partial") {
    partialEl.textContent = msg.text;
  } else if (msg.type === "final") {
    showFinal(msg);
  } else if (msg.type === "error") {
    setStatus(`Error: ${msg.text}`, true);
  }
}

async function startListening() {
  button.disabled = true;
  setStatus("Requesting microphone...");

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setStatus(`Microphone unavailable: ${err.message}`, true);
    setListening(false);
    return;
  }

  const audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
  await audioContext.audioWorklet.addModule("pcm-worklet.js");

  const source = audioContext.createMediaStreamSource(stream);
  const capture = new AudioWorkletNode(audioContext, "pcm-capture");
  const socket = new WebSocket(WS_URL);

  capture.port.onmessage = (event) => {
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(toPcm16(event.data));
    }
  };
  socket.onmessage = handleServerMessage;
  socket.onopen = () => setStatus("Listening...");
  socket.onerror = () => setStatus("Connection error. Is the backend running?", true);
  socket.onclose = () => {
    // Happens after Stop (the server closes once Azure finishes) or if the connection drops.
    releaseMicrophone();
    setListening(false);
    if (!statusEl.classList.contains("error")) setStatus("Idle");
  };

  source.connect(capture);
  capture.connect(audioContext.destination);
  session = { stream, audioContext, capture, socket };
  setListening(true);
}

// Stops the mic and audio processing. Does not close the socket, so Azure's final results can still arrive.
function releaseMicrophone() {
  if (!session) return;
  session.capture.disconnect();
  session.stream.getTracks().forEach((track) => track.stop());
  session.audioContext.close();
  session = null;
}

function stopListening() {
  if (!session) return;
  const { socket } = session;
  button.disabled = true;
  setStatus("Finishing...");
  releaseMicrophone();
  socket.send("end");
}

button.addEventListener("click", () => {
  if (session) {
    stopListening();
  } else {
    startListening();
  }
});

setListening(false);
