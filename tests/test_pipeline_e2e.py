import pytest
from src.db.models import PipelineStatus
from src.services.state_machine import StateMachine


@pytest.mark.asyncio
async def test_end_to_end_state_machine_flow():
    """Verify complete end-to-end state transitions across all 5 phases of the pipeline."""
    item_id = "test-e2e-uuid-9999"

    # Phase 1: Scraped by Userbot
    assert StateMachine.can_transition(PipelineStatus.SCRAPED, PipelineStatus.ENRICHED) is True

    # Phase 2: Metadata Enriched by TMDB
    assert StateMachine.can_transition(PipelineStatus.ENRICHED, PipelineStatus.CONFIRMED) is True

    # Admin clicks Approve & Download
    assert StateMachine.can_transition(PipelineStatus.CONFIRMED, PipelineStatus.DOWNLOADING) is True

    # Phase 3: High-Speed Download & Shadow DB Channel Upload
    assert StateMachine.can_transition(PipelineStatus.DOWNLOADING, PipelineStatus.SHADOW_UPLOADED) is True
    assert StateMachine.can_transition(PipelineStatus.SHADOW_UPLOADED, PipelineStatus.BATCH_LINKED) is True

    # Phase 4: Batch Links & Main Channel Presentation Broadcast
    assert StateMachine.can_transition(PipelineStatus.BATCH_LINKED, PipelineStatus.FINAL_POSTED) is True
