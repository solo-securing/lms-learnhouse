"""Tests for src/services/courses/activities/pdf.py — the update path against Drive video activities."""

import io
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.chapter_activities import ChapterActivity
from src.services.courses.activities.pdf import update_documentpdf_activity

_P = "src.services.courses.activities.pdf"
_STORAGE_CHANGE_DETAIL = "Video : Storage location of a video activity cannot be changed"


def _pdf_upload(name="notes.pdf"):
    return UploadFile(
        file=io.BytesIO(b"%PDF-1.4\n%fake"), filename=name, headers=Headers({"content-type": "application/pdf"})
    )


@pytest.fixture
async def pdf_activity(db, org, course, chapter):
    a = Activity(
        id=3,
        name="Notes",
        activity_type=ActivityTypeEnum.TYPE_DOCUMENT,
        activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DOCUMENT_PDF,
        content={"filename": "old.pdf", "activity_uuid": "activity_pdf"},
        published=True,
        org_id=org.id,
        course_id=course.id,
        activity_uuid="activity_pdf",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    db.add(ChapterActivity(
        order=3, chapter_id=chapter.id, activity_id=a.id, course_id=course.id, org_id=org.id,
        creation_date=str(datetime.now()), update_date=str(datetime.now()),
    ))
    await db.commit()
    return a


class TestUpdateDocumentPdfOnDriveActivity:
    @pytest.mark.asyncio
    async def test_drive_activity_is_refused_and_nothing_is_uploaded(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        """PUT /activities/documentpdf/{uuid} must not rename a Drive video, upload a PDF
        into its server path or add `content.filename` to it (FR-004 / FR-014)."""
        original = dict(gdrive_activity.content)
        with patch(f"{_P}.check_resource_access", new_callable=AsyncMock), patch(
            f"{_P}.upload_pdf", new_callable=AsyncMock, return_value="hijack.pdf"
        ) as upload:
            with pytest.raises(HTTPException) as err:
                await update_documentpdf_activity(
                    mock_request, gdrive_activity.activity_uuid, admin_user, db, name="Hijacked", pdf_file=_pdf_upload()
                )
        assert err.value.status_code == 409
        assert err.value.detail == _STORAGE_CHANGE_DETAIL
        upload.assert_not_awaited()
        await db.refresh(gdrive_activity)
        assert gdrive_activity.content == original
        assert gdrive_activity.name == "Drive Video"

    @pytest.mark.asyncio
    async def test_pdf_activity_keeps_working(self, mock_request, db, org, course, chapter, pdf_activity, admin_user):
        with patch(f"{_P}.check_resource_access", new_callable=AsyncMock), patch(
            f"{_P}.upload_pdf", new_callable=AsyncMock, return_value="new.pdf"
        ) as upload:
            result = await update_documentpdf_activity(
                mock_request, pdf_activity.activity_uuid, admin_user, db, name="Notes v2", pdf_file=_pdf_upload()
            )
        upload.assert_awaited_once()
        assert result.name == "Notes v2"
        assert result.content["filename"] == "new.pdf"
