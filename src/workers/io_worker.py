import logging
from typing import Any, Dict
from src.services.file_io_engine import FileIOEngine

logger = logging.getLogger("workers.io_worker")


async def process_media_io_task(ctx: Dict[str, Any], item_id: str) -> bool:
    """ARQ background task that executes Phase 3: high-speed MTProto streaming download, renaming, and shadow upload."""
    logger.info(f"Starting Phase 3 I/O processing task for item ID={item_id}")
    try:
        success = await FileIOEngine.process_item_io(item_id)
        if success:
            logger.info(f"Phase 3 I/O task completed successfully for ID={item_id}")
        else:
            logger.error(f"Phase 3 I/O task failed for ID={item_id}")
        return success
    except Exception as e:
        logger.error(f"Fatal exception during process_media_io_task for ID={item_id}: {e}", exc_info=True)
        return False

async def process_batch_io_task(ctx: Dict[str, Any], item_ids: list[int]) -> bool:
    """ARQ background task that executes batch processing for multiple selected files."""
    logger.info(f"Starting Batch I/O processing task for IDs={item_ids}")
    try:
        success = await FileIOEngine.process_batch_io(item_ids)
        if success:
            logger.info(f"Batch I/O task completed successfully for IDs={item_ids}")
        else:
            logger.error(f"Batch I/O task failed for IDs={item_ids}")
        return success
    except Exception as e:
        logger.error(f"Fatal exception during process_batch_io_task for IDs={item_ids}: {e}", exc_info=True)
        return False
