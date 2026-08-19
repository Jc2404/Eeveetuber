from uuid import UUID

import pytest
from pydantic import ValidationError

from eeveetuber.api.protocol import PingMessage, TextTurnMessage, parse_client_message


def test_parses_discriminated_text_turn() -> None:
    parsed = parse_client_message('{"protocol_version":1,"type":"turn.text","text":"hello"}')

    assert isinstance(parsed, TextTurnMessage)
    assert parsed.text == "hello"
    assert isinstance(parsed.message_id, UUID)


def test_rejects_unknown_fields_and_message_type() -> None:
    with pytest.raises(ValidationError):
        parse_client_message('{"protocol_version":1,"type":"ping","unexpected":true}')

    with pytest.raises(ValidationError):
        parse_client_message('{"protocol_version":1,"type":"unknown"}')


def test_ping_is_versioned() -> None:
    parsed = parse_client_message('{"protocol_version":1,"type":"ping"}')

    assert isinstance(parsed, PingMessage)
    assert parsed.protocol_version == 1

