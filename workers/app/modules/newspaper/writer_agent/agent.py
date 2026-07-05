from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WriterToolSpec:
    name: str
    description: str


@dataclass(frozen=True)
class WriterAgentPlan:
    """
    Planned agent loop — not executed yet; documents tool surface for Guillaume's spec.
    """

    model: str = "mistral-medium-latest"
    thinking: bool = True
    tools: tuple[WriterToolSpec, ...] = (
        WriterToolSpec("search_platform", "Search articles, snapshots, match keys"),
        WriterToolSpec("fetch_url_safe", "HTTP fetch allowlisted public pages"),
        # Live in app.modules.ai.writer_tools: loads the full markdown body of a
        # prior article by id (ids come from search_platform/recent_articles).
        WriterToolSpec("get_article", "Load full text of a published article by id"),
        WriterToolSpec("draft_article", "Create new markdown article JSON"),
        WriterToolSpec("edit_article", "Patch article; append ## Updated section"),
        WriterToolSpec("get_editorial_brief", "Load admin suggestion-box brief"),
        WriterToolSpec("get_enrichment_bundle", "Writer enrichment + scam context"),
    )
    max_tool_rounds: int = 14  # matches MISTRAL_MAX_TOOL_ROUNDS default

    def as_markdown(self) -> str:
        lines = [
            "### Writer agent (planned)",
            f"- Model: `{self.model}`",
            f"- Thinking: {self.thinking}",
        ]
        lines.append("- Tools:")
        for tool in self.tools:
            lines.append(f"  - `{tool.name}`: {tool.description}")
        return "\n".join(lines)
