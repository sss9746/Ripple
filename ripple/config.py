from dataclasses import dataclass
from typing import Literal


@dataclass
class RetrievalConfig:
    vector_backend: Literal["pgvector", "pinecone"] = "pgvector"
    use_vector: bool = True
    use_bm25: bool = True
    use_rrf: bool = True
    use_rerank: bool = True
    use_graph: bool = True
    use_rewrite: bool = False
    vector_k: int = 30
    bm25_k: int = 30
    rrf_k: int = 60
    rerank_top_n: int = 50
    final_k: int = 8
    graph_seed_n: int = 3
    graph_max_added: int = 10
    graph_route_by_intent: bool = False
