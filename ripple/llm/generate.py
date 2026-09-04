import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI

from ripple.llm.prompts import SYSTEM_PROMPT, format_context
from ripple.retrieval.vector_store import RetrievedBlock


load_dotenv()


GENERATION_MODEL = "gpt-4o-mini"

EvidenceType = Literal["direct", "inference"]
Confidence = Literal["high", "medium", "low"]

_UNVALIDATED_ANSWER_TEXT = (
    "The model's response could not be validated, so no answer is provided."
)

_ANSWER_FIELDS = {
    "has_sufficient_evidence",
    "root_cause",
    "answer",
    "confidence",
    "insufficient_evidence_reason",
    "evidence",
}

_EVIDENCE_FIELDS = {
    "statement",
    "evidence_type",
    "file_path",
    "start_line",
    "end_line",
}


@dataclass
class Citation:
    file_path: str
    start_line: int
    end_line: int

    def __str__(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"


@dataclass
class EvidenceItem:
    statement: str
    evidence_type: EvidenceType
    citation: Citation


@dataclass
class StructuredAnswer:
    """A structurally validated answer with physically verified citations.

    Citation validation proves that each evidence item names a retrieved
    block whose indexed range still matches the real file. It does not prove
    that answer, root_cause, or evidence statement prose is semantically
    supported by that block.
    """

    has_sufficient_evidence: bool
    root_cause: str
    answer: str
    evidence: list[EvidenceItem]
    confidence: Confidence
    insufficient_evidence_reason: str | None


@dataclass
class _BlockValidation:
    physically_valid: bool
    reason: str


_ANSWER_JSON_SCHEMA = {
    "type": "json_schema",
    "name": "structured_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "has_sufficient_evidence": {"type": "boolean"},
            "root_cause": {"type": "string"},
            "answer": {"type": "string"},
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "insufficient_evidence_reason": {"type": ["string", "null"]},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "statement": {"type": "string"},
                        "evidence_type": {
                            "type": "string",
                            "enum": ["direct", "inference"],
                        },
                        "file_path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": [
                        "statement",
                        "evidence_type",
                        "file_path",
                        "start_line",
                        "end_line",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "has_sufficient_evidence",
            "root_cause",
            "answer",
            "confidence",
            "insufficient_evidence_reason",
            "evidence",
        ],
        "additionalProperties": False,
    },
}


def _insufficient_evidence_answer(reason: str) -> StructuredAnswer:
    return StructuredAnswer(
        has_sufficient_evidence=False,
        root_cause=(
            "No validated root cause could be determined from the "
            "available evidence."
        ),
        answer=_UNVALIDATED_ANSWER_TEXT,
        evidence=[],
        confidence="low",
        insufficient_evidence_reason=reason,
    )


def _resolve_repo_root(
    repo_root: str | os.PathLike[str] | None,
) -> Path | None:
    """Return a readable repository directory, or None when unprovable."""
    if repo_root is None:
        return None

    try:
        resolved = Path(repo_root).resolve(strict=True)
    except OSError:
        return None

    if not resolved.is_dir():
        return None
    if not os.access(resolved, os.R_OK | os.X_OK):
        return None
    return resolved


def _resolve_within_repo(
    resolved_root: Path,
    file_path: str,
) -> Path | None:
    """Resolve a non-empty repository-relative path without escaping."""
    if not file_path or not file_path.strip():
        return None
    if (
        PurePosixPath(file_path).is_absolute()
        or PureWindowsPath(file_path).is_absolute()
    ):
        return None

    try:
        candidate = (resolved_root / file_path).resolve(strict=True)
        candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return None

    return candidate if candidate.is_file() else None


def _normalize_for_comparison(text: str) -> str:
    """Normalize trailing whitespace without hiding substantive drift."""
    return "\n".join(line.rstrip() for line in text.splitlines()).rstrip("\n")


