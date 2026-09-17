import { describe, expect, test } from 'bun:test'

import {
  DRIVE_OVERLAY_HEIGHT_PX,
  defaultVideoStorage,
  isGDriveActivity,
  resolveDrivePreviewUrl,
} from '../components/Objects/Activities/Video/videoSource.ts'
import { parseGDriveStatus } from '../services/integrations/gdrive.ts'
import {
  buildGDriveVideoUpdateEntries,
  buildVideoFormFields,
} from '../services/courses/activities.ts'

const FAIL_CLOSED = { enabled: false, ready: false, reason: 'network_error' }

describe('isGDriveActivity', () => {
  test('true only for SUBTYPE_VIDEO_GDRIVE', () => {
    expect(isGDriveActivity({ activity_sub_type: 'SUBTYPE_VIDEO_GDRIVE' })).toBe(true)
  })

  test('false for hosted / youtube / missing subtype', () => {
    expect(isGDriveActivity({ activity_sub_type: 'SUBTYPE_VIDEO_HOSTED' })).toBe(false)
    expect(isGDriveActivity({ activity_sub_type: 'SUBTYPE_VIDEO_YOUTUBE' })).toBe(false)
    expect(isGDriveActivity({})).toBe(false)
  })

  test('false for null / undefined', () => {
    expect(isGDriveActivity(null)).toBe(false)
    expect(isGDriveActivity(undefined)).toBe(false)
  })
})

describe('resolveDrivePreviewUrl', () => {
  test('builds the exact preview URL for a plain file id', () => {
    expect(resolveDrivePreviewUrl('1aB_c-D9')).toBe(
      'https://drive.google.com/file/d/1aB_c-D9/preview'
    )
  })

  test('null for anything that is not a bare file id', () => {
    expect(resolveDrivePreviewUrl('')).toBeNull()
    expect(resolveDrivePreviewUrl('../x')).toBeNull()
    expect(resolveDrivePreviewUrl('a?b')).toBeNull()
    expect(resolveDrivePreviewUrl('a b')).toBeNull()
    expect(resolveDrivePreviewUrl('a/b')).toBeNull()
    expect(resolveDrivePreviewUrl(undefined)).toBeNull()
    expect(resolveDrivePreviewUrl(null)).toBeNull()
  })
})

describe('defaultVideoStorage', () => {
  test("'gdrive' only when the integration is ready", () => {
    expect(defaultVideoStorage({ enabled: true, ready: true, reason: null })).toBe('gdrive')
  })

  test("'server' when enabled but not ready, or with no status at all", () => {
    expect(
      defaultVideoStorage({ enabled: true, ready: false, reason: 'token_missing' })
    ).toBe('server')
    expect(defaultVideoStorage(null)).toBe('server')
    expect(defaultVideoStorage(undefined)).toBe('server')
  })
})

describe('parseGDriveStatus', () => {
  test('passes a valid payload through', () => {
    expect(parseGDriveStatus({ enabled: true, ready: true, reason: null })).toEqual({
      enabled: true,
      ready: true,
      reason: null,
    })
    expect(
      parseGDriveStatus({ enabled: true, ready: false, reason: 'needs_reauthorization' })
    ).toEqual({ enabled: true, ready: false, reason: 'needs_reauthorization' })
  })

  test('fails closed when fields are missing', () => {
    expect(parseGDriveStatus({})).toEqual(FAIL_CLOSED)
    expect(parseGDriveStatus({ enabled: true })).toEqual(FAIL_CLOSED)
    expect(parseGDriveStatus({ ready: true })).toEqual(FAIL_CLOSED)
  })

  test('fails closed for non-objects', () => {
    expect(parseGDriveStatus(null)).toEqual(FAIL_CLOSED)
    expect(parseGDriveStatus(undefined)).toEqual(FAIL_CLOSED)
    expect(parseGDriveStatus('ready')).toEqual(FAIL_CLOSED)
    expect(parseGDriveStatus(42)).toEqual(FAIL_CLOSED)
    expect(parseGDriveStatus([true, true])).toEqual(FAIL_CLOSED)
  })

  test('fails closed when enabled/ready have the wrong type', () => {
    expect(parseGDriveStatus({ enabled: 'true', ready: true, reason: null })).toEqual(FAIL_CLOSED)
    expect(parseGDriveStatus({ enabled: true, ready: 1, reason: null })).toEqual(FAIL_CLOSED)
  })

  test('normalises a malformed reason', () => {
    // Ready with a junk reason: the reason is irrelevant, drop it.
    expect(parseGDriveStatus({ enabled: true, ready: true, reason: 7 })).toEqual({
      enabled: true,
      ready: true,
      reason: null,
    })
    expect(parseGDriveStatus({ enabled: true, ready: true })).toEqual({
      enabled: true,
      ready: true,
      reason: null,
    })
    // Not ready and no usable reason: report it as a transport problem.
    expect(parseGDriveStatus({ enabled: true, ready: false, reason: { x: 1 } })).toEqual({
      enabled: true,
      ready: false,
      reason: 'network_error',
    })
  })
})

