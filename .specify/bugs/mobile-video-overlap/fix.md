# Bug Fix: Video bị che và không thao tác được trên màn hình nhỏ

- **Slug**: mobile-video-overlap
- **Fixed**: 2026-10-23
- **Assessment**: ./assessment.md
- **Status**: applied

## Summary

Player video.js chuyển sang chế độ `responsive` và có thêm CSS cho layout hẹp: ẩn PiP, âm lượng và chất lượng, giữ play, ±15s, thanh tua và fullscreen. Nhờ vậy control bar không còn tràn và cắt mất nút fullscreen, menu popup cũng co theo player. Thanh điều hướng cố định của trang activity không còn hiển thị dưới `sm`, và menu mobile khi đóng không còn chặn thao tác chạm. Sau phản hồi của người dùng (ảnh chụp: activity là video Google Drive, khung đen, không có điều khiển), đã đo thực tế và sửa thêm: khung nhúng Drive được đặt `min-height` 300px, vì Drive không bao giờ dựng player thấp hơn 300px. Phản hồi thứ hai (ảnh khi đang phát: thanh tua nằm trên cùng, nút pause bị cắt) cho thấy trên thiết bị cảm ứng Drive dùng một giao diện điều khiển khác, chỉ hiển thị đúng trong khung gần vuông. Phản hồi thứ ba (nút điều khiển to đè lên hình) dẫn tới phát hiện: khung gần vuông rộng ≥ ~520px khiến Drive chuyển sang một hàng điều khiển gọn nằm **dưới** hình. Vì vậy trên màn hình cảm ứng hẹp hơn 640px, iframe được dựng ở 560×588 rồi `scale` xuống vừa độ rộng khung (khoảng 0,6 trên điện thoại). Dải che 56px giữ nguyên và nằm trong lớp được scale.

## Changes

| File | Change | Notes |
|------|--------|-------|
| `apps/web/components/Objects/Activities/Video/LearnHousePlayer.tsx` | modified | Thêm `responsive: true` vào options `videojs(...)`. video.js tự gắn `vjs-layout-tiny/x-small/small` theo độ rộng player (breakpoint ≤210 / ≤320 / ≤425px), ẩn time display, playback-rate, volume slider và giới hạn chiều cao menu (`max-height` 5em/10em). |
| `apps/web/components/Objects/Activities/Video/player-controls.css` | modified | small: ẩn PiP và volume panel. x-small: ẩn thêm quality selector, **giữ lại** progress bar (video.js mặc định ẩn nó) và tắt spacer. tiny: ẩn thêm ±15s. Selector có tiền tố `.learnhouse-player` để thắng specificity của rule gốc trong `video-js.css`. |
| `apps/web/components/Pages/Activity/FixedActivitySecondaryBar.tsx` | modified | Wrapper thêm `hidden sm:block`. Trên điện thoại vẫn còn nút Trước/Sau ngay dưới nội dung (`activity.tsx:1012-1036`). |
| `apps/web/components/Objects/Menus/OrgMenu.tsx` | modified | Khi menu mobile đóng: thêm `invisible pointer-events-none` (trước đây chỉ có `opacity-0`). |
| `apps/web/components/Objects/Activities/Video/videoSource.ts` | modified | Thêm `DRIVE_PLAYER_MIN_HEIGHT_PX = 300`, `DRIVE_TOUCH_FRAME_WIDTH_PX = 560`, `DRIVE_TOUCH_FRAME_HEIGHT_PX = 588`, `DRIVE_TOUCH_MAX_CONTAINER_PX = 640` và hàm thuần `resolveDriveTouchFrame(containerWidth)` trả về kích thước dựng, hệ số scale và chiều cao khung. |
| `apps/web/components/Objects/Activities/Video/Video.tsx` | modified | Tách component `DriveEmbed`. Không cảm ứng: khung 16:9, `minHeight` 300px. Cảm ứng (`(pointer: coarse)`) và hẹp hơn 640px: iframe dựng ở 560×588 (hoặc rộng bằng khung nếu khung ≥ 560px), `transform: scale(...)` từ góc trên trái, khung cao theo kích thước đã scale. Dải che nằm trong lớp scale. Cây element giống nhau ở hai chế độ nên iframe không bị mount lại. Độ rộng đo bằng `useResizeObserver`, media query lấy từ `usehooks-ts`; thư viện này đã có sẵn trong dự án. |
| `apps/web/tests/mobile-video-layout.test.mjs` | added test | Guard tĩnh (bun test) cho các bất biến trên. |

