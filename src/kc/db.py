"""SQLite schema (documents/chunks/claims/entities/relations + FTS5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id           TEXT PRIMARY KEY,
  title        TEXT,
  source_path  TEXT,
  source_type  TEXT,
  sha256       TEXT UNIQUE,
  content      TEXT,
  char_count   INTEGER DEFAULT 0,
  lang         TEXT,
  summary      TEXT,
  model        TEXT,
  created_at   TEXT,
  compiled_at  TEXT,
  ir_json      TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
  id          INTEGER PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ord         INTEGER NOT NULL DEFAULT 0,
  text        TEXT NOT NULL,
  char_count  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS claims (
  id            INTEGER PRIMARY KEY,
  document_id   TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  text          TEXT NOT NULL,
  norm          TEXT,
  type          TEXT DEFAULT 'fact',
  confidence    REAL DEFAULT 0.6,
  source        TEXT,
  status        TEXT DEFAULT 'active',
  superseded_by INTEGER,
  created_at    TEXT
);

CREATE TABLE IF NOT EXISTS entities (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,
  norm      TEXT UNIQUE,
  type      TEXT DEFAULT 'other',
  aliases   TEXT,
  doc_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS entity_mentions (
  entity_id   INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  PRIMARY KEY (entity_id, document_id)
);

CREATE TABLE IF NOT EXISTS relations (
  id          INTEGER PRIMARY KEY,
  document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
  subject     TEXT,
  predicate   TEXT,
  object      TEXT,
  confidence  REAL DEFAULT 0.6
);

CREATE TABLE IF NOT EXISTS compile_log (
  sha256      TEXT PRIMARY KEY,
  document_id TEXT,
  source_path TEXT,
  status      TEXT,
  error       TEXT,
  compiled_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc   ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_claims_doc   ON claims(document_id);
CREATE INDEX IF NOT EXISTS idx_claims_norm  ON claims(norm);
CREATE INDEX IF NOT EXISTS idx_rel_doc      ON relations(document_id);
CREATE INDEX IF NOT EXISTS idx_mentions_doc ON entity_mentions(document_id);

-- 外部内容 FTS5（触发器同步）；trigram 支持中文子串检索
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text,
  content='chunks', content_rowid='id',
  tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(
  text,
  content='claims', content_rowid='id',
  tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS claims_ai AFTER INSERT ON claims BEGIN
  INSERT INTO claims_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS claims_ad AFTER DELETE ON claims BEGIN
  INSERT INTO claims_fts(claims_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS claims_au AFTER UPDATE ON claims BEGIN
  INSERT INTO claims_fts(claims_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO claims_fts(rowid, text) VALUES (new.id, new.text);
END;
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


# ---------------------------------------------------------------- migrations
# user_version history:
#   0 → V0.1 baseline (no embeddings / aliases / claim lifecycle columns)
#   2 → V0.2: embeddings, entity_aliases, claims.duplicate_of & superseded_reason
#   3 → V0.2: claims.dedup_checked (incremental dedup bookkeeping)
MIGRATIONS: dict[int, str] = {
    2: """
    CREATE TABLE IF NOT EXISTS embeddings (
      id          INTEGER PRIMARY KEY,
      scope       TEXT NOT NULL,
      ref_id      INTEGER NOT NULL,
      model       TEXT NOT NULL,
      dim         INTEGER NOT NULL,
      vec         BLOB NOT NULL,
      content_sha TEXT NOT NULL,
      UNIQUE(scope, ref_id, model)
    );
    CREATE INDEX IF NOT EXISTS idx_emb_scope ON embeddings(scope, model);

    CREATE TABLE IF NOT EXISTS entity_aliases (
      alias_norm TEXT PRIMARY KEY,
      alias      TEXT NOT NULL,
      entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_alias_entity ON entity_aliases(entity_id);
    """,
    3: """
    """,
}


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= max(MIGRATIONS):
        return
    for target in sorted(MIGRATIONS):
        if version < target:
            conn.executescript(MIGRATIONS[target])
            if target == 2:  # ALTERs aren't idempotent → guard by column probe
                for col, ddl in (
                    ("duplicate_of", "ALTER TABLE claims ADD COLUMN duplicate_of INTEGER"),
                    ("superseded_reason", "ALTER TABLE claims ADD COLUMN superseded_reason TEXT"),
                ):
                    if col not in _cols(conn, "claims"):
                        conn.execute(ddl)
            if target == 3:
                if "dedup_checked" not in _cols(conn, "claims"):
                    conn.execute("ALTER TABLE claims ADD COLUMN dedup_checked INTEGER DEFAULT 0")
            conn.execute(f"PRAGMA user_version={target}")
            version = target
    conn.commit()