describe('DRIVE_OVERLAY_HEIGHT_PX', () => {
  test('is 56', () => {
    expect(DRIVE_OVERLAY_HEIGHT_PX).toBe(56)
  })
})

describe('buildVideoFormFields', () => {
  const details = { startTime: 5, endTime: 90, autoplay: true, muted: false }
  const keys = (fields) => fields.map(([k]) => k)

  test('gdrive: sends storage and never details, even when details are given', () => {
    const fields = buildVideoFormFields({
      name: 'Lecture',
      chapterId: 12,
      storage: 'gdrive',
      details,
    })
    expect(fields).toContainEqual(['storage', 'gdrive'])
    expect(fields).toContainEqual(['chapter_id', '12'])
    expect(fields).toContainEqual(['name', 'Lecture'])
    expect(keys(fields)).not.toContain('details')
  })

  test('server: sends storage and the normalised details JSON', () => {
    const fields = buildVideoFormFields({
      name: 'Lecture',
      chapterId: '12',
      storage: 'server',
      details,
    })
    expect(fields).toContainEqual(['storage', 'server'])
    const entry = fields.find(([k]) => k === 'details')
    expect(entry).toBeDefined()
    expect(JSON.parse(entry[1])).toEqual({
      startTime: 5,
      endTime: 90,
      autoplay: true,
      muted: false,
    })
  })

  test('details normalisation matches the legacy behaviour', () => {
    const fields = buildVideoFormFields({
      name: 'x',
      chapterId: 1,
      details: { startTime: undefined, endTime: 0, autoplay: undefined, muted: undefined },
    })
    const entry = fields.find(([k]) => k === 'details')
    expect(JSON.parse(entry[1])).toEqual({
      startTime: 0,
      endTime: null,
      autoplay: false,
      muted: false,
    })
  })

  test('no storage field when storage is not provided; no details when absent', () => {
    const fields = buildVideoFormFields({ name: 'x', chapterId: 1 })
    expect(fields).toEqual([
      ['chapter_id', '1'],
      ['name', 'x'],
    ])
  })

  test('extra_metadata only when provided', () => {
    const fields = buildVideoFormFields({
      name: 'x',
      chapterId: 1,
      extraMetadata: { source: 'import' },
    })
    expect(fields).toContainEqual(['extra_metadata', JSON.stringify({ source: 'import' })])
    expect(keys(buildVideoFormFields({ name: 'x', chapterId: 1 }))).not.toContain(
      'extra_metadata'
    )
  })
})

describe('buildGDriveVideoUpdateEntries', () => {
  const PLAYBACK = ['autoplay', 'muted', 'start_time', 'end_time', 'details']
  const file = new Blob(['00000'], { type: 'video/mp4' })

  test('the name and the replacement file, when both are given', () => {
    const entries = buildGDriveVideoUpdateEntries({ name: 'Renamed', videoFile: file })
    expect(entries.map(([k]) => k)).toEqual(['name', 'video_file'])
    expect(entries[0][1]).toBe('Renamed')
    expect(entries[1][1]).toBe(file)
  })

  test('only the name when there is no file', () => {
    expect(buildGDriveVideoUpdateEntries({ name: 'Renamed' })).toEqual([['name', 'Renamed']])
    expect(buildGDriveVideoUpdateEntries({ name: 'Renamed', videoFile: null })).toEqual([
      ['name', 'Renamed'],
    ])
  })

  test('only the file when the name is absent or empty', () => {
    expect(buildGDriveVideoUpdateEntries({ videoFile: file })).toEqual([['video_file', file]])
    expect(buildGDriveVideoUpdateEntries({ name: '', videoFile: file })).toEqual([
      ['video_file', file],
    ])
  })

  test('nothing when neither is given', () => {
    expect(buildGDriveVideoUpdateEntries({})).toEqual([])
  })

  test('never emits playback fields', () => {
    for (const args of [
      { name: 'n', videoFile: file },
      { name: undefined, videoFile: null },
    ]) {
      const keys = buildGDriveVideoUpdateEntries(args).map(([k]) => k)
      for (const forbidden of PLAYBACK) expect(keys).not.toContain(forbidden)
    }
  })
})