## Diff Highlights

```ts
// LearnHousePlayer.tsx
fill: true,
responsive: true,
preload: 'metadata',
```

```css
/* player-controls.css */
.learnhouse-player .video-js.vjs-layout-x-small .vjs-progress-control { display: flex; }
.learnhouse-player .video-js.vjs-layout-small .vjs-picture-in-picture-control,
.learnhouse-player .video-js.vjs-layout-small .vjs-volume-panel, … { display: none; }
```

Ước tính độ rộng control bar (1em = 10px):
- small (~336px): play, ±15s, [captions], quality, fullscreen tốn 24–28em, thanh tua còn khoảng 56–96px.
- x-small (~312px): play, ±15s, [captions], fullscreen tốn 16–20em, thanh tua còn khoảng 112–152px.

```tsx
// Video.tsx: khung nhúng Drive
<div className="relative w-full aspect-video …" style={{ minHeight: DRIVE_PLAYER_MIN_HEIGHT_PX }}>
```

Số đo (Chromium 155 headless, file Drive thật của activity `797f1c9c-…`, kích thước khung → kích thước player YouTube bên trong Drive):
- 335×188 → 335×**300**
- 400×225 → 400×**300**
- 480×270 → 480×**300**
- 534×300 → 534×300
- 560×315 → 560×315
- 640×360 → 640×360

Giao diện cảm ứng (giả lập iPhone 13 / Pixel 5, đã bấm phát), kích thước khung → bố cục:
- 334×300 (sau bản sửa thứ nhất) → thanh tua nằm ở y≈15, **trong dải che 56px**; hàng pause bị cắt ở đáy. Khớp ảnh chụp thứ hai của người dùng.
- 390×360, 430×400, 480×300, 640×360 (khung rộng hơn cao) → cùng lỗi trên.
- 334×360, 334×420 (khung cao hơn rộng) → thanh tua dưới dải che, có hàng pause ±10s, nhưng hàng nút đáy bị cắt ở mép phải (mất fullscreen).
- 390×400 → fullscreen bị cắt một phần.
- 420×440 thu nhỏ về 334 hoặc 300 → đủ nút, thanh tua dưới dải che, nhưng thanh tua và hàng pause ±10s to, **đè lên nửa dưới hình** (phản hồi thứ ba của người dùng). Các nút không tự ẩn khi đang phát.
- Thu nhỏ về 337, theo khung dựng:
  - 520×546 (scale 0,65): **một hàng gọn dưới hình** gồm pause, âm lượng, CC, tốc độ, cài đặt, fullscreen.
  - 560×588 (scale 0,60): như trên, thêm thời gian. Nút khoảng 31px. **Đã chọn kích thước này.**
  - 600×630 (scale 0,56): như 560, nút nhỏ hơn.
  - 640×672 (scale 0,53): thêm ±10s, nhưng nút khoảng 28px, hơi nhỏ để chạm.
- So sánh: trang `/view` của Drive dùng giao diện YouTube cổ điển (thanh mảnh ở đáy, khung 16:9) nhưng gửi header `X-Frame-Options: SAMEORIGIN` nên không nhúng được. Thử nhúng thẳng `youtube.googleapis.com/embed/?docid=…` thì player báo lỗi phát.

Như vậy mọi khung thấp hơn 300px đều bị cắt phần dưới. Trên điện thoại (khoảng 188px) mất khoảng 112px, gồm toàn bộ thanh điều khiển. Đây là nguyên nhân thật của hiện tượng "che mất nội dung" và "không thao tác được" với video Drive. Nguyên nhân không phải là dải che 56px như assessment phỏng đoán.

## Tests Added or Updated

