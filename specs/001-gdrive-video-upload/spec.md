# Feature Specification: Upload video Activity lên Google Drive

**Feature Branch**: `001-gdrive-video-upload`

**Created**: 2026-09-16

**Status**: Draft

**Input**: User description: "Thêm tuỳ chọn upload content lên google drive đối với Activity là video. Google drive này là từ tài khoản cá nhân của tôi. Tôi đã có file credential.json. Chỉ hiển thị tuỳ chọn upload lên google drive khi cấu hình hệ thống được enable."

## Clarifications

### Session 2026-09-16

- Q: Khi giảng viên tạo Activity video nguồn Google Drive, Activity được tạo ra lúc nào so với quá trình đẩy file lên Drive? → A: Máy chủ nhận file, đẩy lên Drive ngay trong cùng yêu cầu, chỉ tạo Activity khi Drive đã nhận xong; thất bại thì không tạo gì (đồng bộ, không có trạng thái "đang tải").
- Q: Khi cấu hình đã bật nhưng tích hợp chưa sẵn sàng (thiếu credential hoặc token), hộp thoại tạo Activity video hiển thị nguồn Google Drive như thế nào? → A: Ẩn hoàn toàn, giống như khi cấu hình tắt; người vận hành biết qua nhật ký và lệnh kiểm tra trạng thái.
- Q: Tuỳ chọn Google Drive có áp dụng cho video block bên trong bài học dạng trang không? → A: Không; chỉ áp dụng cho Activity kiểu video độc lập. Video block trong bài học dạng trang nằm ngoài phạm vi.
- Q: Khi sửa Activity video đã tồn tại, giảng viên có được đổi nơi lưu trữ (máy chủ ↔ Google Drive ↔ YouTube) không? → A: Không. Activity Drive chỉ thay được bằng video Drive khác; các loại khác giữ nguyên loại. Muốn đổi nơi lưu thì xoá và tạo Activity mới.
- Q: Trong hộp thoại tạo Activity video, tuỳ chọn Google Drive xuất hiện dưới dạng nào? → A: Nằm trong tab "Tải file lên" dưới dạng lựa chọn "Nơi lưu trữ: Máy chủ / Google Drive", mặc định là Google Drive khi tích hợp sẵn sàng, kèm cảnh báo quyền xem khi chọn Google Drive.
- Q: Mức bảo vệ mong muốn với video trên Drive là "gây khó cho việc tải xuống" hay "không ai xem được ngoài LMS"? → A: Gây khó cho việc tải xuống: giữ trình phát nhúng Drive, file chia sẻ "ai có liên kết đều xem" nhưng bật "người xem không được tải xuống/in/sao chép"; chấp nhận ai có link vẫn xem được trên Drive và không chống quay màn hình.
- Q: Lớp phủ ở frontend chặn thao tác ở vùng nào của trình phát nhúng Drive, học viên điều khiển bằng cách nào? → A: Lớp phủ chỉ che dải phía trên của trình phát (nơi có nút mở ở tab mới/menu của Drive); học viên vẫn dùng thanh điều khiển sẵn có của Drive để phát/tạm dừng/tua/âm lượng/toàn màn hình.
- Q: Khi trình duyệt học viên chặn nội dung nhúng bên thứ ba nên trình phát Drive không hiện được, trang Activity làm gì? → A: Hiển thị thông báo hướng dẫn cho phép nội dung nhúng của bên thứ ba; KHÔNG hiển thị link Drive.
- Q: Giảng viên/quản trị viên có được thấy liên kết hoặc nút "Mở trên Google Drive" trong giao diện quản lý khoá học không? → A: Không ai thấy link trong LMS; hộp thoại sửa chỉ hiển thị tên file gốc và nơi lưu trữ "Google Drive"; người vận hành tra file trực tiếp trên Drive theo cấu trúc thư mục.

### Session 2026-09-16 (lập kế hoạch)

- Q: File credential là OAuth client loại "web" không có redirect URI; xử lý thế nào cho luồng uỷ quyền một lần? → A: Giữ client web; người vận hành đăng ký `http://localhost:8765/` vào Authorized redirect URIs; lệnh uỷ quyền dùng cố định cổng 8765.
- Q: Phạm vi quyền Drive và trạng thái màn hình đồng ý? → A: Chỉ scope `drive.file` (app chỉ thấy file/thư mục do chính nó tạo); thư mục gốc cấu hình theo tên, app tự tạo; người vận hành chuyển consent screen sang "In production" để refresh token không hết hạn sau 7 ngày.
- Q: Tên thư mục con trên Drive đặt theo cách nào? → A: Chỉ UUID kỹ thuật (`<gốc>/<org_uuid>/<course_uuid>/<activity_uuid>/`), giống cấu trúc lưu trên máy chủ.
- Q: Trình phát nhúng Drive không hỗ trợ tự phát/tắt tiếng/thời điểm; xử lý khối Video Settings thế nào? → A: Ẩn toàn bộ khối Video Settings với Activity Drive; Activity Drive không có thiết lập phát.

### Session 2026-09-16 (giải quyết mâu thuẫn checklist)

