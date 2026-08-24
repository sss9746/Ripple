from dataclasses import dataclass
from pathlib import Path

from ripple import db
from ripple.ingest import parser, references, scanner
from ripple.llm.embeddings import EmbeddingProvider, OpenAIEmbeddingProvider


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
    embedding: list[float]


@dataclass
class EdgeRow:
    source_id: int
    target_id: int
    ref_text: str


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


def index_repo(
    repo_id: int,
    local_path: str,
    embedder: EmbeddingProvider | None = None,
) -> int:
    """Parse a repository and atomically replace its saved resources."""
    root = Path(local_path)

    blocks = [
        block
        for file_path in scanner.find_tf_files(root)
        for block in parser.parse_file(file_path, root)
    ]

    embed_texts = [build_embed_text(block) for block in blocks]

    if not embed_texts:
        db.replace_resources(repo_id, [])
        return 0

    embedder = embedder or OpenAIEmbeddingProvider()
    embeddings = embedder.embed(embed_texts)

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
            embed_text=embed_texts[index],
            embedding=embeddings[index],
        )
        for index, block in enumerate(blocks)
    ]

    db.replace_resources(repo_id, rows)

    return len(rows)


def index_edges(repo_id: int) -> int:
    """Extract and save reference edges for previously indexed resources."""
    resource_rows = db.fetch_resource_bodies(repo_id)

    address_to_id = {
        address: resource_id
        for resource_id, address, _body in resource_rows
    }

    seen: set[tuple[int, int]] = set()
    edges: list[EdgeRow] = []

    for source_id, _source_address, body in resource_rows:
        for ref_text in references.extract_references(body):
            target_address = references._resolve_reference_address(ref_text)
            target_id = address_to_id.get(target_address)

            if target_id is None or target_id == source_id:
                continue

            edge_key = (source_id, target_id)

            if edge_key in seen:
                continue

            seen.add(edge_key)
            edges.append(
                EdgeRow(
                    source_id=source_id,
                    target_id=target_id,
                    ref_text=ref_text,
                )
            )

    db.replace_edges(repo_id, edges)
    return len(edges)