- `apps/web/tests/mobile-video-layout.test.mjs`:
  - `video.js is created with responsive: true`: player bật responsive mode.
  - `fullscreen and play are never hidden by our narrow-layout rules`: các rule thu gọn không bao giờ ẩn fullscreen hoặc play.
  - `PiP and the volume panel are dropped on small and x-small layouts`: hai layout này phải ẩn PiP và volume panel.
  - `x-small keeps the progress bar that stock video.js hides`: layout x-small vẫn hiện thanh tua.
  - `the minimum height constant is 300px` / `the Drive frame applies it as its min-height`: khung Drive không thấp hơn player của Drive.
  - `a phone-width container gets a near-square frame wide enough for the compact controls`: khung dựng ≥ 520px rộng, cao ≥ rộng, scale vừa khít độ rộng khung.
  - `a container wider than the frame is laid out at its own width, unscaled`: khung rộng từ 560px trở lên thì giữ nguyên độ rộng, không scale.
  - `no touch frame before a width is measured or from the sm breakpoint up`: không áp dụng khi chưa đo được độ rộng hoặc khi khung ≥ 640px.
  - `the touch layout is gated on a coarse pointer, and the strip scales with the frame`: chỉ áp dụng cho `pointer: coarse`, và dải che nằm trong lớp được scale.
  - `the fixed secondary bar is hidden below sm`: thanh cố định không hiện dưới `sm`.
  - `the closed mobile menu is invisible and ignores pointer events`: menu mobile khi đóng không nhận thao tác chạm.

## Local Verification

- `bun test tests/mobile-video-layout.test.mjs tests/gdrive-no-link-guard.test.mjs tests/gdrive-video.test.mjs tests/video-source.test.mjs` (trong `apps/web`): **65 pass, 0 fail**.
- `bun test tests/` (toàn bộ): 298 pass, 8 fail. Trong đó 7 fail thuộc `billing-internal-key.test.mjs`. File này pass khi chạy riêng, và trên code gốc (đã stash) cũng fail y hệt khi chạy cả bộ; lỗi có sẵn do các test ảnh hưởng lẫn nhau, không liên quan bug này. Fail còn lại là test Drive cũ, đã cập nhật theo cấu trúc mới; sau khi cập nhật thì pass.
- `npx eslint` trên `Video.tsx` và `videoSource.ts`: 0 lỗi, 1 cảnh báo có sẵn (`setVideoId` trong effect của phần YouTube).
- Kiểm tra ngược: `git stash` 4 file source rồi chạy lại test mới thì **5 fail, 1 pass**. Test còn pass là test "không ẩn fullscreen/play"; nó pass trên code cũ vì khi đó chưa có rule ẩn nào. Như vậy test bắt được regression.
- `npx tsc --noEmit -p .` (trong `apps/web`): exit 0, 0 lỗi.
- Render thật: dùng Playwright với Chromium từ nix, cài trong scratchpad, không thêm vào dự án. Giả lập Pixel 5 có touch, khung 335px với iframe giống hệt cấu hình trong `Video.tsx` (cùng `sandbox`) và dải che 56px:
  - Trước khi sửa (cao 188px): ảnh bị phóng to và cắt, nút play nằm ở mép dưới, không có thanh điều khiển.
  - Sau khi sửa (335×300):
    - Chạm vào giữa khung thì video phát; dải che không chặn thao tác này.
    - Thanh điều khiển của Drive hiện đủ ở đáy: pause, thời gian, âm lượng, fullscreen, menu và thanh tua.
    - Nút pop-out nằm gọn trong dải che 56px.
- Trang activity thật trên localhost: đăng nhập bằng tài khoản người dùng cung cấp, chỉ truyền qua biến môi trường, không ghi ra file:
  - iPhone 15 / Pixel 5: khung 337×354, iframe dựng 560×588, `scale(0.602)`, `pointer: coarse`. Khi đang phát, thanh tua và hàng nút (pause, thời gian, âm lượng, CC, 1x, cài đặt, fullscreen) nằm hoàn toàn dưới hình, không có gì đè lên hình.
  - Desktop 1280×900: khung 1113×626, không có transform. Giao diện Drive desktop không đổi.
  - Cả ba trường hợp: điểm chạm ở góc trên phải rơi vào dải che (`DIV[aria-hidden]`).
  - Chạm vào giữa khung đã scale thì video phát, tức là toạ độ chạm được ánh xạ đúng qua `transform`.

