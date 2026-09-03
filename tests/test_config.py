from dataclasses import asdict

from ripple.config import RetrievalConfig


def test_retrieval_config_defaults() -> None:
    config = RetrievalConfig()

    assert asdict(config) == {
        "vector_backend": "pgvector",
        "use_vector": True,
        "use_bm25": True,
        "use_rrf": True,
        "use_rerank": True,
        "use_graph": True,
        "use_rewrite": False,
        "vector_k": 30,
        "bm25_k": 30,
        "rrf_k": 60,
        "rerank_top_n": 50,
        "final_k": 8,
        "graph_seed_n": 3,
        "graph_max_added": 10,
        "graph_route_by_intent": False,
    }
