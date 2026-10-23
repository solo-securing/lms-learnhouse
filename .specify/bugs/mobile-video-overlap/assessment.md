# Bug Assessment: Video bị che và không thao tác được trên màn hình nhỏ

- **Slug**: mobile-video-overlap
- **Created**: 2026-10-23
- **Source**: pasted text (+ URL đã bị từ chối, xem bên dưới)
- **Verdict**: valid
- **Severity**: high

## Report (verbatim or summarized)

> "Khi xem video trên các thiết bị màn hình nhỏ (ví dụ: điện thoại) thì bị che mất nội dung video và không thể thao tác với video"

URL kèm theo (truyền dưới dạng `slug=`):
- URL: `http://localhost:3000/course/b43ac3a7-823b-4cc6-9b42-da44300308f9/activity/797f1c9c-01bd-4f39-a13c-e30ec9147392`
- Host: `localhost`
- Policy: `auto-refused: loopback host` — không fetch.

Người báo cáo xác nhận thêm (hỏi đáp): cả 4 hiện tượng đều xảy ra — (1) thanh điều khiển bị cắt, không thấy fullscreen/PiP/chất lượng, thanh tua quá ngắn; (2) khi cuộn, một thanh cố định đè lên phần trên video; (3) menu tốc độ/chất lượng/phụ đề mở ra che gần hết video; (4) chạm vào một vùng của video không có phản hồi. Loại video của activity: không rõ.

## Symptom

Trên điện thoại (viewport ~360–430px), khung video ở trang activity chỉ cao ~190–230px; các control của player tràn và bị cắt, menu popup và thanh điều hướng cố định phủ lên video, và một số vùng chạm không phản hồi. Kỳ vọng: video hiển thị đầy đủ, mọi control (play, tua, fullscreen) dùng được bằng cảm ứng.

## Reproduction

1. Mở một activity video (`/course/<uuid>/activity/<uuid>`) trên điện thoại hoặc DevTools device mode (ví dụ iPhone SE / Pixel 5, ~360–390px).
2. Với video upload (hosted): bấm play → quan sát control bar: các nút bên phải (fullscreen, PiP, chất lượng, phụ đề) bị cắt, thanh tiến trình gần như biến mất.
3. Mở menu tốc độ phát (hoặc chất lượng) → menu che phần lớn khung video.
4. Cuộn trang để video lên gần đầu màn hình → thanh `FixedActivitySecondaryBar` (top 60px, cao 64px) xuất hiện và cùng header 60px phủ ~124px trên của video.
5. Với video Google Drive: chạm vào ~56px trên cùng của khung video → không phản hồi.

[NEEDS CLARIFICATION: loại video của activity trong URL, thiết bị/trình duyệt cụ thể (iOS Safari / Android Chrome), hướng màn hình.]

## Suspected Code Paths

- `apps/web/components/Objects/Activities/Video/LearnHousePlayer.tsx:133-150` — `videojs(...)` khởi tạo không có `responsive: true`, nên các class `vjs-layout-tiny/x-small/small` (ẩn time display, playback-rate, volume và thu nhỏ menu — `video.js/dist/video-js.css:842-847, 1762+`) không bao giờ được áp dụng. Control bar có ~10 control `flex: none` rộng 4em (play, ±15s seek, volume, remaining time, playback rate, subs/caps, quality selector, PiP, fullscreen) ≈ 370px+ > chiều rộng player trên điện thoại → tràn, phần thừa bị cắt bởi `overflow-hidden` của khung `aspect-video` (`Video.tsx:131`). Giải thích hiện tượng (1) và (3).
- `apps/web/components/Objects/Activities/Video/LearnHousePlayer.tsx:217-223` — chèn thêm 2 nút ±15s vào control bar, làm tràn nặng hơn trên màn hình hẹp.
- `apps/web/components/Pages/Activity/FixedActivitySecondaryBar.tsx:166-205` — thanh `fixed top-[60px]`, `h-16`, z-index `--z-drag-overlay` (30), hiện khi `.activity-info-section` cuộn khỏi viewport (rootMargin -80px). Trên mobile phần info xếp dọc nên rất cao; ngay khi người dùng cuộn để xem video, thanh này cộng với header 60px (`OrgMenu.tsx:147`) phủ ~124px — hơn nửa chiều cao video. Giải thích hiện tượng (2).
- `apps/web/components/Objects/Activities/Video/Video.tsx:115-120` + `videoSource.ts:171` — với Drive video, dải trong suốt `DRIVE_OVERLAY_HEIGHT_PX = 56` phủ phía trên iframe để chặn nút mở/chia sẻ. Trên khung cao ~190px, dải này chiếm ~30% diện tích; top bar của Drive embed trên mobile có thể thấp hơn 56px nên dải đè cả vùng video → chạm không phản hồi. Giải thích hiện tượng (4) cho video Drive (tính năng mới ở commit 91f1569).
- `apps/web/components/Objects/Menus/OrgMenu.tsx:383-390` — menu mobile khi đóng chỉ `-top-full opacity-0`, không `invisible`/`pointer-events-none`; nếu chiều cao nội dung > 100vh (ví dụ điện thoại nằm ngang) phần đáy vô hình có thể chặn chạm ở đầu viewport. Nghi vấn phụ, độ tin cậy thấp.
- `apps/web/components/Objects/Activities/AI/AIActivityAsk.tsx:908` — panel AI inline `w-[380px] shrink-0` cạnh nội dung (`activity.tsx:985-1004`); trên tablet 640–1024px khi mở panel, video bị ép còn rất hẹp. Ngoài phạm vi điện thoại, ghi nhận để xử lý cùng.

