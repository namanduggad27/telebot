import pytest
from src.db.models import PipelineStatus
from src.services.state_machine import StateMachine


def test_can_transition_valid():
    """Verify allowed state transitions."""
    assert StateMachine.can_transition(PipelineStatus.SCRAPED, PipelineStatus.ENRICHED) is True
    assert StateMachine.can_transition(PipelineStatus.ENRICHED, PipelineStatus.CONFIRMED) is True
    assert StateMachine.can_transition(PipelineStatus.CONFIRMED, PipelineStatus.DOWNLOADING) is True
    assert StateMachine.can_transition(PipelineStatus.DOWNLOADING, PipelineStatus.SHADOW_UPLOADED) is True
    assert StateMachine.can_transition(PipelineStatus.SHADOW_UPLOADED, PipelineStatus.BATCH_LINKED) is True
    assert StateMachine.can_transition(PipelineStatus.BATCH_LINKED, PipelineStatus.FINAL_POSTED) is True


def test_can_transition_invalid():
    """Verify prohibited state transitions (e.g. jumping straight from SCRAPED to FINAL_POSTED or REJECTED to FINAL_POSTED)."""
    assert StateMachine.can_transition(PipelineStatus.SCRAPED, PipelineStatus.FINAL_POSTED) is False
    assert StateMachine.can_transition(PipelineStatus.REJECTED, PipelineStatus.FINAL_POSTED) is False
    assert StateMachine.can_transition(PipelineStatus.FINAL_POSTED, PipelineStatus.SCRAPED) is False
    assert StateMachine.can_transition(PipelineStatus.ENRICHED, PipelineStatus.SHADOW_UPLOADED) is False


def test_can_transition_same_state():
    """Verify self-transition is permitted (idempotency)."""
    assert StateMachine.can_transition(PipelineStatus.SCRAPED, PipelineStatus.SCRAPED) is True
    assert StateMachine.can_transition(PipelineStatus.ENRICHED, PipelineStatus.ENRICHED) is True
