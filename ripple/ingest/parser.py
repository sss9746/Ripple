import re
from dataclasses import dataclass
from pathlib import Path

import hcl2


BLOCK_RE = re.compile(
    r'^(resource|data|module|variable|output|locals)\s*'
    r'(?:"([^"]+)"\s*)?(?:"([^"]+)"\s*)?\{',
    re.MULTILINE,
)

HEREDOC_START_RE = re.compile(r"<<-?(?P<marker>[A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class ParsedBlock:
    block_kind: str
    resource_type: str | None
    resource_name: str | None
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str


def _find_block_end(text: str, open_brace_index: int) -> int:
    """Return the index of the closing brace matching the given opening brace."""
    depth = 0
    index = open_brace_index
    text_length = len(text)

    while index < text_length:
        character = text[index]

        if character == "#" or text[index : index + 2] == "//":
            newline_index = text.find("\n", index)
            index = newline_index if newline_index != -1 else text_length
            continue

        if text[index : index + 2] == "/*":
            comment_end = text.find("*/", index + 2)
            index = comment_end + 2 if comment_end != -1 else text_length
            continue

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
            terminator = terminator_pattern.search(text, heredoc_match.end())
            index = terminator.end() if terminator else text_length
            continue

        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index

        index += 1

    raise ValueError(f"Unbalanced braces starting at index {open_brace_index}")


def _address_for(
    kind: str,
    resource_type: str | None,
    resource_name: str | None,
    file_path: str,
    start_line: int,
) -> str:
    if kind == "resource":
        return f"{resource_type}.{resource_name}"

    if kind == "data":
        return f"data.{resource_type}.{resource_name}"

    if kind == "module":
        return f"module.{resource_name}"

    if kind == "variable":
        return f"var.{resource_name}"

    if kind == "output":
        return f"output.{resource_name}"

    return f"locals:{file_path}:{start_line}"


def parse_file(path: Path, repo_root: Path) -> list[ParsedBlock]:
    """Parse one Terraform file into blocks with exact line ranges."""
    text = path.read_text()

    with path.open() as file:
        try:
            hcl2.load(file)
        except Exception as exc:
            raise ValueError(f"{path}: not valid HCL: {exc}") from exc

    lines = text.splitlines()
    file_path = str(path.relative_to(repo_root))
    blocks: list[ParsedBlock] = []

    for match in BLOCK_RE.finditer(text):
        kind = match.group(1)
        label1 = match.group(2)
        label2 = match.group(3)

        if kind in ("resource", "data"):
            resource_type = label1
            resource_name = label2
        elif kind in ("module", "variable", "output"):
            resource_type = None
            resource_name = label1
        else:
            resource_type = None
            resource_name = None

        open_brace_index = match.end() - 1
        close_brace_index = _find_block_end(
            text,
            open_brace_index,
        )

        start_line = text.count(
            "\n",
            0,
            match.start(),
        ) + 1

        end_line = text.count(
            "\n",
            0,
            close_brace_index,
        ) + 1

        body = "\n".join(
            lines[start_line - 1 : end_line]
        )

        address = _address_for(
            kind,
            resource_type,
            resource_name,
            file_path,
            start_line,
        )

        blocks.append(
            ParsedBlock(
                block_kind=kind,
                resource_type=resource_type,
                resource_name=resource_name,
                address=address,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                body=body,
            )
        )

    return blocks