- Q: Khi tích hợp Google Drive không sẵn sàng, giảng viên có được sửa thông tin không liên quan tới file (tên, thứ tự, xuất bản) hoặc xoá Activity Drive đã tồn tại không? → A: Không. Chặn toàn bộ cập nhật (kể cả đổi tên) và cả xoá Activity Drive khi tích hợp không sẵn sàng; chỉ phát lại vẫn hoạt động. Hệ quả: Activity Drive cũ chỉ sửa/xoá được sau khi bật lại và uỷ quyền lại tích hợp.
- Q: Khi quyền chia sẻ file trên Drive bị sửa thủ công nên học viên không xem được, trang Activity phản ứng thế nào? → A: Không phát hiện phía trang (trình duyệt không cho biết khung nhúng có hiện được hay không). Bỏ thông báo "video không khả dụng"; chỉ giữ gợi ý thường trực dưới trình phát theo FR-009b. Đây là lỗi vận hành, người vận hành tự sửa quyền trên Drive.
- Q: Key Entities mô tả Activity Drive có "liên kết xem/nhúng" và "thiết lập phát", trái với FR-008/FR-009; sửa thế nào? → A: Sửa Key Entities theo FR-008/FR-009: thuộc tính gồm tên, định danh file Drive, định danh thư mục activity trên Drive, tên file gốc, định dạng, kích thước; không lưu liên kết, không có thiết lập phát.
- Q: Email tài khoản Google Drive đã uỷ quyền được phép xuất hiện ở đâu? → A: Chỉ trong lệnh kiểm tra trạng thái trên máy chủ dành cho người vận hành. Điểm trạng thái mà giao diện gọi (mọi vai trò đã đăng nhập) chỉ trả bật/sẵn sàng/lý do; không trả email, đường dẫn file hay quota.

### Session 2026-09-16 (phân tích chéo spec/plan/tasks)

- Q: "Tất cả ngôn ngữ giao diện đang được hỗ trợ" trong FR-003 có nghĩa là 22 file locale hiện có không? → A: Không. Tính năng bắt buộc có tiếng Anh và tiếng Việt; các ngôn ngữ khác dùng cơ chế fallback sang tiếng Anh sẵn có của giao diện cho tới khi được dịch riêng.
- Q: Sắp xếp lại thứ tự Activity trong chương có bị chặn khi tích hợp không sẵn sàng không? → A: Không. Thao tác này chỉ ghi lên liên kết chương–activity, không ghi lên bản ghi Activity, nên không thuộc phạm vi FR-004.
- Q: Điểm cập nhật chung của Activity cho phép đổi loại con và ghi đè `content`; áp dụng thế nào với Activity Drive? → A: Máy chủ từ chối mọi yêu cầu đổi loại con của Activity Drive, ghi đè các trường Drive trong `content`, hoặc chuyển Activity khác sang loại Drive qua điểm cập nhật chung (FR-014).
- Q: Xoá khoá học gốc khi khoá học nhân bản vẫn trỏ tới file trong thư mục khoá học gốc trên Drive? → A: Không xoá thư mục khoá học trên Drive nếu còn Activity ở khoá học khác tham chiếu bất kỳ thư mục activity nào bên trong; ghi nhật ký để dọn thủ công (FR-014).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Giảng viên tạo Activity video lưu trên Google Drive (Priority: P1)

Khi instance đã bật tích hợp Google Drive, giảng viên mở hộp thoại tạo Activity video, vào tab "Tải file lên" và thấy lựa chọn "Nơi lưu trữ" với hai giá trị "Máy chủ" và "Google Drive", trong đó "Google Drive" được chọn sẵn. Giảng viên chọn file video từ máy, hệ thống nhận file, đẩy lên Google Drive của người vận hành, rồi tạo Activity trỏ tới video đó. Học viên mở Activity và xem video qua trình phát nhúng của Google Drive.

**Why this priority**: Đây là giá trị cốt lõi của tính năng — chuyển dung lượng video từ máy chủ sang Drive mà không thay đổi trải nghiệm tạo bài học.

**Independent Test**: Bật cấu hình, tạo một Activity video với nơi lưu trữ Google Drive và file mp4 nhỏ, xác nhận file xuất hiện trong thư mục Drive đúng cấu trúc và học viên phát được video trong trang Activity.

**Acceptance Scenarios**:

1. **Given** tích hợp Google Drive đã bật và đã uỷ quyền, **When** giảng viên giữ nơi lưu trữ "Google Drive" (mặc định), chọn file mp4/webm hợp lệ và bấm tạo, **Then** giảng viên thấy tiến trình tải lên; Activity mới chỉ xuất hiện trong chương sau khi file đã nằm trên Drive, đã được chia sẻ và xác nhận (với file lớn, Google Drive có thể cần thêm vài phút xử lý trước khi trình phát nhúng phát được).
2. **Given** Activity video Drive đã sẵn sàng, **When** học viên có quyền mở Activity, **Then** trang hiển thị trình phát nhúng của Google Drive có lớp phủ che dải phía trên; học viên phát, tạm dừng, tua, chỉnh âm lượng và bật toàn màn hình bằng thanh điều khiển của Drive, nhưng không bấm được vào nút mở ở tab mới/menu của Drive.
3. **Given** giảng viên đang tạo Activity Drive, **When** upload lên Drive thất bại (hết dung lượng Drive, mất kết nối, token hết hạn), **Then** giảng viên nhận thông báo lỗi rõ ràng, không tạo ra Activity "rỗng" trong chương, và có thể thử lại.
4. **Given** file được đẩy lên Drive thành công, **When** kiểm tra trên Google Drive, **Then** file nằm trong `<thư mục gốc>/<org_uuid>/<course_uuid>/<activity_uuid>/` và được chia sẻ ở chế độ "ai có liên kết đều xem được" với thiết lập "người xem không được tải xuống, in, sao chép" đã bật.

