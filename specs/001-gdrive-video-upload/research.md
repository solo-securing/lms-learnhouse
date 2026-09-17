# Research: Upload video Activity lên Google Drive

**Feature**: `001-gdrive-video-upload` | **Date**: 2026-09-16

Mỗi mục ghi Decision / Rationale / Alternatives. Các quyết định (a), (b), (h), (i) đã được chốt trực tiếp với người dùng trong phiên lập kế hoạch ngày 2026-09-16.

## (a) Loại OAuth client và redirect URI

- **Decision**: Giữ nguyên OAuth client loại `web` hiện có (`gg_drive_credentials/credential_vietpd.ccs2.json`, khoá gốc `"web"`, không có `redirect_uris`). Người vận hành đăng ký `http://localhost:8765/` vào Authorized redirect URIs trên Google Cloud Console. Lệnh uỷ quyền dùng cổng cố định 8765 (`--port` để đổi, `--no-browser` để in URL).
- **Rationale**: Không phải tải lại credential; `InstalledAppFlow` của `google-auth-oauthlib` chấp nhận cả khoá `web` lẫn `installed`, chỉ cần redirect URI khớp. Cổng cố định để URI đăng ký ổn định.
- **Alternatives**: Tạo client "Desktop app" (chấp nhận mọi cổng localhost) — cần tạo client mới; luồng OOB (`urn:ietf:wg:oauth:2.0:oob`) — Google đã gỡ bỏ; service account — tài khoản dịch vụ không có quota lưu trữ trên Drive cá nhân, không dùng được.

## (b) Scope Drive, thư mục gốc và trạng thái consent screen

- **Decision**: Scope duy nhất `https://www.googleapis.com/auth/drive.file`. Thư mục gốc cấu hình theo TÊN (mặc định `LearnHouse`), app tự tạo khi thiếu và cache ID trong tiến trình (bỏ cache khi gặp 404). Người vận hành chuyển OAuth consent screen sang "In production".
- **Rationale**: `drive.file` là scope không nhạy cảm, publish không cần Google xác minh; ở trạng thái "Testing" Google làm refresh token hết hạn sau 7 ngày, buộc uỷ quyền lại hàng tuần. Với `drive.file`, app chỉ thấy file/thư mục do chính nó tạo, nên thư mục gốc phải do app tạo (không hỗ trợ trỏ tới thư mục có sẵn bằng ID).
- **Alternatives**: Scope `drive` toàn quyền + thư mục gốc theo ID — scope restricted, hiện cảnh báo "ứng dụng chưa xác minh" hoặc phải giữ Testing với token 7 ngày.

## (c) Thư viện gọi Drive API

- **Decision**: Gọi Drive REST v3 trực tiếp bằng `httpx.AsyncClient` (đã là dependency). Refresh token tự viết (POST `token_uri`, `grant_type=refresh_token`). Chỉ thêm 1 dependency mới: `google-auth-oauthlib` cho luồng đồng ý một lần (`google-auth` đã có transitive qua `google-genai`, `apps/api/uv.lock:467`).
- **Rationale**: httpx async khớp với handler FastAPI, stream chunk không chặn event loop; `httpx.MockTransport` đã có tiền lệ trong repo (`src/tests/services/test_link_preview_service.py:30-55`) nên test đạt coverage ≥ 90% dễ dàng. `google-api-python-client` là sync (httplib2), cần `to_thread`, khó mock, kéo thêm `httplib2`, `uritemplate`, `google-auth-httplib2`.
- **Alternatives**: `google-api-python-client` + `MediaIoBaseUpload` — bị loại vì lý do trên; tự viết luồng consent (loopback server + PKCE) — ~80 dòng mã tự bảo trì, không đáng so với 1 dependency.

## (d) Giao thức upload resumable

