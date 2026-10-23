import {
  getActivityVideoStreamUrl,
  getActivityHlsMasterUrl,
  getActivityHlsThumbnailsUrl,
  getActivityCaptionUrl,
} from '@services/media/media'
import type { GDriveStatus } from '@services/integrations/gdrive'

/**
 * Pure helpers for choosing a video activity's playback source.
 *
 * Extracted from the components so the decision logic (HLS-when-ready with an
 * MP4 fallback, the credentials rule, and thumbnail config) can be unit-tested.
 */

export interface HlsThumbnails {
  url: string
  interval: number
  width: number
  height: number
  columns: number
  rows: number
}

export interface HlsActivityMeta {
  extra_metadata?: {
    hls?: {
      status?: string
      thumbnails?: {
        url?: string
        interval?: number
        width?: number
        height?: number
        columns?: number
        rows?: number
      } | null
    }
  } | null
}

/** True once transcoding has produced a ready HLS ladder for the activity. */
export function isActivityHlsReady(activity: HlsActivityMeta): boolean {
  return activity?.extra_metadata?.hls?.status === 'ready'
}

export interface VideoSourceArgs {
  hlsReady: boolean
  orgUuid: string
  courseUuid: string
  activityUuid: string
  filename?: string
}

export interface VideoSource {
  src: string
  isHls: boolean
}

/**
 * Resolve the source URL for a hosted video: adaptive HLS when ready, otherwise
 * the (optimized) progressive MP4 stream. Empty when there's no file.
 */
export function resolveActivityVideoSource(args: VideoSourceArgs): VideoSource {
  const { hlsReady, orgUuid, courseUuid, activityUuid, filename } = args
  // Guard against a missing filename (incl. whitespace-only) or any missing id —
  // otherwise we'd build a URL containing the literal string "undefined".
  if (!filename || !filename.trim()) return { src: '', isHls: false }
  if (!orgUuid || !courseUuid || !activityUuid) return { src: '', isHls: false }
  if (hlsReady) {
    return { src: getActivityHlsMasterUrl(orgUuid, courseUuid, activityUuid), isHls: true }
  }
  return {
    src: getActivityVideoStreamUrl(orgUuid, courseUuid, activityUuid, filename),
    isHls: false,
  }
}

/**
 * The player requests both API playlists and R2 segments. Send the auth cookie
 * only to our API playlist endpoint (for RBAC); presigned R2 segment requests
 * must stay uncredentialed (R2 CORS rejects credentialed wildcard requests).
 */
export function shouldSendHlsCredentials(url: string): boolean {
  // Our authed playlist/key endpoints (activity + video block) — but NEVER a
  // presigned object-storage URL (those always carry X-Amz-* query params).
  // Sending the cookie cross-origin to R2 would fail CORS and leak the cookie.
  const isApiPlaylist =
    url.includes('/api/v1/stream/hls/') || url.includes('/api/v1/stream/block-hls/')
  return isApiPlaylist && !url.includes('X-Amz-')
}

export interface ThumbnailArgs {
  orgUuid: string
  courseUuid: string
  activityUuid: string
}

/**
 * Resolve the hover-scrub thumbnail config for the player from the activity's
 * HLS metadata, or null when unavailable/incomplete. Only valid when HLS is
 * ready (the sprite lives alongside the HLS output).
 */
export function resolveHlsThumbnails(
  activity: HlsActivityMeta,
  ids: ThumbnailArgs
): HlsThumbnails | null {
  const t = activity?.extra_metadata?.hls?.thumbnails
  if (!t || !t.url) return null
  if (!ids.orgUuid || !ids.courseUuid || !ids.activityUuid) return null
  // Every numeric field must be a positive number; reject 0/negative/NaN so the
  // sprite plugin can't be handed a broken grid (which would mis-map or divide).
  const pos = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n > 0
  if (!pos(t.interval) || !pos(t.width) || !pos(t.height) || !pos(t.columns)) {
    return null
  }
  const rows = pos(t.rows) ? t.rows : 1
  return {
    url: getActivityHlsThumbnailsUrl(ids.orgUuid, ids.courseUuid, ids.activityUuid, t.url),
    interval: t.interval,
    width: t.width,
    height: t.height,
    columns: t.columns,
    rows,
  }
}

export interface CaptionTrack {
  code: string
  label: string
  url: string
}

interface CaptionsActivityMeta {
  extra_metadata?: {
    captions?: {
      enabled?: boolean
      languages?: { code?: string; label?: string; status?: string }[]
    } | null
  } | null
}

/**
 * Resolve the ready caption tracks for an activity from its captions metadata.
 * Only languages whose generation finished (`status === 'ready'`) are returned.
 */
