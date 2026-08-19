const AUDIO_MAGIC = "EVAF";
const AUDIO_VERSION = 1;
const BINARY_AUDIO_SUBPROTOCOL = "eeveetuber.v1.binary-audio";
const AUDIO_FIXED_HEADER_BYTES = 124;
const FLAG_FINAL_CHUNK = 1;
const MAX_VISIBLE_EVENTS = 500;
const VOICE_INPUT_MAGIC = "EVIF";
const VOICE_INPUT_VERSION = 1;
const VOICE_INPUT_FIXED_HEADER_BYTES = 52;
const VOICE_INPUT_ENCODING_PCM_S16LE = 1;
const VOICE_INPUT_CHANNELS = 1;
const MAX_CAPTURE_BUFFERED_AMOUNT = 1024 * 1024;
const DEFAULT_VOICE_INPUT = Object.freeze({
  enabled: false,
  sampleRateHz: 16000,
  frameDurationMs: 20,
  maxFrameBytes: 4096,
});

const ui = {
  endpoint: document.querySelector("#endpoint"),
  connect: document.querySelector("#connect"),
  status: document.querySelector("#status"),
  form: document.querySelector("#turn-form"),
  text: document.querySelector("#turn-text"),
  history: document.querySelector("#history"),
  events: document.querySelector("#event-log"),
  clear: document.querySelector("#clear-history"),
  audioToggle: document.querySelector("#audio-toggle"),
  micStart: document.querySelector("#mic-start"),
  micStop: document.querySelector("#mic-stop"),
  micState: document.querySelector("#mic-state"),
  micTranscript: document.querySelector("#mic-transcript"),
  controls: [...document.querySelectorAll("[data-action]")],
};

const state = {
  socket: null,
  sessionId: null,
  audioEnabled: true,
  audioQueue: Promise.resolve(),
  audioEpoch: 0,
  activeAudio: null,
  finishActiveAudio: null,
  pendingSegments: new Map(),
  audioGeneration: null,
  voiceInput: { ...DEFAULT_VOICE_INPUT },
  voiceInputReady: false,
  mic: {
    token: 0,
    starting: false,
    active: false,
    streamId: null,
    sequence: 0n,
    frameSamples: 0,
    frameDurationNs: 0n,
    nextCapturedAtNs: 0n,
    mediaStream: null,
    context: null,
    source: null,
    worklet: null,
    mute: null,
  },
};

ui.endpoint.value = defaultEndpoint();
setConnectedControls(false);

ui.connect.addEventListener("click", () => {
  if (state.socket) void disconnect();
  else connect();
});

ui.micStart.addEventListener("click", () => void startMicrophone());
ui.micStop.addEventListener("click", () =>
  void stopMicrophone({ notifyServer: true, statusText: "Microphone idle" }),
);

ui.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = ui.text.value.trim();
  if (!text || !send({ protocol_version: 1, type: "turn.text", text })) return;
  appendHistory("owner", text);
  ui.text.value = "";
});

ui.clear.addEventListener("click", () => {
  ui.history.replaceChildren();
  ui.events.replaceChildren();
});

ui.audioToggle.addEventListener("click", () => {
  state.audioEnabled = !state.audioEnabled;
  ui.audioToggle.textContent = state.audioEnabled ? "Audio enabled" : "Audio muted";
  ui.audioToggle.setAttribute("aria-pressed", String(state.audioEnabled));
  if (!state.audioEnabled) stopActiveAudio("cancelled");
});

for (const control of ui.controls) {
  control.addEventListener("click", () => {
    const action = control.dataset.action;
    if (action === "stop_speech" || action === "kill_session") stopActiveAudio("cancelled");
    if (action === "kill_session") {
      void stopMicrophone({ notifyServer: true, statusText: "Microphone idle" });
    }
    send({ protocol_version: 1, type: "operator.control", action });
  });
}