- **Decision**: Resumable upload: `POST https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable` với `X-Upload-Content-Type`, `X-Upload-Content-Length`, body `{name, parents, mimeType}` → `Location`; `PUT` từng chunk 8 MiB (bội số 256 KiB) với `Content-Range: bytes a-b/size`; `308` → đọc `Range` và seek lại; `5xx`/`httpx.TransportError`/`429` → backoff 1–16 s + jitter, rồi truy vấn trạng thái `Content-Range: bytes */size` để tiếp tục; tối đa 5 lần thất bại liên tiếp → lỗi tạm thời; `404/410` → session mất, lỗi tạm thời. Timeout httpx `connect=30, read=120, write=300, pool=30` (theo từng thao tác socket, không có trần tổng).
- **Ghi chú spec (FR-011, Edge Cases)**: `ensure_folder` tìm theo `name`+`parents`+`mimeType=folder`+`trashed=false` trước khi tạo; nếu trả về nhiều kết quả (đua tranh) chọn `createdTime` sớm nhất. 403 `rateLimitExceeded`/`userRateLimitExceeded`/`dailyLimitExceeded` và 429 → `GDriveTransientError` sau retry, log `reason` gốc.
- **Rationale**: Multipart upload giới hạn 5 MB; resumable là cách duy nhất cho 5 GiB, cho phép tiếp tục sau lỗi mạng và không giữ toàn bộ file trong RAM.
- **Alternatives**: Multipart/simple upload — giới hạn kích thước; upload trực tiếp từ trình duyệt — lộ token cho người dùng cuối (spec loại trừ).

## (e) Biểu diễn Activity Drive

- **Decision**: Thêm giá trị enum `SUBTYPE_VIDEO_GDRIVE` vào `ActivitySubTypeEnum` (`apps/api/src/db/courses/activities.py`) kèm migration Alembic `op.sync_enum_values` (enum Postgres thật `activitysubtypeenum`, head hiện tại `b1c2d3e4f5a6`, mẫu `r5s6t7u8v9w0_add_resource_activity_subtype.py`). `content` lưu `gdrive_file_id`, `gdrive_folder_id`, `original_filename`, `mime_type`, `size`, `storage: "gdrive"`; không lưu liên kết xem.
- **Rationale**: Frontend và pipeline HLS/captions đều rẽ nhánh theo `activity_sub_type`; subtype riêng làm FR-016 tự thoả (`hls_jobs._resolve_source` và `configure_captions` đã bỏ qua non-HOSTED). Không lưu URL để giảm rò rỉ liên kết (FR-009b); URL nhúng suy ra từ file id ở client.
- **Alternatives**: Dùng lại `SUBTYPE_VIDEO_YOUTUBE` với `content.type = "gdrive"` (như Vimeo) — tránh migration nhưng làm mọi nhánh YouTube phải kiểm tra thêm `content.type`, dễ lộ `content.uri` ở `ActivityPreview.tsx`.

## (f) Cách frontend biết tích hợp sẵn sàng

- **Decision**: Endpoint mới có xác thực `GET /api/v1/integrations/gdrive/status` → `{enabled, ready, reason}`; hộp thoại tạo video gọi khi mount; lỗi mạng → coi như không sẵn sàng (fail closed).
- **Rationale**: `/instance/info` là endpoint công khai, được proxy gọi mỗi request và cache 600 s; kiểm tra token có thể cần gọi Google nên không phù hợp. Chỉ giảng viên mở hộp thoại mới trả chi phí.
- **Alternatives**: Thêm cờ vào `_live_fields()` của `/instance/info` + cookie `LH_*` — công khai, tốn kém trên đường nóng; đưa vào `resolved_features` theo tổ chức — tích hợp là cấp instance, không phải cấp org.

## (g) Đồng bộ trong request và trạng thái tiến trình