---

### User Story 2 - Tuỳ chọn chỉ hiển thị khi hệ thống bật tích hợp (Priority: P1)

Người vận hành bật/tắt tích hợp bằng cấu hình hệ thống. Khi tắt (hoặc thiếu credential/token), giảng viên không thấy lựa chọn "Nơi lưu trữ" (file luôn lưu trên máy chủ như hiện nay) và mọi yêu cầu tạo, cập nhật hoặc xoá Activity Drive gửi thẳng tới máy chủ đều bị từ chối.

**Why this priority**: Là điều kiện tiên quyết theo yêu cầu; tránh lộ tuỳ chọn không hoạt động và tránh tạo Activity hỏng.

**Independent Test**: Tắt cấu hình, mở hộp thoại tạo video → tab "Tải file lên" không có lựa chọn "Nơi lưu trữ"; gọi trực tiếp yêu cầu tạo, đổi tên hoặc xoá Activity Drive → bị từ chối với thông báo tính năng chưa bật.

**Acceptance Scenarios**:

1. **Given** cấu hình tích hợp tắt, **When** giảng viên mở hộp thoại tạo Activity video, **Then** tab "Tải file lên" không có lựa chọn "Nơi lưu trữ" và file được lưu trên máy chủ như hiện nay.
2. **Given** cấu hình bật nhưng chưa uỷ quyền (thiếu token), **When** giảng viên mở hộp thoại, **Then** lựa chọn "Nơi lưu trữ" bị ẩn hoàn toàn giống như khi cấu hình tắt, và nhật ký hệ thống ghi cảnh báo cho người vận hành.
3. **Given** cấu hình tắt, **When** một yêu cầu tạo, cập nhật (kể cả chỉ đổi tên) hoặc xoá Activity Drive đến máy chủ, **Then** hệ thống từ chối với lỗi "tính năng chưa được bật".
4. **Given** Activity Drive đã tồn tại, **When** người vận hành tắt cấu hình sau đó, **Then** học viên vẫn phát được video đã có; mọi thao tác tạo, cập nhật và xoá Activity Drive bị từ chối cho tới khi tích hợp sẵn sàng trở lại.

---

### User Story 3 - Người vận hành uỷ quyền Google Drive lần đầu (Priority: P2)

Người vận hành có file OAuth client của tài khoản Google cá nhân. Họ chạy một lệnh một lần trên máy chủ, đăng nhập Google trong trình duyệt, xác nhận quyền truy cập Drive, và hệ thống lưu token làm mới vào vị trí đã cấu hình. Từ đó instance tự upload mà không cần thao tác lại, trừ khi token bị thu hồi.

**Why this priority**: Không có bước này thì US1 không chạy được, nhưng chỉ thực hiện một lần và không ảnh hưởng người dùng cuối.

**Independent Test**: Chạy lệnh uỷ quyền với file credential hợp lệ, hoàn tất đăng nhập, xác nhận file token được tạo và lệnh kiểm tra trạng thái báo "đã kết nối" kèm email tài khoản Drive.

**Acceptance Scenarios**:

1. **Given** có file credential và cấu hình đường dẫn, **When** người vận hành chạy lệnh uỷ quyền và hoàn tất đồng ý trên Google, **Then** token được lưu vào đường dẫn cấu hình và lệnh báo thành công.
2. **Given** token đã lưu, **When** hệ thống khởi động, **Then** hệ thống tự làm mới token khi cần mà không yêu cầu tương tác.
3. **Given** token bị thu hồi hoặc không hợp lệ, **When** giảng viên upload, **Then** upload thất bại với thông báo "cần uỷ quyền lại" và nhật ký hướng dẫn người vận hành chạy lại lệnh.

---

### User Story 4 - Thay thế và xoá video Drive theo vòng đời Activity (Priority: P3)

Giảng viên có thể thay video mới cho Activity Drive; khi xoá Activity, file tương ứng trên Drive cũng được dọn.

**Why this priority**: Giữ Drive gọn gàng, tránh rác; không chặn MVP.

**Independent Test**: Thay video của một Activity Drive → Drive có file mới và file cũ bị xoá; xoá Activity → thư mục activity trên Drive bị xoá.

**Acceptance Scenarios**:

1. **Given** Activity Drive tồn tại, **When** giảng viên tải video thay thế, **Then** file mới được đẩy lên Drive, Activity trỏ tới file mới, file cũ bị xoá khỏi Drive; loại nguồn của Activity không đổi.
2. **Given** Activity Drive tồn tại, **When** giảng viên xoá Activity, **Then** thư mục/file trên Drive được xoá (nỗ lực tốt nhất); nếu Drive không phản hồi, Activity vẫn bị xoá và hệ thống ghi nhật ký để dọn sau.
3. **Given** Activity video tự host hoặc YouTube tồn tại, **When** giảng viên mở hộp thoại sửa, **Then** không có tuỳ chọn chuyển sang Google Drive; tương tự Activity Drive không có tuỳ chọn chuyển sang tự host hoặc YouTube, và hộp thoại sửa Activity Drive chỉ hiển thị tên file gốc cùng nơi lưu trữ, không có liên kết hay nút mở file trên Drive.

