import json
import os
from dataclasses import dataclass
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
    citation: Citation | None


@dataclass
class StructuredAnswer:
    has_sufficient_evidence: bool
    answer: str
    evidence: list[EvidenceItem]
    confidence: Confidence
    insufficient_evidence_reason: str | None


_ANSWER_JSON_SCHEMA = {
    "type": "json_schema",
    "name": "structured_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "has_sufficient_evidence": {"type": "boolean"},
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
                        "file_path": {"type": ["string", "null"]},
                        "start_line": {"type": ["integer", "null"]},
                        "end_line": {"type": ["integer", "null"]},
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
        answer=_UNVALIDATED_ANSWER_TEXT,
        evidence=[],
        confidence="low",
        insufficient_evidence_reason=reason,
    )


def _parse_evidence_item(
    raw_item: object,
    valid_citations: set[tuple[str, int, int]],
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

    if not isinstance(statement, str):
        raise ValueError("an evidence statement was not a string")
    if evidence_type not in ("direct", "inference"):
        raise ValueError(f"evidence_type {evidence_type!r} was not recognized")

    citation: Citation | None = None
    fields_present = (
        file_path is not None or start_line is not None or end_line is not None
    )

    if fields_present:
        if not (
            isinstance(file_path, str)
            and isinstance(start_line, int)
            and isinstance(end_line, int)
            and not isinstance(start_line, bool)
            and not isinstance(end_line, bool)
        ):
            raise ValueError("an evidence citation had an invalid shape")
        if (file_path, start_line, end_line) not in valid_citations:
            raise ValueError(
                f"citation {file_path}:{start_line}-{end_line} does not match "
                "any retrieved block"
            )
        citation = Citation(
            file_path=file_path, start_line=start_line, end_line=end_line
        )
    elif evidence_type == "direct":
        raise ValueError("direct evidence must include a citation")

    return EvidenceItem(
        statement=statement, evidence_type=evidence_type, citation=citation
    )


def _parse_structured_answer(
    raw_output: str, blocks: list[RetrievedBlock]
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
        answer_text = payload["answer"]
        raw_evidence = payload["evidence"]
        confidence = payload["confidence"]
        insufficient_reason = payload["insufficient_evidence_reason"]
    except KeyError as exc:
        return _insufficient_evidence_answer(
            f"the model response was missing required field {exc}"
        )

    if not isinstance(has_sufficient_evidence, bool):
        return _insufficient_evidence_answer(
            "has_sufficient_evidence was not a boolean"
        )
    if not isinstance(answer_text, str):
        return _insufficient_evidence_answer("answer was not a string")
    if confidence not in ("high", "medium", "low"):
        return _insufficient_evidence_answer(
            f"confidence {confidence!r} was not a recognized value"
        )
    if insufficient_reason is not None and not isinstance(insufficient_reason, str):
        return _insufficient_evidence_answer(
            "insufficient_evidence_reason was not a string or null"
        )
    if not isinstance(raw_evidence, list):
        return _insufficient_evidence_answer("evidence was not a list")

    valid_citations = {
        (block.file_path, block.start_line, block.end_line) for block in blocks
    }

    evidence_items: list[EvidenceItem] = []
    for raw_item in raw_evidence:
        try:
            evidence_items.append(
                _parse_evidence_item(raw_item, valid_citations)
            )
        except ValueError as exc:
            return _insufficient_evidence_answer(str(exc))

    return StructuredAnswer(
        has_sufficient_evidence=has_sufficient_evidence,
        answer=answer_text,
        evidence=evidence_items,
        confidence=confidence,
        insufficient_evidence_reason=insufficient_reason,
    )


def render_answer(structured: StructuredAnswer) -> str:
    """Render a validated StructuredAnswer as human-readable text."""
    lines = [structured.answer]

    if structured.evidence:
        lines.append("")
        lines.append("Evidence:")
        for item in structured.evidence:
            citation_text = f" ({item.citation})" if item.citation else ""
            lines.append(
                f"- [{item.evidence_type}]{citation_text} {item.statement}"
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

    return _parse_structured_answer(response.output_text, blocks)
