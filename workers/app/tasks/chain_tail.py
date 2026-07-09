from app.modules.chain_tail.tasks.watch_blocks import poll_new_blocks
from app.modules.chain_tail.tasks.xgov_tasks import poll_xgov_proposals_task

__all__ = ["poll_new_blocks", "poll_xgov_proposals_task"]
