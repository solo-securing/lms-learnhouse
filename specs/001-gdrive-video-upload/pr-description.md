# feat(video): store video activities on the operator's Google Drive

Base branch: `dev`. Spec, plan and task list: `specs/001-gdrive-video-upload/`.

## What this does

Instructors can keep a video activity's file on the operator's personal Google Drive instead of the server. The option appears in **Add activity → Video → Upload** only when the backend reports the integration ready; the server relays the file to Drive inside the same request (resumable upload, 8 MiB chunks, never the whole file in RAM), shares it as *anyone with the link – viewer* with `copyRequiresWriterPermission`, verifies it, and only then creates the activity with the new subtype `SUBTYPE_VIDEO_GDRIVE`. Learners watch through a sandboxed Drive `preview` iframe (no `allow-popups`, transparent strip over the top bar, plain-text hint); the client never renders a link to Drive.

Backend: `apps/api/src/services/integrations/gdrive/` (errors, credentials, client, readiness, service, authorize), `GET /api/v1/integrations/gdrive/status`, `storage=server|gdrive` on `POST /activities/video`, Drive branches in the video/activity/course services, Alembic migration `g1d2r3i4v5e6`, CLI `gdrive-authorize` / `gdrive-status`.
Frontend: `services/integrations/gdrive.ts`, `videoSource.ts` helpers, create/edit dialogs, player, preview, i18n (en, vi, plus ar because `apps/web/tests/rtl-guard.test.mjs` requires `ar.json` to cover every `en.json` key; the other locales fall back to English per FR-003).
Ops: nginx `/api/v1` timeouts raised to 24 h, docs for env vars and setup, `web-tests` CI job.

## Behaviour guarantees (from the spec)

- FR-004: every write to a Drive activity (create, rename/publish via `PUT /activities/{uuid}`, replace, delete, delete course) requires the integration to be ready; otherwise 409 and nothing changes. Playback never depends on readiness.
- FR-014: storage location never changes; `PUT /activities/{uuid}` refuses subtype/`content` rewrites of Drive fields with 409.
- FR-015: credential and token files are referenced by path only, ignored by git/docker, token written atomically with mode 0600; no token or secret is ever logged or returned.
- SC-005: any failure after the activity folder exists removes the file and folder and creates no activity.
- SC-008: `drive.google.com` occurs in exactly one function (`resolveDrivePreviewUrl`), guarded by `apps/web/tests/gdrive-no-link-guard.test.mjs`.

## Verification

Automated (all green locally):

