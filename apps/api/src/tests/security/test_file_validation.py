"""
Unit tests for src/security/file_validation.py

Focus on the media-upload surface: Office (OOXML + legacy OLE) and zip archives
are accepted only when their bytes match, junk/renamed files are rejected, SVG is
blocked, and the stored filename comes from the validated content type.
"""

import io
import zipfile

import pytest
from fastapi import HTTPException

from src.security.file_validation import (
    validate_upload,
    validate_ole_content,
    validate_zip_content,
    get_safe_filename,
)

# The categories the media upload endpoint allows (see src/services/media/media.py).
MEDIA_TYPES = ["image", "video", "document", "audio", "office", "office_legacy", "archive"]


class _FakeUpload:
    def __init__(self, filename, data, content_type="application/octet-stream"):
        self.filename = filename
        self.content_type = content_type
        self.file = io.BytesIO(data)


def _zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    return buf.getvalue()


_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class TestValidateUploadMedia:
    @pytest.mark.parametrize(
        "name,data,expected_ext",
        [
            ("report.docx", _zip_bytes(), "docx"),
            ("sheet.xlsx", _zip_bytes(), "xlsx"),
            ("deck.pptx", _zip_bytes(), "pptx"),
            ("legacy.doc", _OLE, "doc"),
            ("legacy.xls", _OLE, "xls"),
            ("legacy.ppt", _OLE, "ppt"),
            ("bundle.zip", _zip_bytes(), "zip"),
            ("logo.png", _PNG, "png"),
        ],
    )
    def test_accepts_and_normalizes_extension(self, name, data, expected_ext):
        ctype, content = validate_upload(_FakeUpload(name, data), MEDIA_TYPES)
        stored = get_safe_filename(name, "uuid_media", content_type=ctype)
        assert stored == f"uuid_media.{expected_ext}"
        assert content == data

    def test_rejects_disallowed_extension(self):
        with pytest.raises(HTTPException) as exc:
            validate_upload(_FakeUpload("evil.exe", b"MZ\x90\x00" * 4), MEDIA_TYPES)
        assert exc.value.status_code == 415

    def test_rejects_zip_renamed_as_docx_when_not_zip(self):
        with pytest.raises(HTTPException) as exc:
            validate_upload(_FakeUpload("fake.docx", b"not a zip at all"), MEDIA_TYPES)
        assert exc.value.status_code == 415

    def test_rejects_ole_renamed_as_doc_when_not_ole(self):
        with pytest.raises(HTTPException) as exc:
            validate_upload(_FakeUpload("fake.doc", b"plain text, not OLE"), MEDIA_TYPES)
        assert exc.value.status_code == 415

    def test_blocks_svg(self):
        with pytest.raises(HTTPException) as exc:
            validate_upload(_FakeUpload("x.svg", b"<svg></svg>"), MEDIA_TYPES)
        assert exc.value.status_code == 415

    def test_no_file(self):
        with pytest.raises(HTTPException) as exc:
            validate_upload(_FakeUpload("", b""), MEDIA_TYPES)
        assert exc.value.status_code == 400


class TestZipAndOleValidators:
    def test_validate_zip_accepts_real_zip(self):
        assert validate_zip_content(_zip_bytes()) is True

    def test_validate_zip_rejects_non_zip(self):
        assert validate_zip_content(b"nope") is False

    def test_validate_zip_rejects_zip_bomb(self):
        # A single entry whose declared uncompressed size exceeds the 500 MB cap.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.bin", b"\x00" * (1024 * 1024))  # 1 MB real
            zf.infolist()[0].file_size = 600 * 1024 * 1024  # fake 600 MB declared
        assert validate_zip_content(buf.getvalue()) is False

    def test_validate_ole(self):
        assert validate_ole_content(_OLE) is True
        assert validate_ole_content(b"PK\x03\x04stuff") is False


class TestSafeFilename:
    def test_content_type_drives_extension_not_client_name(self):
        # client claims .html but the validated type is png -> stored as .png
        stored = get_safe_filename("evil.html", "uuid", content_type="image/png")
        assert stored == "uuid.png"

    def test_unknown_content_type_falls_back_to_bin(self):
        assert get_safe_filename("x.weird", "uuid", content_type="application/x-unknown") == "uuid.bin"


