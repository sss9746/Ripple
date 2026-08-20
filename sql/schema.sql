CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE repos (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    source_url  TEXT,
    local_path  TEXT NOT NULL,
    indexed_at  TIMESTAMPTZ
);

CREATE TABLE resources (
    id             SERIAL PRIMARY KEY,
    repo_id        INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    block_kind     TEXT NOT NULL,   -- resource | data | module | variable | output | locals
    resource_type  TEXT,            -- aws_security_group  (NULL for non-resource blocks)
    resource_name  TEXT,            -- worker
    address        TEXT NOT NULL,   -- aws_security_group.worker
    file_path      TEXT NOT NULL,
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    body           TEXT NOT NULL,   -- raw source text of the block
    embed_text     TEXT NOT NULL,   -- what was actually embedded (see 9.3)
    embedding      vector(1536)
);

CREATE INDEX ON resources USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON resources (repo_id);
CREATE UNIQUE INDEX ON resources (repo_id, address);

CREATE TABLE edges (
    id          SERIAL PRIMARY KEY,
    repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    source_id   INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    target_id   INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    ref_text    TEXT NOT NULL      -- the literal reference found, e.g. aws_vpc.main.id
);

CREATE INDEX ON edges (source_id);
CREATE INDEX ON edges (target_id);

CREATE TABLE query_logs (
    id             SERIAL PRIMARY KEY,
    repo_id        INTEGER REFERENCES repos(id) ON DELETE CASCADE,
    question       TEXT NOT NULL,
    config_json    JSONB NOT NULL,   -- which stages were on
    stages_json    JSONB NOT NULL,   -- per-stage candidates and scores
    latency_json   JSONB NOT NULL,   -- per-stage milliseconds
    answer         TEXT,
    created_at     TIMESTAMPTZ DEFAULT now()
);
