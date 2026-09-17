# Contract: nội dung Activity Drive và trạng thái trên Google Drive

## `ActivityRead` cho Activity Drive

```json
{
  "activity_uuid": "activity_9f1c…",
  "name": "Bài 1 - Giới thiệu",
  "activity_type": "TYPE_VIDEO",
  "activity_sub_type": "SUBTYPE_VIDEO_GDRIVE",
  "content": {
    "activity_uuid": "activity_9f1c…",
    "storage": "gdrive",
    "gdrive_file_id": "1AbC…",
    "gdrive_folder_id": "1XyZ…",
    "original_filename": "bai-1.mp4",
    "mime_type": "video/mp4",
    "size": 123456789
  },
  "details": {},
  "extra_metadata": null
}
```

Cam kết với frontend:

- `content` KHÔNG chứa `uri`, `filename`, hay bất kỳ URL Drive nào.
- URL nhúng duy nhất được phép dựng ở client: `https://drive.google.com/file/d/{gdrive_file_id}/preview`, chỉ dùng làm `src` của iframe. Không có `<a href>` tới Drive ở bất kỳ giao diện nào (được bảo vệ bởi `apps/web/tests/gdrive-no-link-guard.test.mjs`).
- iframe: `sandbox="allow-scripts allow-same-origin allow-forms allow-presentation"` (không `allow-popups`, không `allow-top-navigation`), `allowFullScreen`, `referrerPolicy="no-referrer"`; overlay trong suốt che dải trên (`absolute inset-x-0 top-0 h-14 z-10`).

## Trạng thái file trên Drive sau khi tạo thành công

| Thuộc tính | Giá trị | API |
|-----------|---------|-----|
| Vị trí | `<root>/<org_uuid>/<course_uuid>/<activity_uuid>/video.<ext>` | `files.create` (resumable) với `parents=[activity_folder_id]` |
| Quyền | `{type: "anyone", role: "reader"}` | `permissions.create` |
| Chống tải xuống/in/sao chép | `copyRequiresWriterPermission: true` | `files.update` |
| Xác nhận | `files.get?fields=id,size,trashed,copyRequiresWriterPermission,permissions(type,role)`; yêu cầu `size` khớp, không `trashed`, cờ `true`, có permission anyone/reader | `files.get` |

Nếu xác nhận thất bại → xoá file rồi xoá thư mục activity → không tạo Activity → HTTP 502.

## Vòng đời

| Sự kiện | Drive |
|---------|-------|
| Thay video (PUT) | file mới trong cùng thư mục activity; file cũ bị xoá sau commit (best-effort, trừ khi Activity khác tham chiếu) |
| Xoá Activity | yêu cầu tích hợp sẵn sàng; xoá thư mục activity (best-effort, lỗi Drive không chặn) |
| Xoá khoá học (service) | nếu có Activity Drive: yêu cầu tích hợp sẵn sàng; xoá thư mục khoá học (best-effort) |
| Xoá tổ chức | chỉ cascade DB; không dọn Drive (ghi nhận) |
| Tích hợp không sẵn sàng (cờ tắt, thiếu credential/token, cần uỷ quyền lại) sau khi đã có Activity Drive | học viên vẫn phát được (iframe không cần token); MỌI yêu cầu tạo, cập nhật (kể cả đổi tên qua `PUT /activities/{uuid}`), thay file và xoá Activity Drive bị từ chối 409 cho tới khi sẵn sàng trở lại (FR-004) |

## Scope và quyền

Scope OAuth: `https://www.googleapis.com/auth/drive.file` — app chỉ thấy/quản lý file và thư mục do chính nó tạo. Thư mục gốc do người vận hành tạo tay sẽ không được nhìn thấy; app tạo thư mục mới cùng tên.