## Deviations from Assessment

- **Mục 3 (Drive): nguyên nhân gốc khác với assessment.** Assessment cho rằng dải che 56px là thủ phạm. Đo thực tế cho thấy nguyên nhân là Drive giữ chiều cao player tối thiểu 300px, nên khung 16:9 trên điện thoại cắt mất phần dưới. Cách sửa là đặt `min-height` 300px cho khung, **không** đổi dải che. Tiếp đó, sau phản hồi thứ hai, phát hiện thêm: trên thiết bị cảm ứng, giao diện điều khiển của Drive cần khung gần vuông (đã thêm chế độ dựng 560×588 rồi scale; ở cỡ này các nút nằm dưới hình). Nếu không có chế độ này, thanh tua của giao diện cảm ứng nằm dưới dải che 56px ở mọi khung 16:9. Ở khung 335×300, top bar của Drive (nút pop-out) vẫn nằm trong 56px trên cùng và thanh điều khiển ở đáy không bị che, nên vẫn đúng FR-009a của `specs/001-gdrive-video-upload/spec.md`. Phần sửa này thêm vào sau phản hồi đầu tiên của người dùng; lần áp dụng đầu đã hoãn mục này.
- **Breakpoint x-small:** assessment chỉ đề xuất "giữ play/progress/fullscreen". Khi làm thì phát hiện video.js mặc định ẩn progress bar ở x-small (≤320px, ví dụ điện thoại 360px trừ padding), nên đã thêm rule ghi đè để hiện lại. Phần này vẫn nằm trong file đã liệt kê.
- **Test:** assessment đề xuất test E2E Playwright. Suite `apps/e2e` chạy trên self-host dựng từ image đã publish, nên sẽ không kiểm được thay đổi local cho tới khi phát hành. Thay vào đó đã thêm guard tĩnh theo mẫu `tests/gdrive-no-link-guard.test.mjs`. Test E2E chuyển sang Follow-ups.
- **Giới hạn max-height menu theo chiều cao player:** không cần CSS riêng vì `responsive: true` đã kích hoạt rule gốc của video.js cho `.vjs-layout-*` (`max-height` 5em/10em).

## Follow-ups

- Kiểm tra lại trên iOS Safari và Android Chrome thật. Các số đo (300px, 560×588, ngưỡng khoảng 520px) lấy trên Chromium giả lập; nếu Drive dựng khác trên WebKit thì chỉnh các hằng trong `videoSource.ts`.
- Tablet cảm ứng (khung ≥ 640px) vẫn dùng khung 16:9. Theo số đo ở 640×360, thanh tua của giao diện cảm ứng ở đó cũng nằm dưới dải che, nên cần một bug riêng.
- Watermark "Made with learnhouse" (`Watermark.tsx`, `fixed bottom-8 end-8 z-50`) và tooltip "mark as complete" đè lên đáy video khi video nằm sát đáy màn hình. Khi cuộn video lên giữa màn hình thì không đè; cân nhắc xử lý riêng.
- Cân nhắc ghi các ràng buộc "khung Drive ≥ 300px" và "khung gần vuông trên cảm ứng" vào `specs/001-gdrive-video-upload/spec.md` (FR-009a) để spec phản ánh đúng.
- Thêm E2E Playwright với viewport 375×667 cho activity video hosted: fullscreen và progress nằm trong bounding box của player, không có phần tử `fixed` nào giao với player.
- Ở viewport hẹp, playback-rate bị ẩn theo mặc định của video.js. Nếu người học cần đổi tốc độ trên điện thoại thì cân nhắc đưa vào menu gộp.
- `AISidePanelInline` (w-[380px]) trên tablet 640–1024px chưa xử lý, đã ghi trong assessment là ngoài phạm vi điện thoại.
