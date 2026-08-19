from __future__ import annotations

import pytest

from eeveetuber.domain.interaction import (
    InteractionState,
    InteractionStateMachine,
    InvalidInteractionTransition,
)


def test_every_declared_transition_matches_can_transition() -> None:
    for initial in InteractionState:
        allowed = InteractionStateMachine.allowed_from(initial)
        for requested in InteractionState:
            machine = InteractionStateMachine(initial)
            if requested in allowed:
                result = machine.transition(requested, reason="test transition")
                assert machine.can_transition(requested) is (
                    requested in InteractionStateMachine.allowed_from(machine.state)
                )
                assert result.previous is initial
                assert result.current is requested
                assert result.revision == 1
            else:
                assert not machine.can_transition(requested)
                with pytest.raises(InvalidInteractionTransition) as error:
                    machine.transition(requested, reason="invalid test")
                assert error.value.previous is initial
                assert error.value.requested is requested
                assert machine.state is initial
                assert machine.revision == 0


def test_barge_in_uses_explicit_interrupting_state() -> None:
    machine = InteractionStateMachine(InteractionState.SPEAKING)

    with pytest.raises(InvalidInteractionTransition):
        machine.transition(InteractionState.LISTENING, reason="illegal shortcut")

    first = machine.transition(InteractionState.INTERRUPTING, reason="barge-in confirmed")
    second = machine.transition(InteractionState.LISTENING, reason="speech stopped")
    assert (first.revision, second.revision) == (1, 2)
    assert machine.state is InteractionState.LISTENING


def test_transition_reason_is_required_and_self_transition_is_not_silent() -> None:
    machine = InteractionStateMachine()
    with pytest.raises(ValueError, match="reason"):
        machine.transition(InteractionState.LISTENING, reason=" ")
    with pytest.raises(InvalidInteractionTransition):
        machine.transition(InteractionState.IDLE, reason="no-op")