- **Decision**: Upload đồng bộ trong cùng request (FR-011): nhận file → validate → đẩy lên Drive → chia sẻ → xác nhận → tạo Activity. Frontend giữ luồng background task hiện có: tiến trình XHR = trình duyệt → máy chủ; khi `xhr.upload.onload` bắn, chuyển task sang `processing` với phụ đề "Đang đẩy lên Google Drive…". Nâng `proxy_read_timeout`/`proxy_send_timeout` của `/api/v1` từ 3600 s lên 86400 s trong `docker/nginx.conf` và `apps/cli/src/templates/nginx.ts`. Commit/rollback session DB trước khi gọi Drive để không giữ kết nối pool.
- **Rationale**: Spec chọn không có trạng thái trung gian; nginx hiện buffer toàn bộ body rồi chờ byte phản hồi đầu tiên, nên với file lớn thời gian chờ có thể vượt 60 phút. Triển khai sau Cloudflare (giới hạn ~100 s) không tương thích và được ghi là không hỗ trợ.
- **Alternatives**: Job nền + trạng thái "đang tải" trên Activity — bị spec loại; upload trực tiếp từ trình duyệt — lộ token.

## (h) Đặt tên thư mục trên Drive

- **Decision**: `<gốc>/<org_uuid>/<course_uuid>/<activity_uuid>/video.<ext>` (chỉ UUID).
- **Rationale**: Ổn định tuyệt đối, không trùng, khớp cấu trúc lưu trên máy chủ hiện tại (`content/orgs/<org_uuid>/courses/<course_uuid>/activities/<activity_uuid>/video/`). ID thư mục vẫn được lưu trong `content` nên đổi tên trên Drive không ảnh hưởng.
- **Alternatives**: Tên dễ đọc + đuôi UUID ngắn; chỉ tên dễ đọc (trùng tên → dùng chung thư mục).

## (i) Thiết lập phát (autoplay/muted/start/end)

- **Decision**: Ẩn toàn bộ khối Video Settings với Activity Drive; `details = {}`.
- **Rationale**: Trình phát nhúng `https://drive.google.com/file/d/<id>/preview` không nhận tham số tự phát, tắt tiếng hay thời điểm; hiển thị ô không có tác dụng sẽ gây hiểu nhầm.
- **Alternatives**: Giữ autoplay/muted và lưu vào `details` để dùng sau — bị loại.

## (j) Phát hiện trình duyệt chặn nội dung nhúng

- **Decision**: Không phát hiện; luôn hiển thị một dòng gợi ý nhỏ dưới trình phát Drive: "Nếu video không hiển thị, hãy cho phép nội dung nhúng của bên thứ ba trong trình duyệt". Không bao giờ hiển thị liên kết Drive.
- **Rationale**: Từ trang cha không thể biết iframe cross-origin bị chặn (`onload` vẫn bắn, nội dung opaque, `fetch` tới Drive bị CORS). Dòng gợi ý cũng bao trùm trường hợp quyền chia sẻ bị gỡ thủ công (Drive tự hiển thị trang yêu cầu quyền trong iframe).
- **Alternatives**: Đo kích thước/`postMessage` — không khả thi với Drive; hiển thị link dự phòng — vi phạm FR-009b.

## (k) Lưu token và cache trạng thái sẵn sàng

- **Decision**: Token JSON theo định dạng `google.oauth2.credentials.Credentials.to_json()`; ghi atomic (`NamedTemporaryFile` cùng thư mục → `fchmod 0600` → `fsync` → `os.replace`). Trạng thái sẵn sàng giữ trong `_State` cấp module: kiểm tra local (file tồn tại, có `refresh_token`) mỗi lần; kiểm tra mạng (làm mới access token khi hết hạn, skew 5 phút) cache 300 s và keyed theo `mtime_ns` của file token để token mới từ `gdrive-authorize` có hiệu lực ngay. `invalid_grant` → trạng thái `needs_reauthorization`, log lỗi actionable 1 lần mỗi lần chuyển trạng thái. 401 trong lúc upload → `invalidate()`.
- **Rationale**: Tránh gọi Google ở mỗi lần mở hộp thoại; ghi atomic tránh file token hỏng khi nhiều worker làm mới đồng thời (last writer wins, Google giữ refresh token hợp lệ).
- **Alternatives**: Lưu token mã hoá trong Postgres (`src/services/webhooks/crypto.py`) — spec chọn đường dẫn file; kiểm tra mạng ở mỗi request — tốn kém.