---

### Edge Cases

- File vượt giới hạn kích thước hiện hành (5 GiB) hoặc sai định dạng (không phải mp4/webm) → từ chối trước khi đẩy lên Drive, thông báo giống luồng tải file hiện có.
- Drive cá nhân hết dung lượng (quota) → báo lỗi cụ thể "Google Drive hết dung lượng", không tạo Activity.
- Mất kết nối giữa chừng khi máy chủ đang đẩy lên Drive → sau khi hết lượt thử lại tự động (FR-011), không tạo Activity, dọn file dở dang trên Drive (nếu đã tạo) và file tạm trên máy chủ, báo lỗi, cho phép thử lại.
- Bước dọn file/thư mục dở dang trên Drive cũng thất bại → vẫn không tạo Activity và vẫn báo lỗi cho giảng viên; ghi nhật ký cảnh báo kèm định danh file và thư mục Drive để người vận hành dọn thủ công. File tạm trên máy chủ luôn được xoá.
- Trình duyệt giảng viên ngắt kết nối sau khi máy chủ đã nhận đủ file nhưng đang đẩy lên Drive → máy chủ tiếp tục hoàn tất và tạo Activity như bình thường; giảng viên thấy lỗi kết nối phía trình duyệt và Activity xuất hiện sau khi tải lại trang. Không có Activity trùng nếu giảng viên tạo lại (hai Activity riêng, giảng viên tự xoá bản thừa).
- Drive từ chối vì hạn mức tải lên theo ngày hoặc giới hạn tốc độ (khác với hết dung lượng lưu trữ) → xử lý như lỗi tạm thời: thử lại theo FR-011, nếu vẫn thất bại báo "Google Drive tạm thời từ chối, hãy thử lại sau" và ghi nhật ký lý do gốc từ Google.
- Thư mục gốc bị xoá/đổi tên trên Drive → hệ thống tự tạo lại thư mục gốc theo tên cấu hình khi không tìm thấy (bỏ định danh đã ghi nhớ khi Drive báo không tồn tại). Thư mục gốc do người vận hành tự tạo bằng tay sẽ không được nhận ra (phạm vi quyền `drive.file` chỉ thấy thư mục do hệ thống tạo).
- Người vận hành chỉnh sửa quyền chia sẻ trên Drive thủ công (tắt "ai có liên kết") → học viên không xem được; hệ thống KHÔNG phát hiện được tình huống này và không hiển thị thông báo riêng, chỉ có gợi ý thường trực dưới trình phát (FR-009b). Đây là lỗi vận hành: người vận hành tự khôi phục quyền chia sẻ trên Drive. Không có kiểm tra quyền phía máy chủ khi mở trang trong phạm vi tính năng này.
- File trên Drive bị đưa vào thùng rác hoặc xoá hẳn ngoài LMS → xử lý như trường hợp quyền chia sẻ bị sửa: hệ thống không phát hiện, không có thông báo riêng; giảng viên khắc phục bằng cách thay file (tải lại video) cho Activity đó.
- Người vận hành uỷ quyền lại bằng một tài khoản Google khác → phát lại các video cũ vẫn hoạt động (file vẫn được chia sẻ ở Drive cũ); thay file tạo file mới trên Drive mới và việc xoá file cũ thất bại theo nỗ lực tốt nhất (ghi nhật ký để dọn thủ công); xoá Activity cũ vẫn thành công, phần Drive ghi nhật ký. Lệnh kiểm tra trạng thái in email tài khoản để người vận hành nhận ra sự thay đổi.
- Hai giảng viên upload song song vào cùng khoá học → không ghi đè nhau (mỗi activity có thư mục riêng). Việc tìm-hoặc-tạo thư mục cha (gốc/tổ chức/khoá học) phải idempotent: tìm theo tên trong thư mục cha trước khi tạo; nếu vẫn xuất hiện thư mục trùng tên do đua tranh, hệ thống dùng thư mục có thời điểm tạo sớm nhất cho các lần sau và không làm hỏng upload đang chạy.
- Trình duyệt học viên chặn nội dung nhúng của bên thứ ba → hệ thống không phát hiện được; dòng gợi ý thường trực dưới trình phát "Nếu video không hiển thị, hãy cho phép nội dung nhúng của bên thứ ba trong trình duyệt" (FR-009b) là thông báo duy nhất; không hiển thị link Drive.
- Tuỳ chọn phụ đề AI, HLS và các chức năng chỉ dành cho video tự host không hiển thị với Activity Drive.
- Google Drive thay đổi bố cục trình phát nhúng (di chuyển nút mở ở tab mới) → lớp phủ có thể không còn che đúng chỗ; đây là rủi ro chấp nhận, cần kiểm tra thủ công định kỳ. Khi học viên bật toàn màn hình, lớp phủ của trang không còn áp dụng; khung nhúng được cấu hình không cho phép mở cửa sổ mới nên nút mở ở tab mới vẫn vô hiệu.
- Tệp trên Drive được sao chép cùng Activity khi nhân bản khoá học → hai Activity có thể trỏ cùng một file; hệ thống không xoá file trên Drive khi vẫn còn Activity khác tham chiếu. Khi thay file ở một trong hai bản, file mới MUST được đặt trong thư mục theo đường dẫn UUID của chính Activity đang sửa (tự tạo nếu chưa có), bản còn lại tiếp tục trỏ tới file cũ và không bị ảnh hưởng.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Hệ thống MUST cung cấp cờ cấu hình cấp instance để bật/tắt tích hợp Google Drive, cùng cấu hình đường dẫn file credential OAuth, đường dẫn file token, và TÊN thư mục gốc trên Drive (mặc định "LearnHouse"); thư mục gốc do hệ thống tự tạo khi thiếu. Phạm vi quyền Drive được yêu cầu chỉ giới hạn ở file/thư mục do hệ thống tạo (`drive.file`).
- **FR-002**: Hệ thống MUST chỉ coi tích hợp là "sẵn sàng" khi cờ bật VÀ đọc được credential VÀ có token hợp lệ. "Token hợp lệ" nghĩa là file token chứa token làm mới VÀ hệ thống làm mới được access token từ Google. Kết quả kiểm tra với Google được lưu tạm tối đa 5 phút; thay đổi trên file token MUST được nhận ngay ở lần kiểm tra kế tiếp mà không cần khởi động lại. Khi không liên lạc được Google lúc kiểm tra (lỗi mạng tạm thời), tích hợp MUST được coi là không sẵn sàng cho tới lần kiểm tra sau khi hết thời gian lưu tạm. Nhật ký chỉ ghi khi lý do không sẵn sàng thay đổi (không lặp lại cùng lý do). Trạng thái này MUST được cung cấp cho giao diện để quyết định hiển thị tuỳ chọn. Dữ liệu trạng thái cung cấp cho giao diện MUST chỉ gồm: cờ bật, sẵn sàng hay không, và lý do khi không sẵn sàng; MUST NOT chứa email tài khoản Drive, đường dẫn file credential/token, dung lượng hay bất kỳ bí mật nào.
- **FR-003**: Trong tab "Tải file lên" của hộp thoại tạo Activity video, hệ thống MUST hiển thị lựa chọn "Nơi lưu trữ" với hai giá trị "Máy chủ" và "Google Drive" chỉ khi tích hợp sẵn sàng, và MUST chọn sẵn "Google Drive" khi lựa chọn này hiển thị; khi không sẵn sàng (cờ tắt, thiếu credential, hoặc chưa uỷ quyền), tuỳ chọn MUST bị ẩn hoàn toàn với mọi vai trò, không hiển thị ở trạng thái vô hiệu. Mọi chuỗi giao diện mới của tính năng (nhãn, cảnh báo, gợi ý, thông báo lỗi) MUST có bản dịch tiếng Anh và tiếng Việt; các ngôn ngữ giao diện khác dùng cơ chế fallback sang tiếng Anh sẵn có cho tới khi được dịch riêng.
- **FR-004**: Máy chủ MUST từ chối mọi yêu cầu tạo, cập nhật (kể cả cập nhật không kèm file như đổi tên, thiết lập xuất bản) và xoá Activity Drive khi tích hợp không sẵn sàng, bất kể giao diện, với lỗi "tính năng chưa được bật". Phát lại video Drive đã có MUST không bị ảnh hưởng. Sắp xếp lại thứ tự Activity trong chương KHÔNG bị chặn vì thao tác này chỉ ghi lên liên kết chương–activity, không ghi lên bản ghi Activity. Hệ quả chấp nhận: Activity Drive tồn tại trước khi tích hợp bị tắt chỉ sửa/xoá được sau khi tích hợp sẵn sàng trở lại.
- **FR-005**: Với nơi lưu trữ Google Drive, giảng viên MUST chọn file từ máy như luồng tải file hiện có; hệ thống nhận file, kiểm tra định dạng/kích thước theo quy tắc video hiện hành (mp4/webm, tối đa 5 GiB), rồi đẩy lên Drive.
- **FR-006**: Hệ thống MUST lưu file trên Drive theo cấu trúc `<thư mục gốc>/<org_uuid>/<course_uuid>/<activity_uuid>/video.<đuôi>`, tự tạo thư mục con còn thiếu; định danh thư mục activity được ghi nhận vào Activity nên việc đổi tên thư mục trên Drive không ảnh hưởng phát lại.
- **FR-007**: Sau khi upload, hệ thống MUST đặt quyền chia sẻ file ở mức "ai có liên kết đều xem được" (chỉ xem) để trình phát nhúng hoạt động, đồng thời MUST bật thiết lập "người xem không được tải xuống, in, sao chép" trên file. Hệ thống MUST xác nhận lại hai thiết lập này ngay sau upload; nếu không đặt được thì coi như upload thất bại (xoá file, không tạo Activity).
- **FR-008**: Activity MUST ghi nhận loại video là "Google Drive" cùng định danh file Drive, định danh thư mục activity trên Drive, tên file gốc, định dạng và kích thước, đủ để phát lại và dọn dẹp sau này. Hệ thống MUST NOT lưu liên kết xem/tải trong Activity; địa chỉ nhúng chỉ được dựng từ định danh file khi hiển thị trình phát. Tên file gốc chỉ dùng để hiển thị: được cắt tối đa 255 ký tự, hiển thị dưới dạng văn bản thuần (không diễn giải HTML), và MUST NOT được dùng làm tên file hay đường dẫn trên Drive hoặc máy chủ.
- **FR-009**: Trang Activity MUST phát video Drive bằng trình phát nhúng của Google Drive. Vì trình phát nhúng của Drive không hỗ trợ tự phát, tắt tiếng hay thời điểm bắt đầu/kết thúc, Activity Drive MUST NOT có thiết lập phát; khối "Video Settings" MUST bị ẩn hoàn toàn trong hộp thoại tạo và sửa Activity Drive.
- **FR-009a**: Trang Activity MUST đặt một lớp phủ trong suốt che dải phía trên của trình phát nhúng Drive (vùng chứa nút mở ở tab mới và menu của Drive) để chặn mọi thao tác chuột/chạm vào vùng đó; lớp phủ MUST không che thanh điều khiển của Drive, để học viên vẫn phát/tạm dừng/tua/chỉnh âm lượng/bật toàn màn hình. Lớp phủ MUST giữ đúng vị trí khi kích thước khung phát thay đổi (màn hình nhỏ, xoay màn hình). Dải che cao 56 px tính từ mép trên khung phát (đủ che thanh tiêu đề của trình phát Drive, nơi đặt nút mở ở tab mới và menu); giá trị này được định nghĩa ở một nơi duy nhất để chỉnh khi Drive đổi bố cục. Lớp phủ MUST NOT nhận focus bàn phím và MUST ẩn với trình đọc màn hình; dòng gợi ý dưới trình phát là văn bản thường, đọc được bằng trình đọc màn hình.
- **FR-009b**: Giao diện LMS, với mọi vai trò (học viên, giảng viên, quản trị viên), MUST không hiển thị hay cung cấp liên kết mở file trên Google Drive dưới bất kỳ hình thức nào (kể cả phương án dự phòng khi trình phát không hiện được, thông báo lỗi, nút chia sẻ, hay hộp thoại sửa Activity); khi trình phát không hiện được, chỉ hiển thị thông báo hướng dẫn (vì không thể phát hiện việc trình duyệt chặn khung nhúng, thông báo hướng dẫn được hiển thị thường trực dưới trình phát ở dạng gợi ý nhỏ). Hộp thoại sửa Activity Drive chỉ hiển thị tên file gốc và nơi lưu trữ "Google Drive".
- **FR-010**: Quyền truy cập trang Activity (khoá/công khai/đăng nhập/giới hạn) MUST tiếp tục được áp dụng như hiện tại; hệ thống MUST hiển thị cảnh báo ngay cạnh lựa chọn "Nơi lưu trữ" khi "Google Drive" được chọn, nêu rõ file được lưu trên Google Drive cá nhân của người vận hành instance và bản thân file có thể xem bởi bất kỳ ai có liên kết (đã tắt tải xuống nhưng không chống quay màn hình). Cảnh báo này là hình thức thông báo duy nhất cho tổ chức; không có bước đồng ý riêng của quản trị viên tổ chức.
- **FR-011**: Việc đẩy file lên Drive MUST diễn ra đồng bộ trong cùng yêu cầu tạo Activity: Activity chỉ được tạo sau khi Drive đã nhận xong file; nếu đẩy lên Drive thất bại thì không tạo Activity và không để lại file dở dang. Giảng viên MUST thấy tiến trình upload và kết quả (thành công/thất bại) như luồng tải video hiện có: giai đoạn trình duyệt→máy chủ hiển thị phần trăm; sau khi đạt 100%, hiển thị trạng thái "Đang đẩy lên Google Drive…" không có phần trăm cho tới khi có kết quả. Với lỗi tạm thời từ Drive (lỗi máy chủ Google, mất kết nối, bị giới hạn tốc độ), hệ thống MUST tự thử lại tối đa 5 lần liên tiếp với thời gian chờ tăng dần (1–16 giây) trước khi báo thất bại; sau đó giảng viên thử lại thủ công. Ứng dụng không đặt ngưỡng thời gian tổng cho một lần đẩy lên Drive; ngưỡng thực tế do proxy quyết định và tài liệu vận hành MUST nêu ngưỡng chờ phản hồi tối thiểu 24 giờ cho route tải video (FR-018). Không có trạng thái trung gian "đang tải lên Drive" trên Activity.
- **FR-012**: Người vận hành MUST có lệnh dòng lệnh để uỷ quyền một lần (đăng nhập Google, đồng ý quyền Drive) tạo ra token, và lệnh kiểm tra trạng thái kết nối. Email tài khoản Drive đã uỷ quyền chỉ được hiển thị trong hai lệnh này (chạy trên máy chủ, dành cho người vận hành); không hiển thị ở bất kỳ giao diện hay điểm truy cập nào của LMS. Khi lý do không sẵn sàng là "cần uỷ quyền lại", lệnh kiểm tra trạng thái MUST in hướng dẫn chạy lại lệnh uỷ quyền và nhắc kiểm tra màn hình đồng ý OAuth đang ở trạng thái "In production" (ở trạng thái "Testing" token làm mới hết hạn sau 7 ngày).
- **FR-013**: Hệ thống MUST tự làm mới token khi hết hạn; khi token bị thu hồi MUST báo lỗi rõ "cần uỷ quyền lại" cho giảng viên và ghi nhật ký cho người vận hành. Mỗi lần tạo, thay file hoặc xoá Activity Drive MUST được ghi nhật ký kiểm toán ở mức thông tin gồm: định danh người dùng thực hiện, định danh tổ chức/khoá học/activity, định danh file và thư mục Drive, kết quả và thời điểm; nhật ký MUST NOT chứa bí mật (FR-015).
- **FR-014**: Khi giảng viên thay video của Activity Drive, hệ thống MUST đẩy file mới lên Drive và xoá file cũ; nơi lưu trữ của một Activity video MUST không thay đổi sau khi tạo (không chuyển đổi giữa tự host, YouTube và Drive khi sửa): máy chủ MUST từ chối, qua mọi điểm cập nhật kể cả điểm cập nhật chung của Activity, yêu cầu đổi loại con của Activity Drive, ghi đè các trường Drive trong nội dung Activity, hoặc chuyển Activity khác sang loại Drive; khi xoá Activity, hệ thống MUST xoá file/thư mục tương ứng trên Drive theo nỗ lực tốt nhất (khi tích hợp sẵn sàng nhưng Drive trả lỗi, lỗi đó không chặn xoá Activity; khi tích hợp không sẵn sàng, yêu cầu xoá bị từ chối theo FR-004). "Nỗ lực tốt nhất" nghĩa là: một lượt xoá với cơ chế thử lại lỗi tạm thời như FR-011, tổng không quá 60 giây; nếu vẫn thất bại, ghi nhật ký cảnh báo kèm định danh activity, file và thư mục Drive để dọn thủ công. Xoá khoá học chứa Activity Drive MUST tuân cùng quy tắc (yêu cầu sẵn sàng; xoá thư mục khoá học trên Drive theo nỗ lực tốt nhất, tìm thư mục theo tên mà không tạo mới); nếu còn Activity ở khoá học khác (do nhân bản) tham chiếu bất kỳ thư mục activity nào bên trong thư mục khoá học đó, hệ thống MUST NOT xoá thư mục khoá học và ghi nhật ký để dọn thủ công. Xoá tổ chức KHÔNG dọn Drive; tài liệu vận hành nêu người vận hành dọn thư mục tổ chức thủ công.
- **FR-015**: Hệ thống MUST KHÔNG lưu credential hay token trong mã nguồn hoặc kho git; các file này chỉ được tham chiếu qua đường dẫn cấu hình, và mẫu cấu hình phải ghi rõ điều này. File token MUST được ghi theo cách nguyên tử (không để lại file hỏng khi ghi dở) với quyền chỉ chủ sở hữu tiến trình đọc/ghi; file credential chỉ đọc. Hệ thống MUST NOT ghi client secret, token làm mới, access token hay nội dung file token vào nhật ký, đầu ra của lệnh dòng lệnh, thông báo lỗi cho người dùng hay phản hồi API.
- **FR-016**: Các chức năng chỉ dành cho video tự host (chuyển mã HLS, phụ đề AI) MUST không được kích hoạt hoặc hiển thị với Activity Drive.
- **FR-017**: Activity Drive MUST được hỗ trợ ở mọi nơi Activity video hiện đang được hiển thị (trang bài học, nhúng ngoài, bảng Boards) với cùng cơ chế trình phát nhúng.
- **FR-018**: Tài liệu vận hành (tự lưu trữ) MUST được cập nhật cùng tính năng và nêu tối thiểu: các biến cấu hình mới; cách đăng ký redirect URI và chuyển màn hình đồng ý OAuth sang "In production"; yêu cầu đĩa tạm tối thiểu bằng 2 lần kích thước file lớn nhất cho phép; ngưỡng chờ phản hồi của proxy tối thiểu 24 giờ cho route tải video và việc không hỗ trợ triển khai sau Cloudflare; cách dọn thủ công file Drive khi nhật ký báo dọn thất bại hoặc khi xoá tổ chức; và lịch kiểm tra thủ công lớp phủ (sau mỗi lần nâng cấp và ít nhất mỗi quý).

