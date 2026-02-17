"""Slim response models for Plane MCP tools."""

from html.parser import HTMLParser

from pydantic import BaseModel, ConfigDict


class _SlimBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    def slim(self) -> dict:
        """Serialize to dict, dropping None values."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._parts.append(stripped)

    def get_text(self) -> str:
        return " ".join(self._parts)


def strip_html(html: str | None) -> str | None:
    """Strip HTML tags and return plain text, or None if empty."""
    if not html:
        return None
    stripper = _HTMLStripper()
    stripper.feed(html)
    text = stripper.get_text().strip()
    return text or None


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class ProjectSummary(_SlimBase):
    """id, name, identifier only — everything else is noise for an AI agent."""

    id: str | None = None
    name: str
    identifier: str


# ---------------------------------------------------------------------------
# Work items
# ---------------------------------------------------------------------------


class WorkItemSummary(_SlimBase):
    """List-level work item — no description, no null fields."""

    id: str | None = None
    sequence_id: int | None = None
    name: str
    priority: str | None = None
    state: str | None = None
    assignees: list[str] = []
    labels: list[str] = []


class AssigneeSummary(_SlimBase):
    id: str | None = None
    display_name: str | None = None


class LabelSummary(_SlimBase):
    id: str | None = None
    name: str
    color: str | None = None


class WorkItemFull(_SlimBase):
    """Detail view — plain-text description, expanded assignees/labels, dates if set."""

    id: str | None = None
    sequence_id: int | None = None
    name: str
    description: str | None = None
    priority: str | None = None
    state: str | None = None
    assignees: list[AssigneeSummary] = []
    labels: list[LabelSummary] = []
    parent: str | None = None
    start_date: str | None = None
    target_date: str | None = None


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


class StateSummary(_SlimBase):
    """id, name, group, default — enough to pick and reference a state."""

    id: str | None = None
    name: str
    group: str | None = None
    default: bool | None = None
