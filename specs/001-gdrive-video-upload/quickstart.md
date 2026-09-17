# Quickstart: kiểm chứng tính năng upload video Activity lên Google Drive

Hướng dẫn chạy/kiểm chứng end-to-end. Chi tiết hợp đồng xem `contracts/`, mô hình dữ liệu xem `data-model.md`.

## 1. Tiền đề (làm một lần trên Google Cloud Console)

1. OAuth client loại **Web application** (file JSON có khoá `web`). Thêm `http://localhost:8765/` vào **Authorized redirect URIs**.
2. OAuth consent screen: thêm scope `https://www.googleapis.com/auth/drive.file`, chuyển **Publishing status → In production** (tránh refresh token hết hạn sau 7 ngày ở trạng thái Testing).
3. Bật **Google Drive API** cho project.
4. KHÔNG tự tạo thư mục "LearnHouse" trên Drive; app sẽ tạo (scope `drive.file` không thấy thư mục tạo tay).

## 2. Cấu hình máy chủ

```bash
# apps/api/.env (hoặc env của container) — không commit các file này
LEARNHOUSE_GDRIVE_ENABLED=true
LEARNHOUSE_GDRIVE_CREDENTIALS_PATH=/Projects/LMS/learnhouse/gg_drive_credentials/credential_vietpd.ccs2.json
LEARNHOUSE_GDRIVE_TOKEN_PATH=/Projects/LMS/learnhouse/gg_drive_credentials/gdrive_token.json
LEARNHOUSE_GDRIVE_ROOT_FOLDER_NAME=LearnHouse
```

Kiểm tra `gg_drive_credentials/` đã nằm trong `.gitignore` và `.dockerignore`:

```bash
git check-ignore -v gg_drive_credentials/credential_vietpd.ccs2.json   # phải in ra dòng khớp
```

## 3. Cài đặt, migration, uỷ quyền

```bash
cd apps/api
uv sync                       # có google-auth-oauthlib sau khi lockfile cập nhật
uv run alembic upgrade head   # thêm SUBTYPE_VIDEO_GDRIVE vào enum
uv run python cli.py gdrive-authorize          # mở trình duyệt, đăng nhập, đồng ý
uv run python cli.py gdrive-status             # mong đợi: ready: true, account: <email>, exit 0
```

Host headless: `uv run python cli.py gdrive-authorize --no-browser` + `ssh -L 8765:localhost:8765 <host>` từ máy có trình duyệt.

## 4. Kịch bản kiểm chứng

### US2 — chỉ hiển thị khi sẵn sàng

| Bước | Mong đợi |
|------|----------|
| `curl -H "Authorization: Bearer <token>" $API/api/v1/integrations/gdrive/status` | `{"enabled":true,"ready":true,"reason":null}` |
| Đặt `LEARNHOUSE_GDRIVE_ENABLED=false`, khởi động lại API, gọi lại | `{"enabled":false,"ready":false,"reason":"disabled"}`; hộp thoại tạo video → tab "Tải file lên" KHÔNG có lựa chọn "Nơi lưu trữ" |
| Với cờ tắt: `curl -F name=x -F chapter_id=<id> -F storage=gdrive -F video_file=@small.mp4 $API/api/v1/activities/video` | HTTP 409, detail `Video : Google Drive storage is not enabled on this instance`; không có Activity mới |
| Với cờ tắt, Activity Drive đã có: `curl -X PUT -F name=renamed $API/api/v1/activities/video/<uuid>` và `curl -X PUT ... $API/api/v1/activities/<uuid>` (đổi tên/xuất bản) | HTTP 409 cùng thông điệp; tên và trạng thái không đổi |
| Với cờ tắt, Activity Drive đã có: `curl -X DELETE $API/api/v1/activities/<uuid>` | HTTP 409; Activity vẫn còn trong chương; học viên vẫn phát được video |
| Cờ bật nhưng xoá file token, khởi động API | log cảnh báo `token_missing`; hộp thoại vẫn ẩn lựa chọn |
| `-F storage=bogus` | HTTP 422 |

### US1 — tạo Activity Drive

