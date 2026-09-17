# Data Model: Upload video Activity lên Google Drive

**Feature**: `001-gdrive-video-upload` | **Date**: 2026-09-16

## 1. Activity (mở rộng, bảng `activity`)

Không thêm cột. Thay đổi duy nhất ở lược đồ: giá trị mới trong enum Postgres `activitysubtypeenum`.

| Trường | Kiểu | Ghi chú |
|--------|------|---------|
| `activity_type` | enum | `TYPE_VIDEO` (không đổi) |
| `activity_sub_type` | enum | **mới**: `SUBTYPE_VIDEO_GDRIVE` (cùng nhóm với `SUBTYPE_VIDEO_HOSTED`, `SUBTYPE_VIDEO_YOUTUBE`) |
| `content` | JSON | shape riêng cho Drive, xem dưới |
| `details` | JSON | luôn `{}` cho Drive (không có thiết lập phát) |
| `extra_metadata` | JSONB | không dùng `hls`/`captions` cho Drive |

### `content` của Activity Drive

```json
{
  "activity_uuid": "activity_<uuid4>",
  "storage": "gdrive",
  "gdrive_file_id": "<Drive file id>",
  "gdrive_folder_id": "<Drive folder id của activity>",
  "original_filename": "bai-1.mp4",
  "mime_type": "video/mp4",
  "size": 123456789
}
```

- `gdrive_file_id`, `gdrive_folder_id`: khớp `^[A-Za-z0-9_-]+$`.
- `original_filename`: tên file người dùng chọn (chỉ để hiển thị; không dùng làm đường dẫn).
- `mime_type` ∈ {`video/mp4`, `video/webm`}; `size` ≤ 5 GiB.
- KHÔNG có `uri`, `filename`, `webViewLink`. URL nhúng do client suy ra: `https://drive.google.com/file/d/<gdrive_file_id>/preview`.

### Quan hệ

Không đổi: Activity thuộc `organization` (`org_id`, CASCADE), `course` (`course_id`, CASCADE), liên kết chương qua `chapter_activity`. Xoá ở cấp DB (course/org cascade) không chạy mã Python → không dọn Drive; chỉ `delete_activity` và `delete_course` (service) dọn Drive best-effort, và cả hai từ chối khi tích hợp không sẵn sàng (xem Quy tắc bất biến). `content` không lưu id thư mục khoá học: `delete_course` tìm thư mục root/org/course theo tên bằng `find_folder` (không tạo) và bỏ qua nếu còn Activity ở khoá học khác tham chiếu thư mục activity bên trong.

### Quy tắc bất biến

- Nơi lưu trữ không đổi sau khi tạo: Activity `SUBTYPE_VIDEO_GDRIVE` chỉ thay được bằng file Drive khác; `SUBTYPE_VIDEO_HOSTED`/`YOUTUBE` không chuyển sang Drive. Vì `ActivityUpdate` (điểm `PUT /activities/{uuid}`) nhận cả `activity_sub_type` lẫn `content`, `update_activity` phải từ chối 409 khi body đổi subtype của Activity Drive, ghi đè `content.storage`/`gdrive_file_id`/`gdrive_folder_id`/`mime_type`/`size`/`original_filename`, hoặc đặt subtype Drive cho Activity khác.
- Tạo Activity chỉ sau khi Drive đã có file, đã chia sẻ và đã xác nhận.
- `clone_course` sao chép `content` nguyên vẹn → hai Activity có thể trỏ cùng `gdrive_file_id`; mọi thao tác xoá phải kiểm tra không còn Activity khác tham chiếu.
- Mọi thao tác ghi lên Activity `SUBTYPE_VIDEO_GDRIVE` (tạo, cập nhật kể cả đổi tên/xuất bản, thay file, xoá; xoá khoá học chứa nó) yêu cầu `GDriveStatus.ready == true`; không sẵn sàng → từ chối 409, không thay đổi gì (FR-004). Phát lại không phụ thuộc readiness.

### Validate khi nhận file

| Quy tắc | Giá trị | Nguồn |
|---------|---------|-------|
| Content-Type | `video/mp4`, `video/webm` | `FILE_TYPES['video']['mime_types']` |
| Extension | `.mp4`, `.webm` | `FILE_TYPES['video']['extensions']` |
| Kích thước | ≤ 5 GiB, đo qua seek/tell (không đọc toàn bộ) | `FILE_TYPES['video']['max_size']` |
| Magic bytes | 64 KiB đầu, `validate_video_content` | `file_validation.py` |
| Tên lưu trên Drive | `video.<ext>` (ext từ MIME đã validate) | `get_safe_filename` |

