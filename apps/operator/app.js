const AUDIO_MAGIC = "EVAF";
const AUDIO_VERSION = 1;
const BINARY_AUDIO_SUBPROTOCOL = "eeveetuber.v1.binary-audio";
const AUDIO_FIXED_HEADER_BYTES = 124;
const FLAG_FINAL_CHUNK = 1;
const MAX_VISIBLE_EVENTS = 500;

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
};

ui.endpoint.value = defaultEndpoint();
setConnectedControls(false);

ui.connect.addEventListener("click", () => {
  if (state.socket) disconnect();
  else connect();
});

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
    send({ protocol_version: 1, type: "operator.control", action });
  });
}

function defaultEndpoint() {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/v1/ws`;
}

function connect() {
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
    addEvent("socket.open", ui.endpoint.value.trim());
  });
  socket.addEventListener("message", (event) => handleMessage(event.data));
  socket.addEventListener("error", () => setStatus("error", "WebSocket error"));
  socket.addEventListener("close", (event) => {
    addEvent("socket.close", `${event.code} ${event.reason || ""}`.trim());
    state.socket = null;
    state.sessionId = null;
    stopActiveAudio("cancelled");
    state.audioGeneration = null;
    setConnectedControls(false);
    setStatus("disconnected", "Disconnected");
  });
}

function disconnect() {
  state.socket?.close(1000, "operator disconnected");
}

function send(message) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return false;
  state.socket.send(JSON.stringify(message));
  return true;
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

  if (message.type === "session.ready") setStatus("connected", "Ready");
  else if (message.type === "session.status") setStatus(statusClass(message.status), message.status);
  else if (message.type === "turn.started") setStatus("processing", "Processing");
  else if (message.type === "utterance.segment_ready") {
    setStatus("connected", "Speaking");
    appendHistory("character", message.data?.display_text || message.data?.speakable_text || "");
  } else if (message.type === "utterance.completed") setStatus("connected", "Ready");
  else if (message.type === "turn.failed") setStatus("error", message.data?.detail || "Turn failed");

  if (message.type === "speech.audio_chunk" && message.data?.audio_base64) {
    acceptAudioFrame(audioFromJson(message));
  }
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
  label.textContent = role === "owner" ? "You" : "Eeveetuber";
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

function statusClass(status) {
  if (status === "error" || status === "degraded") return "error";
  if (status === "processing" || status === "waiting_approval") return "processing";
  return "connected";
}

function setConnectedControls(connected) {
  ui.connect.textContent = connected ? "Disconnect" : "Connect";
  ui.text.disabled = !connected;
  for (const control of ui.controls) control.disabled = !connected;
}
