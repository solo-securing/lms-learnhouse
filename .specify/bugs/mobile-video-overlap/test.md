# Bug Verification: Video bị che và không thao tác được trên màn hình nhỏ

- **Slug**: mobile-video-overlap
- **Tested**: 2026-10-23
- **Assessment**: ./assessment.md
- **Fix**: ./fix.md
- **Result**: verified

## Summary

Cả bốn hiện tượng ban đầu không còn tái hiện khi giả lập điện thoại trên Chromium:
- Thanh điều khiển bị cắt.
- Thanh cố định đè lên video.
- Menu che kín video.
- Thanh tua và nút của trình phát Drive bị che hoặc bị cắt.

Không phát hiện regression: test, typecheck và lint không có lỗi mới.

Lần kiểm tự động đầu tiên cho kết quả **partial**, vì chưa kiểm trên thiết bị thật và chưa kiểm video upload trên trang activity thật. Sau đó người dùng tự kiểm tra và **xác nhận bản sửa thành công** (2026-10-23), nên kết quả được nâng lên **verified**. Báo cáo này không ghi lại thiết bị và trình duyệt người dùng đã dùng, vì người dùng không nêu.

## Checks Performed

| Check | Command / Action | Result | Notes |
|-------|------------------|--------|-------|
| Tái hiện (1): control bar video.js bị cắt | Trang thử dựng riêng: video.js, quality-levels, hls-quality-selector trong `apps/web/node_modules` cùng option của `LearnHousePlayer` (fill, playbackRates, nút ±15s, quality selector, phụ đề). Đo ở player rộng 337, 312 và 1000px. Trước sửa: không `responsive`, không `player-controls.css`. Sau sửa: có cả hai. | pass | Trước sửa, ở 337 và 312px, fullscreen và quality nằm ngoài khung player, thanh tua chỉ rộng 40px. Sau sửa, layout `small`/`x-small`, fullscreen nằm trong khung, thanh tua rộng 97/112px. Ở 1000px, các nút và độ rộng thanh tua giống trước. |
| Tái hiện (3): menu che video | Như trên, đo `max-height` của menu tốc độ phát | pass | 337px: 150px → 100px. 312px: 150px → 50px (layout `x-small` còn ẩn luôn nút tốc độ). |
| Tái hiện (2): thanh cố định đè video | Trang activity thật đã đăng nhập, cuộn hết trang, đọc `display` của thanh `top-[60px]` | pass | iPhone 15: thanh có trong trang nhưng `display: none`, cao 0. Desktop 1280: `block`, cao 64px, giữ nguyên hành vi cũ. |
| Không phần tử `fixed` nào chặn video | Trang thật, video cuộn tới cách đỉnh 130px; liệt kê phần tử `fixed` có nhận chạm và giao với khung video | pass | iPhone 15, Pixel 5, desktop: không có phần tử nào. |
| Menu mobile khi đóng | Trang thật, đọc style đã tính của panel `fixed inset-x-0 md:hidden` | pass | iPhone 15, Pixel 5: `visibility: hidden`, `pointer-events: none`. |
| Tái hiện (4): Drive trên cảm ứng | Trang thật: chạm vào giữa khung Drive, chụp ảnh khi đang phát | pass | iPhone 15, Pixel 5: khung 337×354, iframe dựng 560×588, `scale(0.602)`. Video phát; thanh tua và hàng nút (pause, thời gian, âm lượng, CC, 1x, cài đặt, fullscreen) nằm dưới hình. Desktop: khung 1113×626, không có transform, giao diện Drive desktop không đổi. |
| Dải che Drive (FR-009a) | Trang thật: `elementFromPoint` tại góc trên phải của khung | pass | Cả ba profile đều trả về dải che (`aria-hidden`), nên nút mở ở tab mới vẫn bị chặn. |
| Test mới và test đã cập nhật | `bun test tests/mobile-video-layout.test.mjs tests/gdrive-no-link-guard.test.mjs tests/gdrive-video.test.mjs tests/video-source.test.mjs` | pass | 65 pass, 0 fail. |
| Regression suite | `bun test tests/` (trong `apps/web`) | pass (có fail sẵn từ trước) | 303 pass, 7 fail. Cả 7 fail đều thuộc `billing-internal-key.test.mjs`. File này pass 7/7 khi chạy riêng, và trên code gốc (đã stash) cũng fail y hệt khi chạy cả bộ. Không liên quan bug này. |
| Type-check | `npx tsc --noEmit -p .` | pass | exit 0. |
| Lint | `npx eslint` trên 5 file source đã sửa | pass | 0 lỗi, 7 cảnh báo. Code gốc cũng có đúng 7 cảnh báo đó; không có cảnh báo mới. |
| Activity video upload trên trang thật | — | not-run | DB local chỉ có một activity video (`SUBTYPE_VIDEO_GDRIVE`). Lệnh test không được tạo dữ liệu. |
| Thiết bị thật, iOS Safari | — | not-run | Chỉ có Chromium 155 headless với profile iPhone/Pixel (UA và touch giả lập). |
| Kiểm tra thủ công của người dùng | Người dùng tự kiểm tra theo khuyến nghị bên dưới | pass | Người dùng xác nhận "fix thành công". Thiết bị cụ thể không được nêu. |
| Video YouTube | — | not-run | Không có activity YouTube. Code YouTube không đổi; chỉ hưởng thay đổi của thanh cố định và menu. |