export function resolveActivityCaptions(
  activity: CaptionsActivityMeta,
  ids: ThumbnailArgs
): CaptionTrack[] {
  const caps = activity?.extra_metadata?.captions
  if (!caps || !caps.enabled) return []
  if (!ids.orgUuid || !ids.courseUuid || !ids.activityUuid) return []
  return (caps.languages || [])
    .filter((l) => l && l.status === 'ready' && !!l.code)
    .map((l) => ({
      code: l.code as string,
      label: l.label || (l.code as string),
      url: getActivityCaptionUrl(ids.orgUuid, ids.courseUuid, ids.activityUuid, l.code as string),
    }))
}

// ──────────────────────────────────────────────────────────────────────────
// Google Drive-stored videos
// ──────────────────────────────────────────────────────────────────────────

/**
 * Height (px) of the transparent strip laid over the top of the Drive embed.
 * Applied via an inline style in Video.tsx; this is the only place the number
 * lives.
 */
export const DRIVE_OVERLAY_HEIGHT_PX = 56

/**
 * Minimum height (px) of the Drive embed frame. Drive never lays its player out
 * shorter than 300px: in a shorter frame (a 16:9 box on a phone is ~190px) the
 * bottom is cropped, taking the controls and most of the picture with it.
 * Above ~534px wide the 16:9 box is already taller and this has no effect.
 */
export const DRIVE_PLAYER_MIN_HEIGHT_PX = 300

/**
 * Touch devices get Drive's touch player, whose layout depends on frame size:
 *   - wider than tall (every 16:9 size): the seek bar is pinned to the very top,
 *     under the strip above, and the play/pause row is cropped off the bottom;
 *   - near-square, ~410–500px wide: big seek bar and play/pause rows laid over
 *     the middle of the picture;
 *   - near-square, ≥ ~520px wide: one compact seek bar + button row BELOW the
 *     picture, nothing over it. 560px also fits the time display.
 * Its buttons are a fixed pixel size while the picture grows with the frame, so
 * on touch screens narrower than DRIVE_TOUCH_MAX_CONTAINER_PX the embed is laid
 * out at this size and scaled down to fit (≈0.6 on a phone, buttons ≈31px).
 */
export const DRIVE_TOUCH_FRAME_WIDTH_PX = 560
export const DRIVE_TOUCH_FRAME_HEIGHT_PX = 588
/** Container width (px) from which the touch layout is no longer applied. */
export const DRIVE_TOUCH_MAX_CONTAINER_PX = 640

export interface DriveTouchFrame {
  /** Size the iframe is laid out at, before scaling. */
  width: number
  height: number
  /** Factor that maps the laid-out frame onto the container width (≤ 1). */
  scale: number
  /** Height the container must take to show the scaled frame. */
  boxHeight: number
}

/**
 * Geometry of the Drive embed on a touch screen whose container is
 * `containerWidth` px wide. Null when the touch layout does not apply (no width
 * measured yet, or a container too wide for it).
 */
export function resolveDriveTouchFrame(containerWidth: number): DriveTouchFrame | null {
  if (!(containerWidth > 0) || containerWidth >= DRIVE_TOUCH_MAX_CONTAINER_PX) return null
  const ratio = DRIVE_TOUCH_FRAME_HEIGHT_PX / DRIVE_TOUCH_FRAME_WIDTH_PX
  const width = Math.max(containerWidth, DRIVE_TOUCH_FRAME_WIDTH_PX)
  const height = Math.round(width * ratio)
  const scale = containerWidth / width
  return { width, height, scale, boxHeight: height * scale }
}

/** True for a video activity whose file lives on the operator's Google Drive. */
export function isGDriveActivity(
  activity: { activity_sub_type?: string } | null | undefined
): boolean {
  return activity?.activity_sub_type === 'SUBTYPE_VIDEO_GDRIVE'
}

const DRIVE_FILE_ID_RE = /^[A-Za-z0-9_-]+$/

/**
 * The embed URL for a Drive-stored video, or null for anything that is not a
 * plain Drive file id. This is the ONLY place the client builds a Drive URL,
 * and the result may only ever be used as an iframe `src` — never as a link.
 */
export function resolveDrivePreviewUrl(fileId: string | null | undefined): string | null {
  if (typeof fileId !== 'string' || !DRIVE_FILE_ID_RE.test(fileId)) return null
  return `https://drive.google.com/file/d/${fileId}/preview`
}

/** Where a new video upload goes by default: Drive only once it is confirmed ready. */
export function defaultVideoStorage(
  status: GDriveStatus | null | undefined
): 'server' | 'gdrive' {
  return status?.ready === true ? 'gdrive' : 'server'
}
