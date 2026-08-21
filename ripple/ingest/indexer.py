from dataclasses import dataclass
from pathlib import Path

from ripple import db
from ripple.ingest import parser, scanner


MAX_EMBED_BODY_CHARS = 6000


@dataclass
class ResourceRow:
    block_kind: str
    resource_type: str | None
    resource_name: str | None
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str


def build_embed_text(block: parser.ParsedBlock) -> str:
    """Add identifying context before a block's searchable body."""
    type_label = block.resource_type or block.block_kind

    header = (
        f"{block.address}\n"
        f"File: {block.file_path}\n"
        f"Type: {type_label}\n\n"
    )

    body = block.body

    if len(body) > MAX_EMBED_BODY_CHARS:
        print(
            f"WARNING: truncating embed_text body for {block.address} "
            f"({len(body)} -> {MAX_EMBED_BODY_CHARS} chars)"
        )
        body = body[:MAX_EMBED_BODY_CHARS]

    return header + body


def index_repo(repo_id: int, local_path: str) -> int:
    """Parse a repository and atomically replace its saved resources."""
    root = Path(local_path)

    blocks = [
        block
        for file_path in scanner.find_tf_files(root)
        for block in parser.parse_file(file_path, root)
    ]

    rows = [
        ResourceRow(
            block_kind=block.block_kind,
            resource_type=block.resource_type,
            resource_name=block.resource_name,
            address=block.address,
            file_path=block.file_path,
            start_line=block.start_line,
            end_line=block.end_line,
            body=block.body,
            embed_text=build_embed_text(block),
        )
        for block in blocks
    ]

    db.replace_resources(repo_id, rows)

    return len(rows)