1. Bật cờ, đã uỷ quyền. Vào Dashboard → khoá học → chương → "Thêm Activity" → Video → tab "Tải file lên".
2. Mong đợi: radio "Nơi lưu trữ" với "Google Drive" được chọn sẵn, cảnh báo quyền xem hiển thị; KHÔNG có card Video Settings, KHÔNG có AI captions.
3. Chọn `small.mp4` (vài MB), đặt tên, bấm tạo. Mong đợi: panel tác vụ nền chạy tiến trình → "Đang đẩy lên Google Drive…" → "Uploaded"; Activity xuất hiện trong chương.
4. Trên Drive: `LearnHouse/<org_uuid>/<course_uuid>/<activity_uuid>/video.mp4`; Chia sẻ = "Bất kỳ ai có liên kết – Người xem"; tuỳ chọn "Người xem và người nhận xét có thể tải xuống, in, sao chép" đã TẮT.
5. Mở Activity với tài khoản học viên: iframe Drive phát được; nút mở ở tab mới (góc trên phải) không bấm được; play/pause/tua/âm lượng/toàn màn hình hoạt động; dòng gợi ý nhỏ dưới trình phát; không có link Drive ở bất kỳ đâu (kiểm tra cả trang nhúng `/embed/...` và Boards).
6. Xem `GET /api/v1/activities/<uuid>`: `activity_sub_type = SUBTYPE_VIDEO_GDRIVE`, `content` đúng `contracts/drive-content.md`, `details = {}`, không có `uri`.

### Lỗi giữa chừng (SC-005)

| Cách gây lỗi | Mong đợi |
|--------------|----------|
| Ngắt mạng ra Google khi đang đẩy (vd. chặn `googleapis.com` bằng firewall) | sau retry, HTTP 502 `Google Drive is unreachable`; không Activity; thư mục activity trên Drive bị xoá (hoặc không tồn tại) |
| Sửa file token: `refresh_token` sai **và** `expiry` về quá khứ (access token còn hạn thì chưa cần làm mới nên chưa phát hiện) → gọi tạo | HTTP 409 `needs re-authorization` ngay ở lần gọi kế tiếp (không cần khởi động lại); log hướng dẫn chạy `gdrive-authorize`; `gdrive-status` → `needs_reauthorization`, exit 1 |
| File `.mkv` | 409 `Wrong video format` trước khi gọi Drive |

### US4 — thay và xoá

1. Mở hộp thoại sửa Activity Drive: hiển thị "Google Drive Video", tên file gốc, "Nơi lưu trữ: Google Drive", input thay file; KHÔNG có Video Settings/captions/link.
2. Thay bằng `other.mp4`: Drive có file mới, file cũ biến mất; `content.gdrive_file_id` đổi; subtype không đổi.
3. Xoá Activity (tích hợp sẵn sàng): thư mục `<activity_uuid>` trên Drive biến mất; Activity mất khỏi chương. Nếu Drive không phản hồi giữa chừng, Activity vẫn bị xoá và log ghi id để dọn tay. (Khi tích hợp không sẵn sàng, xoá bị từ chối 409 — xem bảng US2.)
4. Xoá khoá học **đang còn** Activity Drive: thư mục `<course_uuid>` trên Drive biến mất (best-effort). Nếu đã xoá hết Activity Drive trước đó, thư mục rỗng `<course_uuid>` được giữ lại (dọn tay).

## 5. Kiểm tra tự động

```bash
# API
cd apps/api
uv run ruff check .
uv run pytest src/tests/ -k "gdrive or video_activity or file_validation or activities_video" --cov=src --cov-report=term-missing
# toàn bộ suite trước khi mở PR
uv run pytest src/tests/ --cov=src --cov-fail-under=25

# Web
cd apps/web
bunx tsc --noEmit
bun test tests

# Lockfile
cd ../.. && scripts/lockfiles.sh --check
```

Mong đợi: tất cả xanh; patch coverage của các module mới ≥ 90% (không thêm mục nào vào `[tool.coverage.run] omit`).

## 6. Giới hạn đã biết

- Với file lớn, Drive có thể hiển thị "Đang xử lý video" vài phút sau khi upload xong; Activity vẫn được tạo.
- Không hỗ trợ triển khai sau Cloudflare proxy (giới hạn ~100 s time-to-first-byte) vì upload đồng bộ.
- Đĩa tạm cần ~2× kích thước file (nginx body + Starlette spool).
- Khi iframe ở chế độ toàn màn hình, overlay của trang không áp dụng; `sandbox` không có `allow-popups` vẫn vô hiệu nút mở tab mới.
