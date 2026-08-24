import re

from ripple.ingest.parser import HEREDOC_START_RE


REF_RE = re.compile(
    r"\b(?:data\.)?([a-z][a-z0-9_]*)\.([a-z_][a-z0-9_-]*)"
    r"(?:\.[a-z_][a-z0-9_*-]*|\[[a-z0-9_.*-]+\])*"
)


def _mask_comments(text: str) -> str:
    """Replace Terraform comments with spaces while preserving strings and heredocs."""
    result = list(text)
    index = 0
    text_length = len(text)

    while index < text_length:
        character = text[index]

        if character == '"':
            index += 1

            while index < text_length and text[index] != '"':
                index += 2 if text[index] == "\\" else 1

            index += 1
            continue

        heredoc_match = HEREDOC_START_RE.match(text, index)

        if heredoc_match:
            marker = heredoc_match.group("marker")
            terminator_pattern = re.compile(
                rf"^[ \t]*{re.escape(marker)}\s*$",
                re.MULTILINE,
            )
            terminator = terminator_pattern.search(
                text,
                heredoc_match.end(),
            )
            index = terminator.end() if terminator else text_length
            continue

        if character == "#" or text[index : index + 2] == "//":
            newline_index = text.find("\n", index)
            comment_end = (
                newline_index if newline_index != -1 else text_length
            )

            for comment_index in range(index, comment_end):
                result[comment_index] = " "

            index = comment_end
            continue

        if text[index : index + 2] == "/*":
            closing_index = text.find("*/", index + 2)
            comment_end = (
                closing_index + 2 if closing_index != -1 else text_length
            )

            for comment_index in range(index, comment_end):
                if text[comment_index] != "\n":
                    result[comment_index] = " "

            index = comment_end
            continue

        index += 1

    return "".join(result)


def extract_references(body: str) -> list[str]:
    """Return Terraform references in their order of appearance."""
    masked_body = _mask_comments(body)
    return [match.group(0) for match in REF_RE.finditer(masked_body)]


def _resolve_reference_address(ref_text: str) -> str:
    """Convert a Terraform reference into its stored resource address."""
    match = REF_RE.match(ref_text)
    resource_type = match.group(1)
    resource_name = match.group(2)

    if ref_text.startswith("data."):
        return f"data.{resource_type}.{resource_name}"

    return f"{resource_type}.{resource_name}"