def _validate_block_against_filesystem(
    resolved_root: Path | None,
    block: RetrievedBlock,
) -> _BlockValidation:
    if resolved_root is None:
        return _BlockValidation(False, "no_repo_root")

    resolved_path = _resolve_within_repo(resolved_root, block.file_path)
    if resolved_path is None:
        return _BlockValidation(False, "path_invalid_or_traversal")

    try:
        with resolved_path.open(
            "r",
            encoding="utf-8",
            errors="strict",
        ) as handle:
            lines = handle.readlines()
    except (OSError, UnicodeError):
        return _BlockValidation(False, "file_unreadable")

    if not (1 <= block.start_line <= block.end_line <= len(lines)):
        return _BlockValidation(False, "range_out_of_bounds")

    slice_text = "".join(lines[block.start_line - 1 : block.end_line])
    if _normalize_for_comparison(slice_text) != _normalize_for_comparison(
        block.body
    ):
        return _BlockValidation(False, "content_drift")

    return _BlockValidation(True, "ok")


def _parse_evidence_item(
    raw_item: object,
    validations_by_citation: dict[
        tuple[str, int, int],
        _BlockValidation,
    ],
) -> EvidenceItem:
    """Parse and validate one evidence item, or raise ValueError."""
    if not isinstance(raw_item, dict):
        raise ValueError("an evidence item was not a JSON object")

    try:
        statement = raw_item["statement"]
        evidence_type = raw_item["evidence_type"]
        file_path = raw_item["file_path"]
        start_line = raw_item["start_line"]
        end_line = raw_item["end_line"]
    except KeyError as exc:
        raise ValueError(f"an evidence item was missing field {exc}") from exc

    unexpected_fields = set(raw_item) - _EVIDENCE_FIELDS
    if unexpected_fields:
        raise ValueError(
            "an evidence item contained unexpected fields: "
            f"{sorted(unexpected_fields)}"
        )

    if not isinstance(statement, str):
        raise ValueError("an evidence statement was not a string")
    if not statement.strip():
        raise ValueError("an evidence statement was empty")
    if evidence_type not in ("direct", "inference"):
        raise ValueError(f"evidence_type {evidence_type!r} was not recognized")

    if not (
        isinstance(file_path, str)
        and isinstance(start_line, int)
        and isinstance(end_line, int)
        and not isinstance(start_line, bool)
        and not isinstance(end_line, bool)
    ):
        raise ValueError("an evidence citation had an invalid shape")

    citation_key = (file_path, start_line, end_line)
    validation = validations_by_citation.get(citation_key)

    if validation is None:
        raise ValueError(
            f"citation {file_path}:{start_line}-{end_line} does not match "
            "any retrieved block"
        )
    if not validation.physically_valid:
        raise ValueError(
            f"citation {file_path}:{start_line}-{end_line} matches a "
            "retrieved block but failed physical validation "
            f"({validation.reason})"
        )

    return EvidenceItem(
        statement=statement,
        evidence_type=evidence_type,
        citation=Citation(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
        ),
    )


def _validate_answer_invariants(
    *,
    has_sufficient_evidence: bool,
    root_cause: str,
    answer: str,
    evidence: list[EvidenceItem],
    confidence: Confidence,
    insufficient_evidence_reason: str | None,
) -> str | None:
    """Return a rejection reason when answer fields contradict each other."""
    if not root_cause.strip():
        return "root_cause must be non-empty"
    if not answer.strip():
        return "answer must be non-empty"

    if has_sufficient_evidence:
        if not evidence:
            return "a sufficient answer must include evidence"
        if not any(item.evidence_type == "direct" for item in evidence):
            return "a sufficient answer must include direct evidence"
        if insufficient_evidence_reason is not None:
            return (
                "a sufficient answer must not include an insufficient "
                "evidence reason"
            )
        return None

    if insufficient_evidence_reason is None:
        return "an insufficient answer must include a reason"
    if not insufficient_evidence_reason.strip():
        return "an insufficient answer must include a non-empty reason"
    if confidence != "low":
        return "an insufficient answer must have low confidence"
    return None