## Root Cause Hypothesis

Trang activity và player chưa được tối ưu cho viewport hẹp ở ba lớp độc lập: (a) player video.js chạy không có chế độ `responsive`, nên toàn bộ control bar kích thước desktop bị nhồi vào khung ~340px và bị cắt, menu popup không co lại; (b) `FixedActivitySecondaryBar` luôn bật trên mobile và phủ phần trên khung video vốn đã thấp; (c) với video Drive, dải chặn cố định 56px không co theo kích thước khung. Độ tin cậy: **cao** cho (a) và (b) (suy ra trực tiếp từ code/CSS), **trung bình** cho (c) (phụ thuộc layout embed của Google trên mobile, cần kiểm chứng trên thiết bị).

## Proposed Remediation

**Preferred**:
1. `LearnHousePlayer.tsx`: thêm `responsive: true` vào options của `videojs(...)` (có thể kèm `breakpoints` mặc định) để video.js tự gắn `vjs-layout-*` và ẩn bớt control. Bổ sung CSS trong `player-controls.css` cho `.vjs-layout-x-small/.vjs-layout-small/.vjs-layout-tiny`: ẩn nút ±15s và PiP ở layout hẹp, giữ play / progress / fullscreen luôn hiển thị; giới hạn `max-height` của `.vjs-menu-content` theo chiều cao player.
2. `FixedActivitySecondaryBar.tsx`: không hiển thị thanh trên viewport hẹp (ví dụ `hidden sm:block` cho wrapper, hoặc chỉ hiện khi cuộn qua khỏi khung nội dung thay vì khỏi `.activity-info-section`). Điều hướng Trước/Sau trên mobile đã có sẵn ở `PreviousActivityButton`/`NextActivityButton` dưới nội dung (`activity.tsx:1012-1036`).
3. `Video.tsx` (Drive): thu nhỏ dải chặn trên màn hình hẹp (chiều cao responsive, hoặc chỉ phủ góc phải nơi có nút mở/chia sẻ thay vì toàn chiều ngang) — cần đo top bar thực tế của Drive embed trên mobile trước khi chốt số.
4. (Phụ) `OrgMenu.tsx`: thêm `invisible pointer-events-none` khi menu đóng.

**Alternatives**:
- Dùng `fluid`/native controls trên mobile (`nativeControlsForTouch: true`): đơn giản, control native luôn vừa màn hình, nhưng mất nút ±15s, chọn chất lượng HLS và thumbnail preview, và hành vi khác nhau giữa iOS/Android.
- Thay vì ẩn hẳn secondary bar, làm nó gọn hơn trên mobile (cao 40px, chỉ còn nút ‹ ›) — vẫn chiếm diện tích, chỉ giảm nhẹ.

**Files likely to change**:
- `apps/web/components/Objects/Activities/Video/LearnHousePlayer.tsx`
- `apps/web/components/Objects/Activities/Video/player-controls.css`
- `apps/web/components/Pages/Activity/FixedActivitySecondaryBar.tsx`
- `apps/web/components/Objects/Activities/Video/Video.tsx` (và có thể `videoSource.ts`)
- `apps/web/components/Objects/Menus/OrgMenu.tsx` (phụ)
- Test e2e mới trong `apps/e2e/`

**Tests to add or update**:
- E2E (Playwright, viewport 375×667): mở activity video hosted → fullscreen button và progress control nằm trong bounding box của player và click được; mở menu playback rate → menu nằm trong khung player.
- E2E mobile: cuộn tới video → không có phần tử `fixed` nào giao với bounding box của player (hoặc secondary bar không hiển thị ở viewport < 640px).
- E2E Drive (nếu có fixture): điểm chạm ở giữa/khu vực play của iframe không bị phần tử overlay chặn (`elementFromPoint` trả về iframe).
- Regression desktop (1280px): control bar vẫn đủ nút ±15s, quality, PiP; secondary bar vẫn xuất hiện khi cuộn.

## Risks & Considerations

- `responsive: true` thay đổi bộ control hiển thị ở các kích thước trung bình (cả desktop khi cửa sổ hẹp, chế độ embed `apps/web/app/embed/...`) — cần kiểm tra cả trang embed và focus mode.
- Ẩn secondary bar trên mobile làm mất lối tắt điều hướng khi đang cuộn sâu (activity dài dạng dynamic); có thể chỉ ẩn cho activity video.
- Thu nhỏ dải chặn Drive làm giảm khả năng chặn nút "mở trong Drive" — đánh đổi giữa mục tiêu bảo vệ (commit 91f1569) và khả dụng; dải chặn vốn chỉ là biện pháp cosmetic.
- `FixedActivitySecondaryBar` và `AISidePanelInline` cùng dùng IntersectionObserver trên `.activity-info-section` — thay đổi điều kiện hiển thị cần giữ đồng bộ hai nơi.
- Không có rủi ro dữ liệu/migration/API.

## Open Questions

- [NEEDS CLARIFICATION: activity `797f1c9c-…` là video hosted, Google Drive hay YouTube?]
- [NEEDS CLARIFICATION: thiết bị/trình duyệt và hướng màn hình khi tái hiện (iOS Safari vs Android Chrome; dọc/ngang).]
- [NEEDS CLARIFICATION: có muốn giữ secondary bar trên mobile ở dạng thu gọn hay ẩn hẳn?]
- [NEEDS CLARIFICATION: chiều cao thực tế top bar của Drive embed trên mobile để chỉnh dải chặn.]
