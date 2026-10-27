"""Document envelope for the extraction graph.

A Document carries the prepared lines (the canonical 1-based reference frame
for a run) plus the behavior that varies by document kind:

- ``generate_assignments``: how to partition the document into worker tasks.
- ``optional_tools``: extra tools this document kind offers the worker.
- ``augment_search_hit``: extra context to attach to a search hit at a line.
- ``augment_task_prompt``: extra context to add to a worker's task message.

``TextDocument`` is the base: plain text (e.g. from a PDF) with character-based
assignments and no extra structure. ``XmlDocument`` (a later addition) extends
it with an outline, structural navigation, and section-aware assignments.

The document is read-only shared state: workers must not mutate it.
"""

from __future__ import annotations

from provision_extraction.assignments import make_assignments
from provision_graph.models import Assignment


class TextDocument:
    """A plain-text document: prepared lines plus default behaviors.

    Subclasses override the hook methods to add document-kind structure. The
    default implementations are the plain-text baseline: character-based
    assignments, no optional tools, and no hit/prompt augmentation.
    """

    def __init__(self, lines: list[str]):
        self.lines = lines

    def generate_assignments(self, target_chars: int) -> list[Assignment]:
        """Contiguous, nonoverlapping character-based assignments."""
        return make_assignments(self.lines, target_chars)

    def optional_tools(self) -> list:
        """Extra (LangChain) tools this document kind offers. None by default."""
        return []

    def augment_search_hit(self, line: int) -> str:
        """Extra context line for a search hit at ``line``. Empty by default."""
        return ""

    def augment_task_prompt(self, assignment: Assignment) -> str:
        """Extra task-message context for an assignment. Empty by default."""
        return ""