| Gate | Result |
| --- | --- |
| `uvx ruff@0.15.9 check .` (apps/api) | clean |
| `pytest -k "gdrive or video_activity or activities_video or cli_gdrive" --cov=src/services/integrations/gdrive --cov-fail-under=90` | 243 passed, 9 skipped, package coverage **98.35 %** |
| `pytest src/tests/ --cov=src --cov-fail-under=25` (as CI) | 5947 passed, 29 skipped, total coverage 96.99 %. The only failure is `test_active_users.py::TestRecordActivity::test_ee_records`, which is pre-existing and environment-dependent: it reproduces on its own in 0.4 s with none of this branch's code loaded, and neither the test nor `src/services/security/activity.py` is touched here. It passed in the third convergence run and failed in the fourth on the same checkout. |
| `bun test tests` (apps/web) | 295 passed (32 new) |
| `bunx tsc --noEmit` | clean |
| `bunx eslint <touched files>` | 0 errors (15 pre-existing warnings from the #800 backlog) |
| `scripts/lockfiles.sh --check` | all lockfiles up to date |
| `alembic upgrade head` → `downgrade -1` → `upgrade head` on the dev database | enum gains/loses/gains `SUBTYPE_VIDEO_GDRIVE` |

Manual (quickstart §4, run against the dev API with `curl`):

| Step | Result |
| --- | --- |
| Flag off: `GET /integrations/gdrive/status` | `{"enabled":false,"ready":false,"reason":"disabled"}` |
| Flag off: same call unauthenticated | 401 |
| Flag off: `POST /activities/video -F storage=gdrive` | 409 `Video : Google Drive storage is not enabled on this instance`, no activity created |
| `-F storage=bogus` | 422 |
| Flag on, client-secrets present, no token: API start | one WARNING `token_missing` naming `gdrive-authorize`; not repeated on later status calls |
| Flag on, no token: status / POST | `reason: token_missing` / 409 |
| `cli.py gdrive-status` (flag off, flag on without token, bogus credentials path) | exit 1 with `reason: disabled` / `token_missing` / `credentials_missing` and an actionable hint each time |

**End-to-end against a real Google Drive** (2026-09-16, operator's personal account authorized with `gdrive-authorize`; dev stack via `lh dev`; API calls with `curl`, Drive state read back through the Drive REST API with the operator token, UI checked with headless Chromium; `gdrive-status` → `ready: true`, exit 0, token file mode 0600):

| Step | Result |
| --- | --- |
| US1 `POST /activities/video -F storage=gdrive` (68 KB mp4) | 200 in 7 s; `activity_sub_type=SUBTYPE_VIDEO_GDRIVE`, `details={}`, `content` = {activity_uuid, storage, gdrive_file_id, gdrive_folder_id, original_filename, mime_type, size}, no `uri` |
| US1 file on Drive | `My Drive/LearnHouse/org_<uuid>/course_<uuid>/activity_<uuid>/video.mp4`; permissions `anyone/reader` + owner; `copyRequiresWriterPermission=true`; exactly one `LearnHouse` root folder |
| US1 create dialog (Upload tab) | radios `server` / `gdrive` with `gdrive` pre-selected; warning text next to them; no Video Settings, no captions |
| US1 lesson page (published activity) | iframe `src=https://drive.google.com/file/d/<id>/preview`, `sandbox="allow-scripts allow-same-origin allow-forms allow-presentation"`; overlay 1113×56 px at the top edge, `elementFromPoint` at the top-right corner hits the `aria-hidden` overlay; hint text present; Drive frame loads (200) and shows its player with the Play control and "video.mp4" |
| `.mkv` | 409 `Video : Wrong video format`, no activity, no Drive call |
| Revoked token (access token expired + invalid `refresh_token` written to the token file, no restart) | status `needs_reauthorization` immediately; `POST /activities/video` 409 "needs re-authorization"; `PUT /activities/{uuid}` rename 409 (FR-004); `gdrive-status` exit 1 with the `gdrive-authorize` + "In production" hint; restoring the file → `ready: true` on the next call |
| US4 replace (`PUT /activities/video/{uuid}` with file, `autoplay=true` sent) | 200; new file id in the **same** activity folder; subtype unchanged; `details` still `{}`; old file kept because a cloned course still referenced it |
| US4 delete the cloned course | 200; original files and folders untouched |
| US4 delete Drive activities | 200; file(s) and `activity_<uuid>` folder gone (404) |
| US4 delete a course that still contains a Drive activity | 200; `course_<uuid>` folder gone; `org_<uuid>` and `LearnHouse` remain (documented manual cleanup) |

Not exercised here: the transient-network path (blocking `googleapis.com` needs root on this host; it is covered by `test_gdrive_client.py` / `test_gdrive_service.py`) and the audit `INFO` lines, which land in the `lh dev` terminal.

Observation: deleting every Drive activity first and the course afterwards leaves an empty `course_<uuid>` folder on Drive, because the course-level cleanup only runs when the course still contains a Drive activity (T050). Listed under follow-ups.

`apps/e2e/features/` has no video journey (assignments, rtl, scorm only), so `e2e.yaml` was not triggered.

**SC-008 rendered-HTML scan** (Chromium driven by Playwright against `bun run dev` + `uv run python app.py`, with a seeded `SUBTYPE_VIDEO_GDRIVE` activity whose `gdrive_file_id` is the synthetic `SC008SCANFAKEFILEID0001` — no Google account involved; the rendered DOM was serialised after hydration and every `/api/v1` response the browser received was inspected):

| Surface | `drive.google.com` | Drive file id | Links to Drive |
| --- | --- | --- | --- |
| Lesson page `/course/{uuid}/activity/{uuid}` | 1 occurrence, only in the player `<iframe src>` | 1, only in the same `src` | none (`href`, visible text and script payload all clean) |
| Embed page `/embed/default/course/{uuid}/activity/{uuid}` | 1, only in `<iframe src>` | 1, only in `<iframe src>` | none |
| Dashboard course structure `/dash/courses/course/{uuid}/content`, including the `ActivityPreview` hover card | 0 | 0 | none — the preview reads "Google Drive Video · scan-original.mp4" as plain text |
| Edit dialog (`EditVideoActivityModal`) | 0 | 0 | none — text: "Google Drive Video / Storage location: Google Drive / Original file: scan-original.mp4 / Replace video" |
| 22 API responses seen by the browser | 0 | only `GET /activities/{uuid}` and `GET /courses/{uuid}/meta`, i.e. the `content.gdrive_file_id` FR-008 requires the client to have | — |
| Board canvas `/board/{uuid}` with an *Activity* block pointing at the Drive video — inserted through the toolbar + picker, then re-opened cold and hard-reloaded so the block is rehydrated from the persisted Yjs state (selected and deselected) | 1 per capture, only in the player `<iframe src>` | 1 per capture, only in the same `src` | none — the block header's only link is the LMS lesson URL `/course/{uuid}/activity/{uuid}` |
| 10 API responses received by the board page (archived verbatim, every content type) | 0 | only `GET /activities/{uuid}` and `GET /courses/{uuid}/meta` as the bare `content.gdrive_file_id` / `content.gdrive_folder_id` fields | — |
| Persisted board state (`board.ydoc_state`, 281 bytes, byte-identical to the collab server's Redis copy) and 4 collab websocket sessions (positive control: the activity uuid crosses the wire) | 0 | 0 — the `activityBlock` node stores only `activityUuid`, `courseUuid`, `x`, `y`, `width`, `height` | — |

The iframe carried `sandbox="allow-scripts allow-same-origin allow-forms allow-presentation"` and `referrerpolicy="no-referrer"`. The server-rendered HTML of the lesson/embed pages (curl with the session cookies) contains neither string: the player renders client-side.

**Boards (T086, scanned on a database freshly reset with `lh-db-reset`)**: the stack was brought up with `lh dev`, a temporary course/chapter/Drive video activity (real upload of a 70 KB `ftyp`-only mp4 through `POST /activities/video -F storage=gdrive`) and a board were created through the API, and Playwright drove the real UI: toolbar *Activity* tool → canvas click → picker (course, then activity) → wait for the Drive iframe. Captured: the hydrated DOM (block freshly inserted, and again after a cold open and a hard reload so it rehydrates from the persisted Yjs state, selected and deselected), the block's `outerHTML`, every `/api/v1` response body, every collab websocket frame, the persisted `ydoc_state`, all 66 `/_next/static` chunks the page loaded, and the collab server's Redis copy of the document. Needles: `drive.google.com`, `docs.google.com`, `googleusercontent`, `drive.usercontent`, `googlevideo`, the file id, the folder id, `/file/d/`, `uc?id=`, `open?id=`, plus URL-/entity-/JS-escaped, base64, reversed and chunked encodings of the host and file id. Result: the host and the file id occur exactly once per capture, only in the iframe `src`; the only occurrence of `drive.google.com` in the client bundles is the compiled template literal of `resolveDrivePreviewUrl`; the folder id never reaches the DOM. The iframe carried `sandbox="allow-scripts allow-same-origin allow-forms allow-presentation"`, `referrerpolicy="no-referrer"` and `title="Google Drive Video"`. The result was then checked adversarially by a multi-agent pass (four independent lenses — DOM re-parse, measurement-script audit, data path, code path — plus three refuters and a completeness critic): 0 leaks, 0 refutations, verdict *holds*; remaining gaps are non-blocking (Google's own player chrome inside the cross-origin iframe was not exercised because Drive rejects the synthetic fixture as an unplayable format; a viewer-role collaborator was not tried — the client renders the same component regardless of role). Temporary data was deleted through the API afterwards (activity delete removed the Drive file and folder).

## Constitution exceptions

1. **Principle IV (static analysis)** — no Python type checker and no automated formatting check run in CI. This is the project-wide gap already recorded in `.specify/memory/constitution.md` ("to be closed by follow-up work"); it is deliberately not added in this PR to keep the scope to the feature. Tracking issue: https://github.com/solo-securing/lms-learnhouse/issues/1.
2. **Principle III (90 % coverage)** — `fail_under = 25` in `apps/api/pyproject.toml` and `--cov-fail-under=25` in `api-tests.yaml` are left unchanged (recorded debt). Patch coverage for the new package is 98.35 % (table above); nothing was added to `[tool.coverage.run] omit`.

## Follow-ups (out of scope, documented)

- `clone_course` copies `content` verbatim, so a cloned course shares the original's Drive files; deletes are reference-aware and skip shared folders with a WARNING. A server-side `files.copy` on clone would give each course its own files (plan R5).
- Deleting an organization cascades in the database only; `<root>/<org uuid>` on Drive must be removed by hand (documented in `storage.mdx`).
- Course export/import carries only the Drive metadata, not the file.
- An empty `course_<uuid>` folder stays on Drive when a course is deleted after all its Drive activities were already removed; the operator deletes it by hand, or a follow-up could look the folder up by name on every course deletion.
- `GET /activities/{uuid}` and `GET /courses/{uuid}/meta` deliver `content.gdrive_folder_id` to every authenticated client although the client only reads `gdrive_file_id`; it is not a link (SC-008 holds), but FR-008 could be narrowed so non-author roles receive the file id only.
- Session-replay tooling (Sentry Replay, PostHog session recording) serialises element attributes, so with a DSN/key configured the Drive preview URL in the iframe `src` would be sent to those vendors on sampled sessions; a `blockSelector` for `iframe[src*="drive.google.com"]` would prevent it. Inactive in the dev stack, outside SC-008 as written.

## Reviewer notes

- One new dependency: `google-auth-oauthlib==1.4.1` (CLI-only, imported lazily inside `authorize.py`; the API process never loads it). No `google-api-python-client`.
- `validate_upload_stream` is a pure refactor of `validate_upload`'s head; existing tests unchanged and green.
- `docker/nginx.conf` `/api/v1` timeouts 3600 s → 86400 s; the CLI template already proxies with 86400 s.
- Downgrade of migration `g1d2r3i4v5e6` fails while any `SUBTYPE_VIDEO_GDRIVE` row exists (documented in the migration docstring).
- Retry policy (FR-011): `DriveClient` performs exactly five retries with 1/2/4/8/16 s (+ jitter) back-off in both `_request` and `resumable_upload`. A transient failure of the resumable *status query* (`Content-Range: bytes */size`) is one more retry against the same budget instead of aborting the upload, and Google's `reason` is logged on retryable 403s.
- `ActivityPreview` renders the Drive label through `t('activities.video_gdrive.label')`; `gdrive-no-link-guard.test.mjs` now also fails on a hard-coded label (FR-003).
- `gdrive-authorize` tells the operator what to check when the client-secrets file is missing or invalid (`LEARNHOUSE_GDRIVE_CREDENTIALS_PATH`, redirect URI, consent screen).
- `storage.mdx` notes that Drive may need a few minutes to process a large video before the embedded player plays it.
- Second convergence pass (T078–T085, T087): `PUT /activities/{uuid}` with `"content": null` on a Drive activity is now refused with the 409 storage-invariant error instead of wiping the Drive ids; a Drive error while resolving `<root>/<org>/<course>` no longer trips an `UnboundLocalError` in the failure log (the original 409/502/507 reaches the client); `DriveClient._request` re-sends the caller's headers on every retry (the resumable-session POST keeps `X-Upload-Content-*`); both Drive branches in `video.py` close the spooled upload file in a `finally`; the `gdrive-authorize` "could not load the OAuth client file" branch carries the same redirect-URI guidance as the missing-file branch; the edit dialog's "Leave empty to keep the current video" hint is an i18n key (en/vi/ar); `test_config_gdrive.py` no longer depends on the developer's `apps/api/.env`; `test_gdrive_errors.py` pins the status/detail contract of every error class and `DEFAULT_TIMEOUT` is asserted; `storage.mdx` tells operators to `chmod 600` the OAuth client JSON.
- Third convergence pass (T088–T092): the remaining write paths that could reach a Drive activity outside the readiness gate are closed — the generic `POST /activities/` refuses `SUBTYPE_VIDEO_GDRIVE` with 409 (a Drive video only exists once its file is on Drive), `PUT /activities/external_video/{uuid}` and `PUT /activities/documentpdf/{uuid}` refuse a Drive activity with the 409 storage-invariant error before touching it, and `POST /activities/{uuid}/versions/{n}/restore` runs the readiness gate and the storage invariant against the snapshot (a snapshot naming another Drive file is refused). Audit lines and "delete it by hand" warnings for rename/replace/delete now carry `org=<uuid>` (FR-013), and a replacement that fails after this request created the activity folder (cloned activity) removes that empty folder again.
- Fourth convergence pass (T093–T096): clean-up only, no behaviour change. The unused `resolve_activity_folder` wrapper is gone (the replace flow uses `ensure_activity_folder`, which also reports whether it created the folder) and its test cases were folded into `TestEnsureActivityFolder`. `gdrive-no-link-guard.test.mjs` now also pins the overlay's `aria-hidden="true"`, `tabIndex={-1}` and its use of `DRIVE_OVERLAY_HEIGHT_PX` (FR-009a) — verified by mutation: dropping either attribute, or hard-coding the height, turns the guard red. Three previously unexercised branches of the Drive video service gained tests: a missing organization row on create and on replace (both 404 before any Drive call) and replacing the file while renaming in the same request.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