### Key Entities

- **Activity video (nơi lưu trữ Google Drive)**: một Activity kiểu video có nơi lưu trữ là "Google Drive"; thuộc tính: tên, định danh file Drive, định danh thư mục activity trên Drive, tên file gốc, định dạng, kích thước. KHÔNG lưu liên kết xem/nhúng (FR-008) và KHÔNG có thiết lập phát (FR-009). Quan hệ: thuộc một chương của một khoá học trong một tổ chức.
- **Cấu hình tích hợp Google Drive (cấp instance)**: bật/tắt, đường dẫn credential, đường dẫn token, thư mục gốc trên Drive. Không thuộc tổ chức nào; áp dụng cho toàn instance.
- **Kết nối Google Drive**: trạng thái uỷ quyền của tài khoản Drive cá nhân (đã kết nối / chưa / cần uỷ quyền lại), email tài khoản (chỉ hiển thị qua lệnh dòng lệnh của người vận hành), thời điểm làm mới token gần nhất.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Khi tích hợp bật, giảng viên tạo được Activity video Drive từ hộp thoại trong tối đa 5 thao tác (chọn nguồn, chọn file, đặt tên, bấm tạo, chờ hoàn tất) — không nhiều hơn luồng tải file hiện có.
- **SC-002**: 100% Activity Drive tạo thành công có thể phát được (trình phát nhúng của Drive phát video) trong trang Activity mà không cần thao tác thêm của giảng viên, đo tại thời điểm tối đa 15 phút sau khi tạo để Google Drive hoàn tất xử lý video.
- **SC-003**: Khi tích hợp tắt hoặc chưa uỷ quyền, 0 trường hợp lựa chọn "Nơi lưu trữ" xuất hiện trong hộp thoại và 100% yêu cầu tạo, cập nhật hoặc xoá Activity Drive gửi trực tiếp tới máy chủ bị từ chối.
- **SC-004**: Video Drive không chiếm dung lượng lưu trữ lâu dài trên máy chủ: sau khi upload xong, không còn bản sao file video trên máy chủ (chỉ còn file tạm trong lúc xử lý).
- **SC-005**: 100% trường hợp upload thất bại để lại trạng thái sạch: không có Activity rỗng trong chương và giảng viên nhận thông báo lỗi có thể hành động trong vòng 5 giây kể từ lúc máy chủ xác định thất bại (sau khi hết lượt thử lại tự động theo FR-011).
- **SC-006**: Người vận hành hoàn tất uỷ quyền lần đầu trong dưới 5 phút với hướng dẫn kèm theo, không phải chỉnh mã nguồn.
- **SC-007**: Xoá Activity Drive dọn được file trên Drive trong ≥ 95% trường hợp bình thường; "bình thường" nghĩa là tích hợp sẵn sàng, Google Drive phản hồi, file vẫn tồn tại và do hệ thống tạo (loại trừ token bị thu hồi, Drive ngừng hoạt động, file đã bị xoá thủ công, tài khoản Drive đã đổi); các trường hợp còn lại được ghi nhật ký để dọn thủ công.
- **SC-008**: 100% file video đẩy lên Drive có thiết lập "người xem không được tải xuống/in/sao chép" được bật; 0 liên kết Drive xuất hiện trong bất kỳ giao diện LMS nào. Cách đo: quét chuỗi "drive.google.com" và định danh file Drive trong HTML đã dựng và phản hồi API của trang Activity, trang nhúng ngoài, bảng Boards và hộp thoại sửa; chuỗi này chỉ được phép xuất hiện trong thuộc tính nguồn của khung nhúng trình phát.