function defaultEndpoint() {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/v1/ws`;
}

function connect() {
  state.voiceInput = { ...DEFAULT_VOICE_INPUT };
  state.voiceInputReady = false;
  let socket;
  try {
    socket = new WebSocket(ui.endpoint.value.trim(), [BINARY_AUDIO_SUBPROTOCOL]);
  } catch (error) {
    setStatus("error", String(error));
    return;
  }
  socket.binaryType = "arraybuffer";
  state.socket = socket;
  setStatus("processing", "Connecting…");
  socket.addEventListener("open", () => {
    setConnectedControls(true);
    setStatus("connected", "Connected");
    setMicrophoneState("idle", "Waiting for voice input capability…");
    addEvent("socket.open", ui.endpoint.value.trim());
  });
  socket.addEventListener("message", (event) => handleMessage(event.data));
  socket.addEventListener("error", () => setStatus("error", "WebSocket error"));
  socket.addEventListener("close", (event) => {
    addEvent("socket.close", `${event.code} ${event.reason || ""}`.trim());
    state.socket = null;
    state.sessionId = null;
    state.voiceInput = { ...DEFAULT_VOICE_INPUT };
    state.voiceInputReady = false;
    void stopMicrophone({ notifyServer: false, statusText: "Microphone idle (disconnected)" });
    stopActiveAudio("cancelled");
    state.audioGeneration = null;
    setConnectedControls(false);
    setStatus("disconnected", "Disconnected");
  });
}

async function disconnect() {
  const socket = state.socket;
  await stopMicrophone({ notifyServer: true, statusText: "Microphone idle" });
  socket?.close(1000, "operator disconnected");
}

function send(message) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return false;
  state.socket.send(JSON.stringify(message));
  return true;
}

function applyVoiceInputCapabilities(data) {
  state.voiceInputReady = true;
  const capability = data?.voice_input;
  const frameSamples = capability
    ? (capability.sample_rate_hz * capability.frame_duration_ms) / 1000
    : 0;
  const valid =
    capability &&
    typeof capability.enabled === "boolean" &&
    Number.isInteger(capability.sample_rate_hz) &&
    capability.sample_rate_hz > 0 &&
    Number.isInteger(capability.frame_duration_ms) &&
    capability.frame_duration_ms > 0 &&
    Number.isInteger(capability.max_frame_bytes) &&
    capability.max_frame_bytes > 0 &&
    (capability.channels === undefined || capability.channels === VOICE_INPUT_CHANNELS) &&
    (capability.encoding === undefined || capability.encoding === "pcm_s16le") &&
    Number.isInteger(frameSamples) &&
    frameSamples * 2 <= capability.max_frame_bytes;

  if (!valid) {
    state.voiceInput = {
      enabled: false,
      sampleRateHz: 0,
      frameDurationMs: 0,
      maxFrameBytes: 0,
    };
    void stopMicrophone({ notifyServer: true, statusText: "Voice input unavailable" });
    setMicrophoneState("error", "Voice input unavailable");
    updateMicrophoneControls();
    return;
  }

  const previous = state.voiceInput;
  const next = {
    enabled: capability.enabled,
    sampleRateHz: capability.sample_rate_hz,
    frameDurationMs: capability.frame_duration_ms,
    maxFrameBytes: capability.max_frame_bytes,
  };
  const changedDuringCapture =
    (state.mic.active || state.mic.starting) &&
    (previous.sampleRateHz !== next.sampleRateHz ||
      previous.frameDurationMs !== next.frameDurationMs ||
      previous.maxFrameBytes !== next.maxFrameBytes);
  state.voiceInput = next;
  if (changedDuringCapture) {
    void stopMicrophone({
      notifyServer: true,
      statusText: "Microphone stopped: server configuration changed",
    });
  }
  if (!capability.enabled) {
    void stopMicrophone({ notifyServer: true, statusText: "Voice input disabled by server" });
  } else if (!state.mic.active && !state.mic.starting) {
    setMicrophoneState("idle", "Microphone idle");
  }
  updateMicrophoneControls();
}

async function startMicrophone() {
  if (state.mic.active || state.mic.starting) return;
  const socket = state.socket;
  const capability = { ...state.voiceInput };
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    setMicrophoneState("error", "Connect before starting the microphone");
    return;
  }
  if (!capability.enabled) {
    setMicrophoneState("error", "Voice input is unavailable");
    return;
  }
  if (!microphoneSupported()) {
    setMicrophoneState("error", "AudioWorklet microphone capture is not supported");
    return;
  }

  const frameSamples = (capability.sampleRateHz * capability.frameDurationMs) / 1000;
  if (!Number.isInteger(frameSamples) || frameSamples <= 0 || frameSamples * 2 > capability.maxFrameBytes) {
    setMicrophoneState("error", "Server supplied an invalid voice frame configuration");
    return;
  }

  const token = state.mic.token + 1;
  state.mic.token = token;
  state.mic.starting = true;
  setMicrophoneState("starting", "Requesting microphone permission…");
  updateMicrophoneControls();

  let mediaStream = null;
  let context = null;
  let source = null;
  let worklet = null;
  let mute = null;
  let startSent = false;
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    context = new AudioContextClass({ latencyHint: "interactive" });
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: { ideal: VOICE_INPUT_CHANNELS },
        sampleRate: { ideal: capability.sampleRateHz },
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    if (!captureAttemptIsCurrent(token, socket)) {
      await releaseMicrophoneResources({ mediaStream, context });
      return;
    }

    await context.audioWorklet.addModule("./mic-worklet.js");
    if (!captureAttemptIsCurrent(token, socket)) {
      await releaseMicrophoneResources({ mediaStream, context });
      return;
    }

    source = context.createMediaStreamSource(mediaStream);
    worklet = new AudioWorkletNode(context, "eeveetuber-microphone", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: {
        targetSampleRate: capability.sampleRateHz,
        frameSamples,
      },
    });
    mute = context.createGain();
    mute.gain.value = 0;
    worklet.connect(mute);
    mute.connect(context.destination);

    const streamId = newStreamId();
    Object.assign(state.mic, {
      streamId,
      sequence: 0n,
      frameSamples,
      frameDurationNs: BigInt(capability.frameDurationMs) * 1_000_000n,
      nextCapturedAtNs: BigInt(Math.round(performance.now() * 1_000_000)),
      mediaStream,
      context,
      source,
      worklet,
      mute,
    });
    worklet.port.onmessage = (event) => {
      if (state.mic.token === token) handleMicrophoneFrame(event.data);
    };
    startSent = send({
      protocol_version: 1,
      type: "voice.capture.start",
      stream_id: streamId,
      sample_rate_hz: capability.sampleRateHz,
      channels: VOICE_INPUT_CHANNELS,
    });
    if (!startSent) throw new Error("WebSocket closed before microphone capture started");

    state.mic.active = true;
    state.mic.starting = false;
    source.connect(worklet);
    await context.resume();
    ui.micTranscript.textContent = "Listening…";
    setMicrophoneState("listening", "Microphone listening");
    addEvent(
      "voice.capture.start",
      `stream=${streamId} rate=${capability.sampleRateHz} frame_ms=${capability.frameDurationMs}`,
    );
    updateMicrophoneControls();
  } catch (error) {
    if (startSent) sendVoiceCaptureStop(state.mic.streamId);
    await releaseMicrophoneResources({ mediaStream, context, source, worklet, mute });
    if (state.mic.token === token) {
      resetMicrophoneState();
      setMicrophoneState("error", microphoneErrorMessage(error));
      addEvent("voice.capture.error", microphoneErrorMessage(error));
      updateMicrophoneControls();
    }
  }
}

async function stopMicrophone({ notifyServer, statusText }) {
  const hadCapture = state.mic.starting || state.mic.active || state.mic.streamId !== null;
  state.mic.token += 1;
  const resources = {
    mediaStream: state.mic.mediaStream,
    context: state.mic.context,
    source: state.mic.source,
    worklet: state.mic.worklet,
    mute: state.mic.mute,
  };
  const streamId = state.mic.streamId;
  if (notifyServer && streamId) sendVoiceCaptureStop(streamId);
  resetMicrophoneState();
  updateMicrophoneControls();
  await releaseMicrophoneResources(resources);
  if (hadCapture) addEvent("voice.capture.stop", statusText);
  setMicrophoneState("idle", statusText);
}

function sendVoiceCaptureStop(streamId) {
  if (!streamId) return false;
  return send({
    protocol_version: 1,
    type: "voice.capture.stop",
    stream_id: streamId,
    reason: "operator_requested",
  });
}

function handleMicrophoneFrame(message) {
  if (!state.mic.active || message?.type !== "pcm.frame" || !(message.samples instanceof ArrayBuffer)) {
    return;
  }
  const samples = new Float32Array(message.samples);
  if (samples.length !== state.mic.frameSamples) {
    setMicrophoneState("error", "Microphone worklet emitted an invalid frame");
    void stopMicrophone({ notifyServer: true, statusText: "Microphone stopped" });
    return;
  }

  const socket = state.socket;
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    void stopMicrophone({ notifyServer: false, statusText: "Microphone idle (disconnected)" });
    return;
  }
  const packet = encodeVoiceInputFrame(
    state.mic.streamId,
    state.mic.sequence,
    state.mic.nextCapturedAtNs,
    state.voiceInput.sampleRateHz,
    samples,
  );
  const bufferedLimit = Math.min(
    MAX_CAPTURE_BUFFERED_AMOUNT,
    Math.max(64 * 1024, state.voiceInput.maxFrameBytes * 8),
  );
  if (socket.bufferedAmount + packet.byteLength > bufferedLimit) {
    addEvent("voice.capture.backpressure", `buffered=${socket.bufferedAmount}`);
    setMicrophoneState("error", "Microphone stopped: connection is too slow");
    void stopMicrophone({
      notifyServer: true,
      statusText: "Microphone stopped: connection is too slow",
    });
    return;
  }
  socket.send(packet);
  state.mic.sequence += 1n;
  state.mic.nextCapturedAtNs += state.mic.frameDurationNs;
}

function encodeVoiceInputFrame(streamId, sequence, capturedAtMonotonicNs, sampleRateHz, samples) {
  const pcmBytes = samples.length * 2;
  const packet = new ArrayBuffer(VOICE_INPUT_FIXED_HEADER_BYTES + pcmBytes);
  const view = new DataView(packet);
  for (let index = 0; index < VOICE_INPUT_MAGIC.length; index += 1) {
    view.setUint8(index, VOICE_INPUT_MAGIC.charCodeAt(index));
  }
  view.setUint8(4, VOICE_INPUT_VERSION);
  view.setUint8(5, 0);
  view.setUint16(6, VOICE_INPUT_FIXED_HEADER_BYTES, false);
  new Uint8Array(packet, 8, 16).set(uuidBytes(streamId));
  view.setBigUint64(24, sequence, false);
  view.setBigUint64(32, capturedAtMonotonicNs, false);
  view.setUint32(40, sampleRateHz, false);
  view.setUint16(44, VOICE_INPUT_CHANNELS, false);
  view.setUint8(46, VOICE_INPUT_ENCODING_PCM_S16LE);
  view.setUint8(47, 0);
  view.setUint32(48, pcmBytes, false);
  for (let index = 0; index < samples.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[index]));
    const pcm = Math.round(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
    view.setInt16(VOICE_INPUT_FIXED_HEADER_BYTES + index * 2, pcm, true);
  }
  return packet;
}

async function releaseMicrophoneResources({ mediaStream, context, source, worklet, mute }) {
  try {
    worklet?.port.postMessage({ type: "stop" });
    worklet?.port.close();
  } catch {}
  try {
    source?.disconnect();
    worklet?.disconnect();
    mute?.disconnect();
  } catch {}
  for (const track of mediaStream?.getTracks() || []) track.stop();
  if (context && context.state !== "closed") {
    try {
      await context.close();
    } catch {}
  }
}

function resetMicrophoneState() {
  Object.assign(state.mic, {
    starting: false,
    active: false,
    streamId: null,
    sequence: 0n,
    frameSamples: 0,
    frameDurationNs: 0n,
    nextCapturedAtNs: 0n,
    mediaStream: null,
    context: null,
    source: null,
    worklet: null,
    mute: null,
  });
}

function captureAttemptIsCurrent(token, socket) {
  return state.mic.token === token && state.socket === socket && socket.readyState === WebSocket.OPEN;
}

function microphoneSupported() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  return Boolean(
    navigator.mediaDevices?.getUserMedia &&
      AudioContextClass &&
      window.AudioWorkletNode,
  );
}

function newStreamId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function uuidBytes(value) {
  const hex = value.replaceAll("-", "");
  if (!/^[0-9a-f]{32}$/i.test(hex)) throw new Error("invalid microphone stream UUID");
  return Uint8Array.from(hex.match(/.{2}/g), (pair) => Number.parseInt(pair, 16));
}

function microphoneErrorMessage(error) {
  if (error?.name === "NotAllowedError") return "Microphone permission was denied";
  if (error?.name === "NotFoundError") return "No microphone was found";
  return error instanceof Error ? error.message : "Microphone could not start";
}

function applyTranscript(message) {
  const text = message.data?.text;
  if (typeof text !== "string") return;
  const final = message.type === "voice.transcript_final";
  ui.micTranscript.textContent = text || (final ? "No speech recognized." : "Listening…");
  if (final && text.trim()) appendHistory("owner", text.trim());
  if (state.mic.active) setMicrophoneState("listening", "Microphone listening");
}

function handleMessage(raw) {
  if (raw instanceof ArrayBuffer) {
    try {
      const frame = decodeAudioFrame(raw);
      addEvent("speech.audio_binary", summarizeAudio(frame));
      acceptAudioFrame(frame);
    } catch (error) {
      addEvent("audio.invalid", String(error));
      setStatus("error", "Invalid audio frame");
    }
    return;
  }
  if (typeof raw !== "string") {
    addEvent("protocol.unsupported", Object.prototype.toString.call(raw));
    return;
  }
  let message;
  try {
    message = JSON.parse(raw);
  } catch (error) {
    addEvent("json.invalid", String(error));
    return;
  }
  addEvent(message.type || "event", JSON.stringify(message.data || message));
  if (message.session_id) state.sessionId = message.session_id;
  applyAudioControl(message);

  if (message.type === "session.ready") {
    applyVoiceInputCapabilities(message.data);
    setStatus("connected", "Ready");
  }
  else if (message.type === "session.status") setStatus(statusClass(message.status), message.status);
  else if (message.type === "turn.started") setStatus("processing", "Processing");
  else if (message.type === "utterance.segment_ready") {
    setStatus("connected", "Speaking");
    appendHistory("character", message.data?.display_text || message.data?.speakable_text || "");
  } else if (message.type === "utterance.completed") setStatus("connected", "Ready");
  else if (message.type === "turn.failed") {
    const detail = message.data?.detail || "Turn failed";
    setStatus("error", detail);
    appendHistory("system", detail);
  }

  if (message.type === "voice.transcript_partial" || message.type === "voice.transcript_final") {
    applyTranscript(message);
  } else if (message.type === "voice.transcript_empty") {
    ui.micTranscript.textContent = "No speech recognized.";
  } else if (message.type === "voice.recognition_failed") {
    setMicrophoneState("error", "Speech recognition failed");
  }

  if (message.type === "speech.audio_chunk" && message.data?.audio_base64) {
    acceptAudioFrame(audioFromJson(message));
  }
}

function applyAudioControl(message) {
  const generation = message.generation;
  const hasGeneration = Number.isInteger(generation);
  const newerGeneration =
    hasGeneration && (state.audioGeneration === null || generation > state.audioGeneration);
  const currentCancellation =
    message.type === "speech.cancelled" &&
    (!hasGeneration || state.audioGeneration === null || generation >= state.audioGeneration);
  if (currentCancellation || (state.audioGeneration !== null && newerGeneration)) {
    stopActiveAudio("cancelled");
  }
  if (newerGeneration) state.audioGeneration = generation;
}

function decodeAudioFrame(buffer) {
  if (buffer.byteLength < AUDIO_FIXED_HEADER_BYTES) throw new Error("audio frame is truncated");
  const view = new DataView(buffer);
  const magic = String.fromCharCode(...new Uint8Array(buffer, 0, 4));
  if (magic !== AUDIO_MAGIC) throw new Error("audio frame magic mismatch");
  if (view.getUint8(4) !== AUDIO_VERSION) throw new Error("unsupported audio frame version");
  const flags = view.getUint8(5);
  const headerBytes = view.getUint16(6, false);
  const mediaBytes = view.getUint16(114, false);
  const payloadBytes = view.getUint32(120, false);
  if (headerBytes !== AUDIO_FIXED_HEADER_BYTES + mediaBytes) throw new Error("invalid header size");
  if (buffer.byteLength !== headerBytes + payloadBytes) throw new Error("invalid payload size");
  const duration = view.getUint32(116, false);
  return {
    sessionId: uuidAt(buffer, 8),
    eventId: uuidAt(buffer, 24),
    correlationId: uuidAt(buffer, 40),
    turnId: uuidAt(buffer, 56),
    segmentId: uuidAt(buffer, 72),
    generation: view.getUint32(88, false),
    eventSequence: view.getBigUint64(92, false),
    segmentSequence: view.getUint32(100, false),
    chunkIndex: view.getUint32(104, false),
    sampleRateHz: view.getUint32(108, false),
    channels: view.getUint16(112, false),
    mediaType: new TextDecoder("ascii", { fatal: true }).decode(
      new Uint8Array(buffer, AUDIO_FIXED_HEADER_BYTES, mediaBytes),
    ),
    durationMs: duration === 0xffffffff ? null : duration,
    flags,
    audio: buffer.slice(headerBytes),
  };
}

function audioFromJson(message) {
  const binary = atob(message.data.audio_base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return {
    sessionId: message.session_id,
    eventId: message.message_id,
    correlationId: message.correlation_id,
    turnId: message.data.turn_id,
    segmentId: message.data.segment_id,
    generation: message.generation,
    eventSequence: BigInt(message.sequence),
    segmentSequence: message.data.sequence,
    chunkIndex: message.data.chunk_index,
    sampleRateHz: message.data.sample_rate_hz,
    channels: 1,
    mediaType: message.data.media_type,
    durationMs: message.data.duration_ms,
    flags: message.data.is_final ? FLAG_FINAL_CHUNK : 0,
    audio: bytes.buffer,
  };
}

function acceptAudioFrame(frame) {
  if (state.audioGeneration !== null && frame.generation < state.audioGeneration) {
    acknowledge(frame, "cancelled", "stale audio generation");
    return;
  }
  if (state.audioGeneration === null || frame.generation > state.audioGeneration) {
    stopActiveAudio("cancelled");
    state.audioGeneration = frame.generation;
  }
  acknowledge(frame, "queued");
  const key = `${frame.generation}:${frame.segmentId}`;
  const pending = state.pendingSegments.get(key) || {
    frames: [],
    chunks: [],
    nextChunkIndex: 0,
    mediaType: frame.mediaType,
  };
  if (frame.chunkIndex !== pending.nextChunkIndex || frame.mediaType !== pending.mediaType) {
    for (const accepted of pending.frames) {
      acknowledge(accepted, "failed", "audio segment framing was discontinuous");
    }
    acknowledge(frame, "failed", "audio segment framing was discontinuous");
    state.pendingSegments.delete(key);
    return;
  }
  pending.frames.push(frame);
  pending.chunks.push(new Uint8Array(frame.audio));
  pending.nextChunkIndex += 1;
  state.pendingSegments.set(key, pending);
  if (!(frame.flags & FLAG_FINAL_CHUNK)) return;

  state.pendingSegments.delete(key);
  const totalBytes = pending.chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const combined = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of pending.chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  enqueueAudio({ ...frame, audio: combined.buffer, ackFrames: pending.frames });
}

function enqueueAudio(frame) {
  const epoch = state.audioEpoch;
  state.audioQueue = state.audioQueue
    .then(() => {
      if (epoch !== state.audioEpoch) {
        acknowledgeAll(frame, "cancelled", "audio queue was superseded");
        return undefined;
      }
      return playAudio(frame);
    })
    .catch((error) => addEvent("audio.queue_error", String(error)));
}

async function playAudio(frame) {
  if (!state.audioEnabled) {
    acknowledgeAll(frame, "cancelled", "operator muted audio");
    return;
  }
  if (!frame.mediaType.startsWith("audio/")) {
    acknowledgeAll(frame, "unsupported", `unsupported media type: ${frame.mediaType}`);
    return;
  }
  const url = URL.createObjectURL(new Blob([frame.audio], { type: frame.mediaType }));
  const audio = new Audio(url);
  state.activeAudio = audio;
  try {
    const terminalState = await new Promise((resolve, reject) => {
      state.finishActiveAudio = (reason = "cancelled") => resolve(reason);
      audio.addEventListener("ended", () => resolve("completed"), { once: true });
      audio.addEventListener("error", () => reject(new Error("browser could not decode audio")), { once: true });
      audio.play().then(() => acknowledgeAll(frame, "started"), reject);
    });
    acknowledgeAll(
      frame,
      terminalState,
      terminalState === "completed" ? null : "playback stopped by operator",
      Math.round(audio.currentTime * 1000),
    );
  } catch (error) {
    acknowledgeAll(frame, "failed", String(error), Math.round(audio.currentTime * 1000));
  } finally {
    state.activeAudio = null;
    state.finishActiveAudio = null;
    URL.revokeObjectURL(url);
  }
}

function stopActiveAudio(ackState) {
  state.audioEpoch += 1;
  for (const pending of state.pendingSegments.values()) {
    for (const frame of pending.frames) acknowledge(frame, ackState, "audio was superseded");
  }
  state.pendingSegments.clear();
  if (!state.activeAudio) return;
  state.activeAudio.pause();
  state.finishActiveAudio?.(ackState);
  addEvent("audio.stopped", ackState);
}

function acknowledgeAll(frame, playbackState, detail = null, playedMs = null) {
  for (const acknowledged of frame.ackFrames || [frame]) {
    acknowledge(acknowledged, playbackState, detail, playedMs);
  }
}

function acknowledge(frame, playbackState, detail = null, playedMs = null) {
  const eventSequence = frame.eventSequence.toString();
  send({
    protocol_version: 1,
    type: "playback.ack",
    session_id: frame.sessionId || state.sessionId,
    audio_event_id: frame.eventId,
    generation: frame.generation,
    event_sequence: eventSequence,
    segment_id: frame.segmentId,
    chunk_index: frame.chunkIndex,
    state: playbackState,
    client_monotonic_ms: Math.round(performance.now()),
    played_ms: playedMs,
    detail,
  });
}

function uuidAt(buffer, offset) {
  const hex = [...new Uint8Array(buffer, offset, 16)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function appendHistory(role, text) {
  if (!text) return;
  const item = document.createElement("li");
  item.className = role;
  const label = document.createElement("span");
  label.className = "role";
  label.textContent = role === "owner" ? "You" : role === "system" ? "Runtime" : "Eeveetuber";
  item.append(label, document.createTextNode(text));
  ui.history.append(item);
  trimList(ui.history, 200);
  item.scrollIntoView({ block: "nearest" });
}

function addEvent(type, detail) {
  const item = document.createElement("li");
  item.textContent = `${new Date().toLocaleTimeString()}  ${type}  ${detail}`;
  ui.events.append(item);
  trimList(ui.events, MAX_VISIBLE_EVENTS);
  item.scrollIntoView({ block: "nearest" });
}

function trimList(list, maximum) {
  while (list.children.length > maximum) list.firstElementChild?.remove();
}

function summarizeAudio(frame) {
  return `${frame.mediaType} segment=${frame.segmentSequence} chunk=${frame.chunkIndex} bytes=${frame.audio.byteLength}`;
}

function setStatus(kind, text) {
  ui.status.className = `status ${kind}`;
  ui.status.textContent = text;
}

function setMicrophoneState(kind, text) {
  ui.micState.className = `mic-state ${kind}`;
  ui.micState.textContent = text;
}

function updateMicrophoneControls() {
  const connected = Boolean(state.socket && state.socket.readyState === WebSocket.OPEN);
  const supported = microphoneSupported();
  ui.micStart.disabled =
    !connected || !supported || !state.voiceInput.enabled || state.mic.starting || state.mic.active;
  ui.micStop.disabled = !state.mic.starting && !state.mic.active;
}

function statusClass(status) {
  if (status === "error" || status === "degraded") return "error";
  if (status === "processing" || status === "waiting_approval") return "processing";
  return "connected";
}

function setConnectedControls(connected) {
  ui.connect.textContent = connected ? "Disconnect" : "Connect";
  ui.text.disabled = !connected;
  for (const control of ui.controls) control.disabled = !connected;
  updateMicrophoneControls();
}
