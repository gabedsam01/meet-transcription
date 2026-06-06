"""add transcript full-text search index

Revision ID: 0002_transcript_fts
Revises: 0001_initial
Create Date: 2026-06-05

Adds a GIN index on ``to_tsvector('simple', transcript_text)`` so the per-user
transcript search (``PgTranscriptRepository.search_transcripts``) is index-backed.
The ``simple`` text-search configuration needs no language extension and is always
available in PostgreSQL. This is an index-only change — no column/table change.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_transcript_fts"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_transcripts_text_fts"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "transcripts",
        [sa.text("to_tsvector('simple', transcript_text)")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="transcripts")
