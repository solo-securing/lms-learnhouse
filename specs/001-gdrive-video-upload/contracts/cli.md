# Contract: lệnh CLI uỷ quyền và kiểm tra Google Drive

Cả hai lệnh là `@cli.command()` Typer trong `apps/api/cli.py`, chạy từ `apps/api`:

```bash
uv run python cli.py gdrive-authorize [--port 8765] [--no-browser]
uv run python cli.py gdrive-status
```

Logic nằm trong `src/services/integrations/gdrive/` (`authorize.py`, `readiness.py`, `client.py`) để được đo coverage; `cli.py` chỉ là lớp vỏ.

## `gdrive-authorize`

| Mục | Nội dung |
|-----|----------|
| Mục đích | Uỷ quyền một lần: mở trình duyệt, đăng nhập Google, đồng ý scope `drive.file`, lưu token. |
| Đầu vào | `LEARNHOUSE_GDRIVE_CREDENTIALS_PATH`, `LEARNHOUSE_GDRIVE_TOKEN_PATH` (env hoặc `config.yaml`). Tuỳ chọn `--port` (mặc định 8765, phải khớp redirect URI đã đăng ký `http://localhost:<port>/`), `--no-browser` (in URL thay vì mở trình duyệt; vẫn lắng nghe loopback). |
| Hành vi | `InstalledAppFlow.from_client_secrets_file(credentials_path, scopes=["https://www.googleapis.com/auth/drive.file"]).run_local_server(port=<port>, open_browser=<bool>, access_type="offline", prompt="consent")` → ghi `creds.to_json()` vào `token_path` (atomic, mode 0600) → gọi `about()` và in email tài khoản. |
| Không cần cờ bật | Lệnh chạy được ngay cả khi `LEARNHOUSE_GDRIVE_ENABLED=false` (để chuẩn bị trước khi bật). |
| Stdout thành công | `Google Drive authorized as <email>. Token saved to <token_path>.` |
| Exit code | `0` thành công; `1` thiếu/hỏng credential (`GDriveConfigError`), người dùng huỷ đồng ý, hoặc không ghi được token. Thông báo lỗi in ra stderr kèm hướng dẫn (đăng ký redirect URI, kiểm tra đường dẫn). |
| Không được | In `client_secret`, `refresh_token` hay nội dung token ra màn hình/log. |

## `gdrive-status`

| Mục | Nội dung |
|-----|----------|
| Mục đích | Kiểm tra trạng thái kết nối cho người vận hành. |
| Hành vi | `get_status(force=True)` (bỏ qua cache) → nếu `ready`, gọi `about()` lấy `user.emailAddress` và `storageQuota`. |
| Stdout | Các dòng `enabled: true/false`, `ready: true/false`, `reason: <reason|->`, `account: <email>` (chỉ khi ready), `quota: <usage>/<limit>` (chỉ khi ready), `credentials_path: <path>`, `token_path: <path>`. |
| Exit code | `0` khi `ready`; `1` khi không sẵn sàng (để dùng trong script/healthcheck). |

## Mã lỗi/`reason` dùng chung

Xem `contracts/gdrive-status.openapi.yaml` — CLI in cùng chuỗi `reason` như endpoint.

## Ghi chú vận hành

- Host headless: chạy với `--no-browser`, mở URL trên máy có trình duyệt và forward cổng `ssh -L 8765:localhost:8765 <host>`; hoặc chạy lệnh trên máy cá nhân rồi copy file token lên máy chủ (giữ mode 0600).
- Token bị thu hồi: `gdrive-status` báo `needs_reauthorization`; chạy lại `gdrive-authorize`. API tự nhận token mới nhờ theo dõi mtime file, không cần khởi động lại.
