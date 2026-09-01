import re
from enum import Enum


class QueryIntent(str, Enum):
    LOOKUP = "lookup"
    ATTRIBUTE = "attribute"
    DEPENDENCY = "dependency"
    BLAST_RADIUS = "blast_radius"
    AMBIGUOUS_RELATIONSHIP = "ambiguous_relationship"


_BLAST_RADIUS_PATTERNS = (
    re.compile(r"\baffected\b", re.IGNORECASE),
    re.compile(r"\bblast[- ]radius\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+breaks\b", re.IGNORECASE),
    re.compile(
        r"\bif\b.*\b(?:removed|deleted|destroyed)\b",
        re.IGNORECASE,
    ),
)
_DEPENDENCY_PATTERNS = (
    re.compile(r"\bdepend(?:s|ed|ing)?\s+on\b", re.IGNORECASE),
    re.compile(r"\brel(?:y|ies|ied|ying)\s+on\b", re.IGNORECASE),
    re.compile(r"\brequires?\b.*\bto\b", re.IGNORECASE),
)
_AMBIGUOUS_RELATIONSHIP_PATTERNS = (
    re.compile(r"\brelated\s+to\b", re.IGNORECASE),
    re.compile(r"\bconnected\s+to\b", re.IGNORECASE),
    re.compile(r"\bassociated\s+with\b", re.IGNORECASE),
    re.compile(r"\blinked\s+to\b", re.IGNORECASE),
    re.compile(r"\bhow\s+does\b.*\brelate\b", re.IGNORECASE),
)
_ATTRIBUTE_PATTERNS = (
    re.compile(r"=\s*(?:true|false)\b", re.IGNORECASE),
    re.compile(r"\bexact\s+reference\b", re.IGNORECASE),
    re.compile(
        r"\bexplicit(?:ly)?\b.*\b(?:setting|enables?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcontains?\b.*\b(?:setting|reference)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdefines?\s+services?\s+for\b", re.IGNORECASE),
)


def _matches_any(question: str, patterns: tuple[re.Pattern, ...]) -> bool:
    return any(pattern.search(question) for pattern in patterns)


def classify_intent(question: str) -> QueryIntent:
    """Classify graph traversal intent using only the question text."""
    if _matches_any(question, _BLAST_RADIUS_PATTERNS):
        return QueryIntent.BLAST_RADIUS
    if _matches_any(question, _DEPENDENCY_PATTERNS):
        return QueryIntent.DEPENDENCY
    if _matches_any(question, _AMBIGUOUS_RELATIONSHIP_PATTERNS):
        return QueryIntent.AMBIGUOUS_RELATIONSHIP
    if _matches_any(question, _ATTRIBUTE_PATTERNS):
        return QueryIntent.ATTRIBUTE
    return QueryIntent.LOOKUP


def directions_for_intent(intent: QueryIntent) -> tuple[str, ...]:
    """Return the graph directions appropriate for one query intent."""
    return {
        QueryIntent.LOOKUP: (),
        QueryIntent.ATTRIBUTE: (),
        QueryIntent.DEPENDENCY: ("dependency",),
        QueryIntent.BLAST_RADIUS: ("dependent",),
        QueryIntent.AMBIGUOUS_RELATIONSHIP: (
            "dependent",
            "dependency",
        ),
    }[intent]