# ---------------------------------------------------------------------------
# validate_upload_stream — validation without reading the body into memory
# (Google Drive upload path streams the spooled file onwards).
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

from src.security.file_validation import validate_upload_stream  # noqa: E402

_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
_WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


class _CountingBytesIO(io.BytesIO):
    """Records how many bytes were actually read through the stream."""

    def __init__(self, data):
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size=-1):
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


class TestValidateUploadStream:
    @pytest.mark.parametrize(
        "name,data,expected_mime",
        [("clip.mp4", _MP4, "video/mp4"), ("clip.webm", _WEBM, "video/webm")],
    )
    def test_accepts_video_and_returns_mime_and_size(self, name, data, expected_mime):
        upload = _FakeUpload(name, data, content_type=expected_mime)
        mime, size = validate_upload_stream(upload, ["video"])
        assert mime == expected_mime
        assert size == len(data)
        # Stream is rewound so the caller can stream the body onwards.
        assert upload.file.tell() == 0
        assert upload.file.read() == data

    def test_does_not_read_whole_body(self):
        data = _MP4 + b"\x00" * (200 * 1024)
        stream = _CountingBytesIO(data)
        upload = _FakeUpload("clip.mp4", b"", content_type="video/mp4")
        upload.file = stream
        _mime, size = validate_upload_stream(upload, ["video"])
        assert size == len(data)
        # Only the 64 KiB magic-byte prefix is read; never the full body.
        assert stream.bytes_read <= 64 * 1024

    def test_rejects_disallowed_extension_before_reading(self):
        stream = _CountingBytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 32)
        upload = _FakeUpload("movie.mkv", b"", content_type="video/x-matroska")
        upload.file = stream
        with pytest.raises(HTTPException) as exc:
            validate_upload_stream(upload, ["video"])
        assert exc.value.status_code == 415
        assert stream.bytes_read == 0

    def test_rejects_over_five_gib(self):
        upload = _FakeUpload("huge.mp4", _MP4, content_type="video/mp4")
        with patch(
            "src.security.file_validation._stream_length",
            return_value=5 * 1024 * 1024 * 1024 + 1,
        ):
            with pytest.raises(HTTPException) as exc:
                validate_upload_stream(upload, ["video"])
        assert exc.value.status_code == 413

    def test_rejects_wrong_magic_bytes(self):
        upload = _FakeUpload("clip.mp4", b"definitely not a video" + b"\x00" * 32, content_type="video/mp4")
        with pytest.raises(HTTPException) as exc:
            validate_upload_stream(upload, ["video"])
        assert exc.value.status_code == 415

    def test_blocks_svg_and_missing_file(self):
        with pytest.raises(HTTPException) as exc:
            validate_upload_stream(_FakeUpload("x.svg", b"<svg/>"), ["video"])
        assert exc.value.status_code == 415
        with pytest.raises(HTTPException) as exc:
            validate_upload_stream(_FakeUpload("", b""), ["video"])
        assert exc.value.status_code == 400

    def test_unmeasurable_stream_falls_back_to_bounded_read(self):
        class _NoSeekEnd(io.BytesIO):
            def seek(self, pos, whence=0):
                if whence == 2:
                    raise OSError("cannot seek to end")
                return super().seek(pos, whence)

        upload = _FakeUpload("clip.mp4", b"", content_type="video/mp4")
        upload.file = _NoSeekEnd(_MP4)
        mime, size = validate_upload_stream(upload, ["video"], max_size=1024 * 1024)
        assert mime == "video/mp4"
        assert size == len(_MP4)

    def test_validate_upload_still_returns_bytes(self):
        # The legacy API is a thin wrapper: same checks, plus the body.
        upload = _FakeUpload("clip.mp4", _MP4, content_type="video/mp4")
        mime, content = validate_upload(upload, ["video"])
        assert mime == "video/mp4"
        assert content == _MP4
        assert upload.file.tell() == 0