## (l) Dọn dẹp file Drive theo vòng đời

- **Decision**: Mọi thao tác ghi lên Activity Drive (tạo, cập nhật kể cả đổi tên, thay file, xoá, xoá khoá học chứa nó) đi qua `require_ready()` trước; không sẵn sàng → `GDriveNotEnabledError`/`GDriveNeedsAuthorizationError` (409), không thay đổi gì (FR-004, clarify 2026-09-16). Khi sẵn sàng: `delete_activity` xoá thư mục activity trên Drive (best-effort, log khi lỗi); `delete_course` xoá thư mục khoá học nếu có ≥ 1 activity Drive — tìm root/org/course theo tên bằng `find_folder` (không tạo mới, vì `content` không lưu id thư mục khoá học) và bỏ qua + log WARNING nếu Activity ở khoá học khác (bản clone) còn tham chiếu thư mục activity bên trong; `update_activity` (PUT chung) từ chối 409 khi đổi `activity_sub_type`/trường Drive trong `content` của Activity Drive hoặc gán subtype Drive cho Activity khác; thay video xoá file cũ sau khi commit; file mới luôn đặt vào thư mục resolve theo path UUID của chính activity đang sửa (tạo mới nếu bản clone còn dùng chung folder). Ngân sách best-effort: 1 lượt + retry tạm thời, tổng ≤ 60 s, thất bại → log WARNING kèm id (FR-014). Trước mọi lệnh xoá, kiểm tra không còn Activity nào khác tham chiếu cùng `gdrive_file_id`/`gdrive_folder_id` (vì `clone_course` sao chép nguyên `content`). Xoá tổ chức chỉ cascade ở DB, không dọn Drive (ghi nhận, ngoài phạm vi).
- **Rationale**: `files.delete` xoá vĩnh viễn (không vào thùng rác), nên tránh xoá nhầm file dùng chung; xoá thư mục kéo theo con nên là bước dọn chung khi lỗi.
- **Alternatives**: Sao chép server-side (`files.copy`) khi clone khoá học — follow-up, ghi trong PR.

## (m) Validate file không đọc toàn bộ vào RAM

- **Decision**: Tách `validate_upload_stream(file, allowed_types) -> (mime, size)` từ phần đầu của `validate_upload` (`apps/api/src/security/file_validation.py:333-395`): kiểm tra extension/SVG, kích thước qua `_stream_length`, 64 KiB đầu qua `validate_video_content`, rồi rewind. `validate_upload` gọi hàm này rồi `read()` như cũ.
- **Rationale**: `validate_upload` trả về toàn bộ bytes (tới 5 GiB) — chấp nhận được với luồng ghi đĩa cũ nhưng không với luồng stream lên Drive. Refactor thuần giữ test cũ xanh.
- **Alternatives**: Ghi file tạm rồi upload — tốn đĩa gấp đôi và thêm bước; Starlette đã spool >1 MiB ra đĩa nên đọc chunk từ `UploadFile` là đủ.

## (n) Xử lý lỗi và mã HTTP

- **Decision**: Hệ phân cấp `GDriveError` → `GDriveNotEnabledError` (409), `GDriveNeedsAuthorizationError` (409), `GDriveConfigError` (409), `GDriveQuotaExceededError` (507), `GDrivePermissionError` (502), `GDriveTransientError` (502); `to_http_exception()` với `detail` dạng `"Video : ..."` theo convention hiện có. Router `POST /video` trả 422 khi `storage` ∉ {`server`, `gdrive`}.
- **Rationale**: Frontend hiển thị `detail` cho mọi mã ≠ 2xx trừ 413 (`services/courses/activities.ts:68-77`), nên thông điệp phải hành động được. 409 = cần người vận hành xử lý, 502 = upstream lỗi, 507 = hết dung lượng.
- **Alternatives**: 503 cho "cần uỷ quyền lại" — proxy có thể diễn giải khác; giữ 409 cho nhất quán với `"Video : Wrong video format"`.
