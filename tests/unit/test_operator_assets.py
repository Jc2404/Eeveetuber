from __future__ import annotations

from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[2] / "apps" / "operator"


def test_operator_client_is_dependency_free_and_exposes_required_controls() -> None:
    html = (OPERATOR / "index.html").read_text(encoding="utf-8")
    script = (OPERATOR / "app.js").read_text(encoding="utf-8")

    assert "https://" not in html
    assert 'type="module" src="./app.js"' in html
    for action in ("stop_speech", "neutral_avatar", "kill_session"):
        assert f'data-action="{action}"' in html
    assert 'type: "turn.text"' in script
    assert 'type: "playback.ack"' in script
    assert "decodeAudioFrame" in script
    assert "speech.audio_chunk" in script


def test_operator_mount_instructions_and_assets_exist() -> None:
    for name in ("index.html", "app.js", "styles.css", "README.md"):
        assert (OPERATOR / name).is_file()
    readme = (OPERATOR / "README.md").read_text(encoding="utf-8")
    server = (OPERATOR.parents[1] / "src" / "eeveetuber" / "api" / "app.py").read_text(
        encoding="utf-8"
    )
    assert '"/operator"' in server and "StaticFiles" in server
    assert "encode_audio_frame" in readme