## Output Excerpts

```
video.js trên trang thử (player rộng 337px):
before {"layout":"-","fullscreen":{"inside":false},"progress":{"w":40},"quality":{"inside":false},"rateMenuMaxH":"150px"}
after  {"layout":"vjs-layout-small","fullscreen":{"inside":true},"progress":{"w":97},"quality":{"inside":true},"rateMenuMaxH":"100px"}

Trang thật:
iphone15 {"box":[337,354],"layout":[560,588],"transform":"matrix(0.601786, …)","closedMenu":"block/hidden/none","fixedOverVideo":[],"popoutCorner":"blocked by strip"}
desktop  {"box":[1113,626],"layout":[1113,626],"transform":"none","fixedOverVideo":[],"popoutCorner":"blocked by strip"}
thanh cố định sau khi cuộn hết trang: iphone15 "none h=0" | desktop "block h=64"

bun test (4 file video/Drive): 65 pass, 0 fail
bun test tests/: 303 pass, 7 fail (billing-internal-key, lỗi có sẵn; chạy riêng 7 pass)
tsc: exit 0 | eslint: 0 errors, 7 warnings (trước sửa cũng 7)
```

## Residual Risks

- **WebKit / thiết bị thật.** Mọi số đo về Drive được lấy trên Chromium: chiều cao player tối thiểu 300px, ngưỡng khoảng 520px để có hàng nút gọn dưới hình, và khung 560×588. Safari trên iOS có thể dựng trình phát Drive khác. Nếu khác thì chỉnh các hằng `DRIVE_*` trong `videoSource.ts`.
- **Phụ thuộc vào giao diện của Google.** Bố cục trình phát của Drive do Google quyết định và có thể đổi bất kỳ lúc nào, không báo trước. Test guard chỉ khóa phía LearnHouse, không phát hiện được thay đổi phía Google.
- **Cỡ nút.** Nút điều khiển Drive trên điện thoại khoảng 31px, nhỏ hơn khuyến nghị 44px cho màn hình cảm ứng.
- **Video upload trên trang thật** chưa được kiểm, chỉ kiểm trên trang thử dùng cùng thư viện và cùng CSS.
- **Tablet cảm ứng** (khung từ 640px trở lên) vẫn dùng khung 16:9 cho Drive. Theo số đo ở 640×360, thanh tua ở đó có thể vẫn nằm dưới dải che. Đã ghi trong Follow-ups của fix.md.
- **Watermark "Made with learnhouse"** và tooltip "mark as complete" đè lên đáy video khi video nằm sát đáy màn hình. Ngoài phạm vi bản sửa; đã ghi trong fix.md.

## Recommendation

**Đóng bug.** Người dùng đã xác nhận bản sửa thành công khi kiểm tra thủ công. Các mục Follow-ups trong fix.md (tablet cảm ứng, watermark, test E2E) nên tách thành việc riêng.

Khuyến nghị ban đầu, trước khi người dùng xác nhận: đóng bug sau khi kiểm thêm hai việc:
- Mở trang activity Drive trên một iPhone (Safari) và một Android thật, xác nhận thanh tua và fullscreen dùng được.
- Tạo một activity video upload rồi mở ở viewport khoảng 375px, xác nhận control bar không bị cắt.

Nếu Safari dựng trình phát Drive khác, mở lại bằng `/speckit-bug-assess` kèm ảnh chụp. Các mục Follow-ups trong fix.md (tablet, watermark, test E2E) nên tách thành việc riêng.
