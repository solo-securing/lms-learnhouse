"""Add Google Drive video activity subtype

Revision ID: g1d2r3i4v5e6
Revises: b1c2d3e4f5a6
Create Date: 2026-09-16 00:00:00.000000

Adds SUBTYPE_VIDEO_GDRIVE to activitysubtypeenum so a TYPE_VIDEO activity can
keep its file on the operator's Google Drive (see
specs/001-gdrive-video-upload). No columns change; ``content`` carries the
Drive file/folder ids.

Downgrade note: ``sync_enum_values`` rebuilds the Postgres enum without the
value, which FAILS while any ``activity`` row still has
``activity_sub_type = 'SUBTYPE_VIDEO_GDRIVE'``. Delete (or re-create on the
server) every Drive video activity before downgrading.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa  # noqa: F401
import sqlmodel  # noqa: F401
from alembic_postgresql_enum import TableReference  # type: ignore

# revision identifiers, used by Alembic.
revision: str = 'g1d2r3i4v5e6'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_WITHOUT_GDRIVE = [
    'SUBTYPE_DYNAMIC_PAGE', 'SUBTYPE_DYNAMIC_MARKDOWN', 'SUBTYPE_DYNAMIC_EMBED',
    'SUBTYPE_DYNAMIC_RESOURCE', 'SUBTYPE_VIDEO_YOUTUBE', 'SUBTYPE_VIDEO_HOSTED',
    'SUBTYPE_DOCUMENT_PDF', 'SUBTYPE_DOCUMENT_DOC', 'SUBTYPE_ASSIGNMENT_ANY',
    'SUBTYPE_CUSTOM', 'SUBTYPE_SCORM_12', 'SUBTYPE_SCORM_2004',
]
_WITH_GDRIVE = [
    'SUBTYPE_DYNAMIC_PAGE', 'SUBTYPE_DYNAMIC_MARKDOWN', 'SUBTYPE_DYNAMIC_EMBED',
    'SUBTYPE_DYNAMIC_RESOURCE', 'SUBTYPE_VIDEO_YOUTUBE', 'SUBTYPE_VIDEO_HOSTED',
    'SUBTYPE_VIDEO_GDRIVE',
    'SUBTYPE_DOCUMENT_PDF', 'SUBTYPE_DOCUMENT_DOC', 'SUBTYPE_ASSIGNMENT_ANY',
    'SUBTYPE_CUSTOM', 'SUBTYPE_SCORM_12', 'SUBTYPE_SCORM_2004',
]
_REFS = [TableReference(table_schema='public', table_name='activity', column_name='activity_sub_type')]


def upgrade() -> None:
    op.sync_enum_values(
        'public',
        'activitysubtypeenum',
        _WITH_GDRIVE,
        _REFS,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    # Fails if any SUBTYPE_VIDEO_GDRIVE row remains — remove Drive video
    # activities first (see module docstring).
    op.sync_enum_values(
        'public',
        'activitysubtypeenum',
        _WITHOUT_GDRIVE,
        _REFS,
        enum_values_to_rename=[],
    )