## 2. GDriveConfig (cấu hình cấp instance, `config/config.py`)

| Trường | Kiểu | Mặc định | Env override | YAML |
|--------|------|----------|--------------|------|
| `enabled` | bool | `false` | `LEARNHOUSE_GDRIVE_ENABLED` (qua `_env_bool`) | `gdrive_config.enabled` |
| `credentials_path` | str \| None | `null` | `LEARNHOUSE_GDRIVE_CREDENTIALS_PATH` | `gdrive_config.credentials_path` |
| `token_path` | str \| None | `null` | `LEARNHOUSE_GDRIVE_TOKEN_PATH` | `gdrive_config.token_path` |
| `root_folder_name` | str | `"LearnHouse"` | `LEARNHOUSE_GDRIVE_ROOT_FOLDER_NAME` | `gdrive_config.root_folder_name` |

Không thuộc tổ chức nào; áp dụng toàn instance. Env luôn thắng YAML (đúng convention của repo).

## 3. File credential và token (trên đĩa, ngoài git)

- **Credential** (`credentials_path`): JSON OAuth client, khoá gốc `web` hoặc `installed`, cần `client_id`, `client_secret`, `token_uri`. Chỉ đọc.
- **Token** (`token_path`): JSON theo `Credentials.to_json()`: `token`, `refresh_token`, `token_uri`, `client_id`, `client_secret`, `scopes`, `expiry` (ISO 8601 UTC, hậu tố `Z`). Đọc/ghi; ghi atomic, mode `0600`. Được `gdrive-authorize` tạo và được làm mới tự động khi hết hạn.

## 4. GDriveStatus (trạng thái sẵn sàng, trong tiến trình)

```python
@dataclass
class GDriveStatus:
    enabled: bool
    ready: bool
    reason: str | None   # xem bảng
```

| `reason` | `enabled` | `ready` | Ý nghĩa |
|----------|-----------|---------|---------|
| `disabled` | false | false | cờ tắt |
| `credentials_missing` | true | false | không đọc được file credential |
| `credentials_invalid` | true | false | JSON thiếu khoá bắt buộc |
| `token_missing` | true | false | chưa chạy `gdrive-authorize` hoặc thiếu `refresh_token` |
| `needs_reauthorization` | true | false | Google trả `invalid_grant` khi làm mới |
| `network_error` | true | false | không liên lạc được Google khi làm mới (tạm thời) |
| `null` | true | true | sẵn sàng |

### Chuyển trạng thái

```
disabled ──(bật cờ)──▶ credentials_missing ──(có file)──▶ token_missing
   ──(gdrive-authorize)──▶ ready
ready ──(invalid_grant / thu hồi)──▶ needs_reauthorization ──(gdrive-authorize)──▶ ready
ready ──(Google 5xx / mất mạng)──▶ network_error ──(TTL hết, thử lại OK)──▶ ready
```

### Cache nội bộ `_State`

| Trường | Ghi chú |
|--------|---------|
| `checked_at`, `network_ok` | kết quả kiểm tra mạng, TTL 300 s |
| `token_mtime_ns`, `token` | token đã parse; đổi mtime → nạp lại ngay |
| `root_folder_id` | ID thư mục gốc; bỏ khi Drive trả 404 |
| `lock` | `asyncio.Lock` để nhiều upload dùng chung một lần làm mới |
| `logged_reason` | chống log lặp cùng một lý do |

`reset_for_tests()` tạo lại `_State` mới; `invalidate()` xoá kết quả kiểm tra mạng.

## 5. DriveUploadResult (giá trị trả về nội bộ)

```python
@dataclass
class DriveUploadResult:
    file_id: str
    folder_id: str
    stored_filename: str   # "video.mp4" | "video.webm"
    mime_type: str
    size: int
```

## 6. Bố cục trên Google Drive

```
<root_folder_name>/                 # do app tạo, ID cache trong tiến trình
└── <org_uuid>/
    └── <course_uuid>/
        └── <activity_uuid>/
            └── video.<ext>         # anyone/reader, copyRequiresWriterPermission=true
```

Thư mục root/org/course là idempotent (tìm theo tên + parent, tạo nếu thiếu); thư mục activity tạo mới cho mỗi lần tạo Activity và là đơn vị dọn dẹp khi lỗi.