## Assumptions

- Một tài khoản Google Drive cá nhân duy nhất dùng chung cho toàn instance; mọi tổ chức trên instance đều upload vào Drive này. Không có bật/tắt riêng theo tổ chức trong phiên bản này.
- Credential hiện có là OAuth client loại "web" (client_id + client_secret), không phải service account; do đó cần bước uỷ quyền một lần bằng script để lấy token làm mới. Tên file credential thực tế có thể khác `credential.json`; hệ thống nhận đường dẫn qua cấu hình. Người vận hành đăng ký `http://localhost:8765/` làm redirect URI cho client này và chuyển màn hình đồng ý OAuth sang "In production" (ở trạng thái "Testing", Google làm token làm mới hết hạn sau 7 ngày).
- Việc đẩy file lên Drive diễn ra đồng bộ trong request nên yêu cầu proxy ngược không giới hạn thời gian chờ phản hồi quá ngắn; triển khai sau Cloudflare proxy (giới hạn khoảng 100 giây) không được hỗ trợ cho tính năng này. Máy chủ cần dung lượng đĩa tạm khoảng hai lần kích thước file trong lúc xử lý.
- Phát lại bằng trình phát nhúng của Google Drive, chấp nhận giới hạn: không HLS, không phụ đề AI, không tuỳ chỉnh giao diện trình phát, không kiểm soát truy cập ở cấp file (file có thể xem bởi ai có liên kết). Thiết lập "không cho tải xuống/in/sao chép" của Drive là rào cản với người dùng thông thường, không phải cơ chế chống sao chép tuyệt đối: người cố ý có link vẫn xem được trên Drive và có thể quay màn hình. Quyền truy cập trang Activity của LMS vẫn áp dụng.
- Giới hạn định dạng và kích thước dùng lại quy tắc video tự host hiện tại (mp4/webm, tối đa 5 GiB); giới hạn dung lượng thực tế còn phụ thuộc quota của Drive cá nhân.
- File đi qua máy chủ (trình duyệt → máy chủ → Drive); không upload trực tiếp từ trình duyệt lên Drive, để không lộ token cho người dùng cuối.
- Activity Drive vẫn dùng chung mọi hành vi khác của Activity video (xuất bản, khoá, sắp xếp trong chương, theo dõi hoàn thành).
- Không di chuyển (migrate) video tự host sẵn có lên Drive trong phạm vi tính năng này.
- Ngoài phạm vi: xuất/nhập hoặc di chuyển khoá học giữa các instance chỉ mang theo siêu dữ liệu của Activity Drive (định danh file/thư mục); file không được sao chép sang Drive khác.
- Không hỗ trợ hạ cấp (downgrade) lược đồ dữ liệu khi vẫn còn Activity Drive; muốn hạ cấp phải xoá các Activity Drive trước. Migration ghi chú rõ điều này.
- Ngoài phạm vi: video block chèn bên trong bài học dạng trang (Activity kiểu dynamic) vẫn tải lên máy chủ như hiện tại; lựa chọn "Nơi lưu trữ" chỉ xuất hiện ở hộp thoại tạo Activity kiểu video độc lập (tab "Tải file lên").
- Cấu hình mẫu và tài liệu vận hành cần cập nhật; file credential/token phải được loại khỏi kho git (thư mục `gg_drive_credentials/` hiện chưa được bỏ qua).
- Tuân thủ constitution: thay đổi hành vi đi kèm test (API test dưới `apps/api/src/tests/`, web test dưới `apps/web/tests/`), thay đổi lược đồ qua Alembic, cập nhật lockfile nếu thêm phụ thuộc.
