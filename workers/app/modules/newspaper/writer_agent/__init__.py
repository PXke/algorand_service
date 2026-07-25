"""Tool-using writer agent (phase 2+).

Replaces one-shot mistral_compose for create/edit flows.
See docs/modules/editorial-platform-vision.md
"""

from app.modules.newspaper.writer_agent.agent import WriterAgentPlan

__all__ = ["WriterAgentPlan"]
