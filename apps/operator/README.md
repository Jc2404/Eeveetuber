# Eeveetuber operator client

This is a dependency-free browser client for the version-one WebSocket protocol. It supports
text turns, event inspection, current JSON/base64 audio, the `EVAF` binary audio frame, playback
acknowledgements, and stop/neutral/kill controls.

The FastAPI composition root mounts this client at `/operator/` and redirects `/` there. The
assets are also included in the built wheel.

Binary WebSocket messages must contain exactly one frame produced by
`eeveetuber.api.audio_frames.encode_audio_frame`. The client negotiates
`eeveetuber.v1.binary-audio`; clients that do not negotiate it retain JSON/base64 audio. Text
server messages remain JSON. Playback acknowledgements are routed through the owning session actor
and generation-checked.

Serve these files through the application rather than opening `index.html` from disk, because the
Content Security Policy and WebSocket endpoint assume an HTTP(S) origin.
