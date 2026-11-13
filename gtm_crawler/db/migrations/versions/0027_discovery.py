"""crawl_job kind 'discovery' and table competitor_candidate (discovery.py)

* crawl_job.kind may be 'discovery': a job that searches for the company's
  competitors from its business profile.
* competitor_candidate: what one discovery job found, one row per domain.

Downgrade drops the table and restores the old kind check as NOT VALID: discovery
jobs already written stay; new ones are refused.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-13 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_crawl_job_kind"), "crawl_job", type_="check")
    op.create_check_constraint(
        op.f("ck_crawl_job_kind"), "crawl_job", "kind IN ('crawl', 'refresh', 'discovery')"
    )
    op.create_table(
        "competitor_candidate",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(),
                  sa.ForeignKey("organization.id",
                                name=op.f("fk_competitor_candidate_organization_id_organization")),
                  nullable=False),
        sa.Column("job_id", sa.BigInteger(),
                  sa.ForeignKey("crawl_job.id",
                                name=op.f("fk_competitor_candidate_job_id_crawl_job")),
                  nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("class", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("found_by", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("class IN ('direct', 'indirect', 'substitute', 'emerging')",
                           name=op.f("ck_competitor_candidate_class")),
        sa.CheckConstraint("basis IN ('named_by_source', 'inferred_overlap', 'user_added')",
                           name=op.f("ck_competitor_candidate_basis")),
        sa.UniqueConstraint("job_id", "domain", name=op.f("uq_competitor_candidate_job_id_domain")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_candidate")),
    )  # fmt: skip
    op.create_index(op.f("ix_competitor_candidate_organization_id"), "competitor_candidate",
                    ["organization_id"])  # fmt: skip
    op.create_index(op.f("ix_competitor_candidate_job_id"), "competitor_candidate", ["job_id"])


def downgrade() -> None:
    op.drop_table("competitor_candidate")
    op.drop_constraint(op.f("ck_crawl_job_kind"), "crawl_job", type_="check")
    op.execute(
        "ALTER TABLE crawl_job ADD CONSTRAINT ck_crawl_job_kind CHECK (kind IN ('crawl', "
        "'refresh')) NOT VALID"
    )
