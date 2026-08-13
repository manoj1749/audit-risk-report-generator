"""Cross-reference graph between notes, built with networkx."""
import re

import networkx as nx
from loguru import logger

from models.financial import NoteSection

XREF_PATTERNS = [
    re.compile(r"[Rr]efer\s+[Nn]ote\s+[Nn]o\.?\s*(\d+(?:\s*[\(\[]\s*[a-zA-Z]\s*[\)\]])?)", re.IGNORECASE),
    re.compile(r"\(see\s+[Nn]ote\s+(\d+)\)", re.IGNORECASE),
    re.compile(r"as\s+per\s+[Nn]ote\s+(\d+)", re.IGNORECASE),
    re.compile(r"\(refer\s+[Nn]ote\s+(\d+)\)", re.IGNORECASE),
    re.compile(r"[Nn]ote\s+[Nn]o\.?\s*(\d+)\s+for\s+further", re.IGNORECASE),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _normalize_ref(ref: str) -> str:
    """Turn '6 (a)' / '6(a)' into '6a'."""
    return re.sub(r"[\s\(\)\[\]]", "", ref).lower()


def _sentence_containing(text: str, idx: int) -> str:
    sentences = _SENTENCE_SPLIT.split(text)
    cursor = 0
    for sentence in sentences:
        end = cursor + len(sentence)
        if cursor <= idx <= end:
            return sentence.strip()
        cursor = end + 1
    return text[max(0, idx - 80): idx + 80].strip()


class NoteXRefGraph:
    def __init__(self, notes: dict[str, NoteSection]):
        self.notes = notes
        self.graph = nx.DiGraph()
        for note_id in notes:
            self.graph.add_node(note_id)

    def get_resolved_note(self, note_id: str, max_depth: int = 2) -> str:
        """Return text of note_id plus all notes it references, up to max_depth hops."""
        note_id = _normalize_ref(note_id)
        if note_id not in self.notes:
            return ""

        visited: set[str] = set()
        sections: list[str] = []

        def visit(nid: str, depth: int, via: str | None):
            if nid in visited or nid not in self.notes:
                return
            visited.add(nid)
            note = self.notes[nid]
            header = f"=== {note.full_id}" + (f" (referenced by Note {via})" if via else "") + " ==="
            sections.append(f"{header}\n{note.raw_text}")
            if depth >= max_depth:
                return
            for target in self.graph.successors(nid):
                visit(target, depth + 1, nid)

        visit(note_id, 0, None)
        return "\n\n".join(sections)


def build_xref_graph(notes: dict[str, NoteSection]) -> NoteXRefGraph:
    xref = NoteXRefGraph(notes)

    for citing_id, note in notes.items():
        for pattern in XREF_PATTERNS:
            for match in pattern.finditer(note.raw_text):
                cited_raw = match.group(1)
                cited_id = _normalize_ref(cited_raw)
                if cited_id == citing_id or cited_id not in notes:
                    continue
                context = _sentence_containing(note.raw_text, match.start())
                xref.graph.add_edge(citing_id, cited_id, context=context)
                note.references.append(cited_id)
                notes[cited_id].referenced_by.append(citing_id)

    logger.info(
        f"Built cross-reference graph: {xref.graph.number_of_nodes()} notes, "
        f"{xref.graph.number_of_edges()} references"
    )
    return xref
