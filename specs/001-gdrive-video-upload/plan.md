# Implementation Plan: Upload video Activity lên Google Drive

**Branch**: `001-gdrive-video-upload` | **Date**: 2026-09-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-gdrive-video-upload/spec.md`

## Summary

Giảng viên có thể lưu video của Activity kiểu video lên Google Drive cá nhân của người vận hành thay vì máy chủ. Máy chủ nhận file, đẩy lên Drive bằng resumable upload (httpx, chunk 8 MiB, không giữ file trong RAM), đặt quyền "ai có liên kết đều xem" + `copyRequiresWriterPermission`, xác nhận, rồi mới tạo Activity với subtype mới `SUBTYPE_VIDEO_GDRIVE`. Học viên xem qua iframe `drive.google.com/file/d/<id>/preview` có overlay chặn dải trên và không có `allow-popups`. Tuỳ chọn chỉ hiện khi endpoint `GET /api/v1/integrations/gdrive/status` báo sẵn sàng (cờ bật + credential + token hợp lệ). Người vận hành uỷ quyền một lần bằng `cli.py gdrive-authorize` (OAuth client web, redirect `http://localhost:8765/`, scope `drive.file`, consent screen In production).

Quyết định thiết kế chi tiết: [research.md](research.md). Mô hình dữ liệu: [data-model.md](data-model.md). Hợp đồng: [contracts/](contracts/). Kiểm chứng: [quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.14.7 (uv) cho `apps/api`; TypeScript 6 strict / Next.js 16 / React 19 / bun 1.4.0 cho `apps/web`

**Primary Dependencies**: FastAPI 0.141, SQLModel, Alembic + `alembic-postgresql-enum`, `httpx` 0.28 (đã có; gọi Drive REST v3 async), Typer 0.27 (CLI). Phụ thuộc mới duy nhất: `google-auth-oauthlib` (pin `==`); `google-auth` đã là transitive qua `google-genai`. Không dùng `google-api-python-client`.

**Storage**: PostgreSQL (Activity.content JSON; enum Postgres `activitysubtypeenum` thêm giá trị qua migration); file video trên Google Drive; token JSON trên đĩa máy chủ (ghi atomic, mode 0600, ngoài git)

**Testing**: pytest 9 + pytest-asyncio (`asyncio_mode=auto`) trong `apps/api/src/tests/`, mock Drive bằng `httpx.MockTransport`; `bun test tests` (pure-function `.test.mjs`) trong `apps/web/tests/`

**Target Platform**: Linux server (Docker all-in-one nginx+pm2, compose sinh bởi CLI, hoặc nix dev shell `lh dev` trên WSL2)

**Project Type**: web application (backend `apps/api` + frontend `apps/web`)

**Performance Goals**: upload tối đa 5 GiB đi qua máy chủ rồi đẩy lên Drive trong cùng request (FR-011); stream chunk 8 MiB từ `SpooledTemporaryFile`; không giữ kết nối DB trong lúc đẩy lên Drive; kiểm tra trạng thái không gọi Google quá 1 lần / 5 phút

**Constraints**: nginx `client_max_body_size 6G` đủ, nhưng `/api/v1` chỉ có `proxy_read_timeout 3600s` (`docker/nginx.conf:56-57`, `apps/cli/src/templates/nginx.ts`) → nâng lên 86400s cho route video; không hỗ trợ sau Cloudflare (~100 s TTFB); không lưu credential/token trong git/Docker image (FR-015); không widen coverage `omit`; enum mới cần migration Alembic; `clone_course` sao chép `content` nguyên vẹn → kiểm tra tham chiếu trước khi xoá file Drive

**Scale/Scope**: 1 tài khoản Drive cho toàn instance; 1 endpoint mới, 2 endpoint mở rộng, 2 lệnh CLI, 1 migration, 1 package backend mới (6 module), ~8 file web sửa, 2 file web test mới, 1 dependency mới

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Nguyên tắc | Trước thiết kế | Sau thiết kế (Phase 1) |
|-----------|----------------|------------------------|
| I. Quality Gates Are Blocking | Không thêm `\|\| true`. `apps/web/tests` chưa có CI job (nợ có sẵn). | PASS — PR này thêm job `bun test tests` vào `.github/workflows/web-lint.yaml` vì là PR đầu tiên thêm web test cho tính năng này. |
| II. Tests Accompany Every Behavior Change | Có kế hoạch test API + web trong cùng PR. | PASS — danh sách file test ở mục Project Structure; mỗi thay đổi hành vi (config, client, readiness, service, router, CLI, migration, helper web) đều có test. |
| III. Coverage Floor of 90% | Module mới không đưa vào `omit`. | PASS — logic CLI đặt trong `src/services/integrations/gdrive/authorize.py` để được `--cov=src` đo (`cli.py` nằm ngoài `src`); client mock đủ nhánh lỗi. |
| IV. Static Analysis Is Mandatory | ruff 0.15.9; `tsc --noEmit` strict. | PASS CÓ NGOẠI LỆ — ruff và `tsc --noEmit` chạy trong CI; không dùng `any` mới ngoài chỗ file đã `any`; helper web có kiểu rõ. Type checker Python và kiểm tra format tự động trong CI là nợ toàn dự án (constitution ghi nhận "to be closed by follow-up work"), KHÔNG được thêm trong PR này để giữ phạm vi; PR MUST nêu ngoại lệ này rõ ràng kèm issue theo dõi theo Principle I (task T065). |
| V. Maintainability by Construction | 1 dependency mới có lý do; migration Alembic; `scripts/lockfiles.sh`. | PASS — không tạo abstraction storage-backend chung, chỉ 1 package `gdrive`; abstraction duy nhất là `DriveClient` (bọc httpx + dịch lỗi) được biện minh bởi test và retry. |

Không có vi phạm → Complexity Tracking để trống.

## Project Structure

### Documentation (this feature)

```text
specs/001-gdrive-video-upload/
├── spec.md              # đã cập nhật theo quyết định lập kế hoạch
├── plan.md              # file này
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/           # Phase 1
│   ├── gdrive-status.openapi.yaml
│   ├── activities-video.openapi.yaml
│   ├── cli.md
│   └── drive-content.md
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks) — chưa tạo
```

### Source Code (repository root)

```text
apps/api/
├── cli.py                                   # + gdrive-authorize, gdrive-status (vỏ Typer)
├── pyproject.toml / uv.lock                 # + google-auth-oauthlib
├── config/
│   ├── config.py                            # + GDriveConfig, LearnHouseConfig.gdrive_config
│   └── config.yaml                          # + gdrive_config: block (ghi chú FR-015)
├── migrations/versions/
│   └── g1d2r3i4v5e6_add_gdrive_video_subtype.py   # sync_enum_values, down_revision b1c2d3e4f5a6
└── src/
    ├── db/courses/activities.py             # + SUBTYPE_VIDEO_GDRIVE
    ├── security/file_validation.py          # + validate_upload_stream (tách từ validate_upload)
    ├── core/events/events.py                # + log_startup_status() lúc khởi động
    ├── router.py                            # + mount /integrations/gdrive
    ├── routers/
    │   ├── integrations/gdrive.py           # GET /status (mới)
    │   └── courses/activities/activities.py # POST /video + storage Form
    ├── services/
    │   ├── integrations/gdrive/             # package mới
    │   │   ├── __init__.py
    │   │   ├── errors.py                    # GDriveError hierarchy + to_http_exception
    │   │   ├── credentials.py               # load secrets/token, save atomic 0600, refresh
    │   │   ├── client.py                    # DriveClient (httpx): ensure_folder/find_folder, resumable, perms, delete, about
    │   │   ├── readiness.py                 # _State, get_status, ensure_ready, get_access_token
    │   │   ├── service.py                   # upload_activity_video, *_best_effort deletes
    │   │   └── authorize.py                 # run_authorize (google-auth-oauthlib, lazy import)
    │   └── courses/
    │       ├── activities/video.py          # create/update rẽ nhánh gdrive
    │       ├── activities/activities.py     # delete_activity → dọn Drive
    │       └── courses.py                   # delete_course → dọn Drive
    └── tests/
        ├── conftest.py                      # pin LEARNHOUSE_GDRIVE_ENABLED=false
        ├── core/test_config_gdrive.py
        ├── services/test_gdrive_credentials.py
        ├── services/test_gdrive_client.py
        ├── services/test_gdrive_readiness.py
        ├── services/test_gdrive_service.py
        ├── services/test_gdrive_authorize.py
        ├── services/test_video_activity_service.py      # mở rộng
        ├── services/test_activities_service.py          # mở rộng
        ├── services/test_courses_service.py             # mở rộng
        ├── security/test_file_validation*.py            # mở rộng
        ├── routers/test_activities_video_router.py
        ├── routers/test_gdrive_status_router.py
        └── test_cli_gdrive.py

apps/web/
├── services/
│   ├── integrations/gdrive.ts               # getGDriveStatus (mới)
│   └── courses/activities.ts                # buildVideoFormFields, onUploaded, updateGDriveVideoActivity
├── components/
│   ├── Objects/Activities/Video/videoSource.ts        # isGDriveActivity, resolveDrivePreviewUrl, defaultVideoStorage
│   ├── Objects/Activities/Video/Video.tsx             # nhánh SUBTYPE_VIDEO_GDRIVE: iframe + overlay + hint
│   ├── Objects/Activities/ActivityPreview/ActivityPreview.tsx   # nhánh gdrive, không link
│   ├── Objects/Modals/Activities/Create/NewActivityModal/VideoActivityModal.tsx  # radio Nơi lưu trữ
│   ├── Objects/Modals/Activities/Edit/EditVideoActivityModal.tsx                # nhánh gdrive
│   └── Dashboard/Pages/Course/EditCourseStructure/Buttons/NewActivityButton.tsx  # storage, processing subtitle
├── locales/en.json, locales/vi.json         # khoá i18n mới (các locale khác fallback en theo FR-003)
└── tests/
    ├── gdrive-video.test.mjs
    └── gdrive-no-link-guard.test.mjs

docker/nginx.conf, apps/cli/src/templates/nginx.ts     # proxy_read/send_timeout /api/v1 → 86400s
.gitignore, .dockerignore, apps/api/.gitignore, apps/api/.dockerignore   # gg_drive_credentials/, **/gdrive_token*.json
.github/workflows/web-lint.yaml                        # + job bun test tests
docs/content/self-hosting/configuration/{environment-variables,storage}.mdx   # FR-018: env mới, redirect URI + consent In production, đĩa tạm 2×, proxy ≥ 24h, không Cloudflare, dọn tay Drive, lịch kiểm tra overlay
```

**Structure Decision**: Web application hai phần (`apps/api` + `apps/web`) theo cấu trúc monorepo hiện có. Mã tích hợp Drive gom vào package mới `apps/api/src/services/integrations/gdrive/`, tách khỏi `services/courses/activities/video.py` để test độc lập bằng `httpx.MockTransport` và để `video.py` chỉ rẽ nhánh theo `storage`/subtype. Frontend không thêm component mới; mở rộng các component video hiện có vì cùng một `VideoActivity` được dùng ở trang bài học, trang nhúng và Boards (FR-017).

## Thiết kế chính (tóm tắt; chi tiết trong research.md)

1. **Config**: `GDriveConfig{enabled, credentials_path, token_path, root_folder_name}`; env `LEARNHOUSE_GDRIVE_*`; `enabled` qua `_env_bool`.
2. **Readiness**: `get_status()` = cờ + stat file credential/token + access token dùng được (cache 300 s, keyed mtime token); endpoint `GET /integrations/gdrive/status` có xác thực; không đưa vào `/instance/info`.
3. **Upload**: `validate_upload_stream` → commit session DB → `ensure_folder` root/org/course/activity (UUID) → resumable 8 MiB với resume/retry → `permissions.create` anyone/reader → `files.update copyRequiresWriterPermission` → `files.get` xác nhận → tạo Activity. Lỗi sau khi tạo thư mục activity → xoá file rồi thư mục; lỗi insert DB → xoá thư mục, re-raise.
4. **Lỗi → HTTP**: NotEnabled/NeedsAuthorization/Config 409, Quota 507, Permission/Transient 502, `storage` sai 422; `detail` dạng `"Video : ..."`. Cổng readiness (`require_ready()`) đặt ở đầu mọi thao tác ghi lên Activity Drive: `POST /video`, `PUT /video/{uuid}` (kể cả chỉ đổi tên), `PUT /activities/{uuid}`, `DELETE /activities/{uuid}`, `delete_course` — không sẵn sàng → 409, không thay đổi gì (FR-004). Sắp xếp lại thứ tự trong chương (`reorder_chapters_and_activities`) không đi qua cổng vì chỉ ghi `chapter_activity`. Bất biến nơi lưu trữ (FR-014): `update_activity` từ chối 409 khi body đổi `activity_sub_type` của Activity Drive, ghi đè `content.storage`/`content.gdrive_*`, hoặc đặt `activity_sub_type = SUBTYPE_VIDEO_GDRIVE` cho Activity không phải Drive — vì `ActivityUpdate` hiện nhận cả hai trường này.
5. **Vòng đời**: thay file → upload mới, xoá cũ sau commit (kiểm tra tham chiếu); xoá Activity/khoá học → yêu cầu sẵn sàng, rồi xoá thư mục best-effort (lỗi Drive khi đã sẵn sàng không chặn xoá). `delete_course` xoá Activity bằng DB cascade (không gọi `delete_activity`) và `content` không lưu id thư mục khoá học, nên trước khi xoá: gom `gdrive_folder_id` của mọi Activity Drive trong khoá học; nếu Activity ở khoá học khác (bản clone) tham chiếu một trong các id đó → bỏ qua xoá thư mục khoá học + log WARNING; ngược lại tìm thư mục root/org/course bằng `DriveClient.find_folder` (tìm theo tên, KHÔNG tạo mới) rồi `delete_folder_best_effort`. Hệ quả chấp nhận theo spec: Activity Drive không sửa/xoá được khi tích hợp tắt. Best-effort = một lượt xoá với retry tạm thời, tổng ≤ 60 s (FR-014); thất bại → log WARNING kèm activity/file/folder id. Mỗi tạo/thay/xoá ghi log INFO kiểm toán (user id, org/course/activity uuid, file/folder id, kết quả) — FR-013. Trình duyệt ngắt kết nối sau khi body đã nhận đủ → không phát hiện, máy chủ hoàn tất và tạo Activity (spec Edge Cases).
6. **CLI**: `gdrive-authorize --port 8765 --no-browser`, `gdrive-status` (exit 1 khi chưa sẵn sàng).
7. **Frontend**: radio "Nơi lưu trữ" chỉ khi `ready`, mặc định Google Drive, cảnh báo FR-010; ẩn Video Settings + captions với Drive; iframe sandbox không `allow-popups` + overlay cao `DRIVE_OVERLAY_HEIGHT_PX = 56` (hằng số duy nhất trong `videoSource.ts`, áp dụng qua inline style) + dòng gợi ý; hộp thoại sửa chỉ tên file gốc + nơi lưu + thay file; `ActivityPreview` không in link/id.

## Rủi ro và cách xử lý

| # | Rủi ro | Xử lý trong PR |
|---|--------|----------------|
| R1 | nginx `/api/v1` timeout 3600 s; upload lớn đồng bộ có thể bị 504 dù API vẫn tạo Activity | Nâng `proxy_read_timeout`/`proxy_send_timeout` lên 86400s trong `docker/nginx.conf` và template CLI; docs ghi không hỗ trợ Cloudflare và cần đĩa tạm ~2× file |
| R2 | Kết nối DB bị giữ suốt lúc đẩy lên Drive | `commit()` trước khi gọi Drive; không chạm `db_session` trong lúc upload |
| R3 | Drive xử lý video vài phút sau upload → không "phát ngay" | Không chặn; spec/docs nêu rõ |
| R4 | `drive.file` không thấy thư mục tạo tay → tạo thư mục "LearnHouse" thứ hai | Docs: không tự tạo thư mục gốc; cache `root_folder_id`, bỏ khi 404 |
| R5 | `clone_course` sao chép `content` → 2 Activity dùng chung file Drive; xoá/thay ở bản này phá bản kia | Kiểm tra tham chiếu trước mọi lệnh xoá; thay file luôn resolve thư mục của chính activity theo path; follow-up `files.copy` ghi trong PR |
| R6 | Downgrade migration thất bại nếu còn hàng `SUBTYPE_VIDEO_GDRIVE` | Ghi chú trong migration (giống `r5s6t7u8v9w0`) |
| R7 | Overlay không áp dụng khi iframe toàn màn hình | `sandbox` không `allow-popups` vẫn vô hiệu nút mở tab mới; ghi nhận trong spec |
| R9 | Export/import khoá học chỉ mang metadata Drive | Ngoài phạm vi; ghi nhận |
| R10 | Uỷ quyền trên host headless | `--no-browser` + SSH port-forward, hoặc chạy local rồi copy token |
| R12 | Quota upload ~750 GB/ngày | 403 không phải `storageQuotaExceeded` → `GDrivePermissionError`, log raw reason |

## Complexity Tracking

> Không có vi phạm Constitution Check; bảng để trống.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