def _parse_structured_answer(
    raw_output: str,
    blocks: list[RetrievedBlock],
    repo_root: str | os.PathLike[str] | None,
) -> StructuredAnswer:
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError:
        return _insufficient_evidence_answer(
            "the model response was not valid JSON"
        )

    if not isinstance(payload, dict):
        return _insufficient_evidence_answer(
            "the model response was not a JSON object"
        )

    try:
        has_sufficient_evidence = payload["has_sufficient_evidence"]
        root_cause = payload["root_cause"]
        answer_text = payload["answer"]
        raw_evidence = payload["evidence"]
        confidence = payload["confidence"]
        insufficient_reason = payload["insufficient_evidence_reason"]
    except KeyError as exc:
        return _insufficient_evidence_answer(
            f"the model response was missing required field {exc}"
        )

    unexpected_fields = set(payload) - _ANSWER_FIELDS
    if unexpected_fields:
        return _insufficient_evidence_answer(
            "the model response contained unexpected fields: "
            f"{sorted(unexpected_fields)}"
        )

    if not isinstance(has_sufficient_evidence, bool):
        return _insufficient_evidence_answer(
            "has_sufficient_evidence was not a boolean"
        )
    if not isinstance(root_cause, str):
        return _insufficient_evidence_answer("root_cause was not a string")
    if not root_cause.strip():
        return _insufficient_evidence_answer("root_cause was empty")
    if not isinstance(answer_text, str):
        return _insufficient_evidence_answer("answer was not a string")
    if not answer_text.strip():
        return _insufficient_evidence_answer("answer was empty")
    if confidence not in ("high", "medium", "low"):
        return _insufficient_evidence_answer(
            f"confidence {confidence!r} was not a recognized value"
        )
    if insufficient_reason is not None and not isinstance(insufficient_reason, str):
        return _insufficient_evidence_answer(
            "insufficient_evidence_reason was not a string or null"
        )
    if isinstance(insufficient_reason, str) and not insufficient_reason.strip():
        return _insufficient_evidence_answer(
            "insufficient_evidence_reason was empty"
        )
    if not isinstance(raw_evidence, list):
        return _insufficient_evidence_answer("evidence was not a list")

    resolved_root = _resolve_repo_root(repo_root)
    validations_by_citation = {
        (block.file_path, block.start_line, block.end_line): (
            _validate_block_against_filesystem(resolved_root, block)
        )
        for block in blocks
    }

    evidence_items: list[EvidenceItem] = []
    for raw_item in raw_evidence:
        try:
            evidence_items.append(
                _parse_evidence_item(raw_item, validations_by_citation)
            )
        except ValueError as exc:
            return _insufficient_evidence_answer(str(exc))

    invariant_error = _validate_answer_invariants(
        has_sufficient_evidence=has_sufficient_evidence,
        root_cause=root_cause,
        answer=answer_text,
        evidence=evidence_items,
        confidence=confidence,
        insufficient_evidence_reason=insufficient_reason,
    )
    if invariant_error is not None:
        return _insufficient_evidence_answer(invariant_error)

    return StructuredAnswer(
        has_sufficient_evidence=has_sufficient_evidence,
        root_cause=root_cause,
        answer=answer_text,
        evidence=evidence_items,
        confidence=confidence,
        insufficient_evidence_reason=insufficient_reason,
    )


def render_answer(structured: StructuredAnswer) -> str:
    """Render a validated StructuredAnswer as human-readable text."""
    lines = [
        structured.answer,
        "",
        f"Root cause: {structured.root_cause}",
    ]

    if structured.evidence:
        lines.append("")
        lines.append("Evidence:")
        for item in structured.evidence:
            lines.append(
                f"- [{item.evidence_type}] ({item.citation}) {item.statement}"
            )

    lines.append("")
    lines.append(f"Confidence: {structured.confidence}")

    if not structured.has_sufficient_evidence:
        reason = structured.insufficient_evidence_reason or (
            "not enough evidence was found"
        )
        lines.append("")
        lines.append(f"Insufficient evidence: {reason}")

    return "\n".join(lines)


def answer_question(
    question: str,
    blocks: list[RetrievedBlock],
    repo_root: str | os.PathLike[str] | None,
    client: OpenAI | None = None,
) -> StructuredAnswer:
    if client is None:
        api_key = os.environ.get("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is not set"
            )

        client = OpenAI(api_key=api_key)

    user_message = (
        f"Question: {question}\n\n"
        f"Resource blocks:\n{format_context(blocks)}"
    )

    response = client.responses.create(
        model=GENERATION_MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_message,
        text={"format": _ANSWER_JSON_SCHEMA},
    )

    return _parse_structured_answer(response.output_text, blocks, repo_root)
