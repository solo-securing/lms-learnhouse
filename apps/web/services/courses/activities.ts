import { getAPIUrl } from '@services/config/config'
import {
  RequestBodyFormWithAuthHeader,
  RequestBodyWithAuthHeader,
  getResponseMetadata,
} from '@services/utils/ts/requests'

export async function createActivity(
  data: any,
  chapter_id: any,
  org_id: any,
  access_token: string
) {
  data.content = data.content || {}
  // remove chapter_id from data
  delete data.chapterId

  const result = await fetch(
    `${getAPIUrl()}activities/?coursechapter_id=${chapter_id}&org_id=${org_id}`,
    RequestBodyWithAuthHeader('POST', data, null, access_token)
  )
  const res = await result.json()
  return res
}

/**
 * Upload a hosted-video activity with real upload progress (XHR — fetch can't
 * report upload progress). Resolves with the created activity JSON. Used by the
 * background-upload flow so the modal can close immediately.
 */
export type VideoStorage = 'server' | 'gdrive'

export interface VideoFormFieldsArgs {
  name: string
  chapterId: string | number
  storage?: VideoStorage
  details?: {
    startTime?: number
    endTime?: number | null
    autoplay?: boolean
    muted?: boolean
  } | null
  extraMetadata?: Record<string, unknown> | null
}

/**
 * Pure: the multipart fields (excluding the file itself) for POST
 * /activities/video. Playback `details` only apply to server-hosted videos —
 * a Google Drive upload ignores them server-side, so they are never sent.
 */
export function buildVideoFormFields(args: VideoFormFieldsArgs): Array<[string, string]> {
  const fields: Array<[string, string]> = [
    ['chapter_id', String(args.chapterId)],
    ['name', args.name],
  ]
  if (args.storage) fields.push(['storage', args.storage])
  if (args.storage !== 'gdrive' && args.details) {
    fields.push([
      'details',
      JSON.stringify({
        startTime: args.details.startTime || 0,
        endTime: args.details.endTime || null,
        autoplay: args.details.autoplay || false,
        muted: args.details.muted || false,
      }),
    ])
  }
  if (args.extraMetadata) {
    fields.push(['extra_metadata', JSON.stringify(args.extraMetadata)])
  }
  return fields
}

/**
 * Pure: the multipart entries for PUT /activities/video/{uuid} on a Google
 * Drive activity — only `name` and the replacement `video_file`. Playback
 * settings (start/end/autoplay/muted) belong to hosted videos and are never
 * sent.
 */
export function buildGDriveVideoUpdateEntries(args: {
  name?: string
  videoFile?: Blob | null
}): Array<[string, string | Blob]> {
  const entries: Array<[string, string | Blob]> = []
  if (typeof args.name === 'string' && args.name !== '') entries.push(['name', args.name])
  if (args.videoFile) entries.push(['video_file', args.videoFile])
  return entries
}

function normalizeVideoStorage(value: unknown): VideoStorage | undefined {
  return value === 'gdrive' || value === 'server' ? value : undefined
}

export function createVideoActivityWithProgress(
  file: File,
  data: any,
  chapter_id: any,
  access_token: string,
  onProgress: (_percent: number) => void,
  onUploaded?: () => void
): Promise<any> {
  const formData = new FormData()
  for (const [key, value] of buildVideoFormFields({
    name: data.name,
    chapterId: chapter_id,
    storage: normalizeVideoStorage(data.storage),
    details: data.details,
  })) {
    formData.append(key, value)
  }
  formData.append('video_file', file)
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${getAPIUrl()}activities/video`)
    xhr.withCredentials = true
    xhr.setRequestHeader('Authorization', `Bearer ${access_token}`)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress((e.loaded / e.total) * 100)
    }
    // The browser has finished sending the body. The server may still be busy
    // (e.g. relaying the file to Google Drive) before it answers.
    xhr.upload.onload = () => onUploaded?.()
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch {
          resolve({})
        }
      } else if (xhr.status === 413) {
        reject(new Error('The file is too large to upload.'))
      } else {
        let detail: string | undefined
        try {
          detail = JSON.parse(xhr.responseText)?.detail
        } catch {
          /* non-JSON error body */
        }
        reject(new Error(detail || `Upload failed (HTTP ${xhr.status})`))
      }
    }
    xhr.onerror = () => reject(new Error('Network error during upload'))
    xhr.onabort = () => reject(new Error('Upload cancelled'))
    xhr.send(formData)
  })
}

export async function createFileActivity(
  file: File,
  type: string,
  data: any,
  chapter_id: any,
  access_token: string
) {
  // Send file thumbnail as form data
  const formData = new FormData()

  let endpoint = ''

  if (type === 'video') {
    for (const [key, value] of buildVideoFormFields({
      name: data.name,
      chapterId: chapter_id,
      storage: normalizeVideoStorage(data.storage),
      details: data.details,
    })) {
      formData.append(key, value)
    }
    formData.append('video_file', file)
    endpoint = `${getAPIUrl()}activities/video`
  } else if (type === 'documentpdf') {
    formData.append('chapter_id', chapter_id)
    formData.append('pdf_file', file)
    formData.append('name', data.name)
    endpoint = `${getAPIUrl()}activities/documentpdf`
  } else {
    formData.append('chapter_id', chapter_id)
    // Handle other file types here
  }

  const result: any = await fetch(
    endpoint,
    RequestBodyFormWithAuthHeader('POST', formData, null, access_token)
  )
  if (!result.ok) {
    // A too-large body is rejected by the reverse proxy (nginx) with a 413
    // and an HTML error page — not JSON — so surface a clear error instead of
    // letting `result.json()` blow up with "Unexpected token '<'".
    if (result.status === 413) {
      throw new Error('The file is too large to upload.')
    }
    const detail = await result.json().catch(() => null)
    throw new Error(detail?.detail || `Upload failed (HTTP ${result.status})`)
  }
  const res = await result.json()
  return res
}

export async function createExternalVideoActivity(
  data: any,
  activity: any,
  chapter_id: any,
  access_token: string
) {
  // add coursechapter_id to data
  data.chapter_id = String(chapter_id)
  data.activity_id = activity.id
  
  // Add video details with null checking
  const defaultDetails = {
    startTime: 0,
    endTime: null,
    autoplay: false,
    muted: false
  }

  const videoDetails = data.details ? {
    startTime: data.details.startTime ?? defaultDetails.startTime,
    endTime: data.details.endTime ?? defaultDetails.endTime,
    autoplay: data.details.autoplay ?? defaultDetails.autoplay,
    muted: data.details.muted ?? defaultDetails.muted
  } : defaultDetails

  data.details = JSON.stringify(videoDetails)

  const result = await fetch(
    `${getAPIUrl()}activities/external_video`,
    RequestBodyWithAuthHeader('POST', data, null, access_token)
  )
  const res = await result.json()
  return res
}

export async function getActivity(
  activity_uuid: any,
  next: any,
  access_token: string
) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}`,
    RequestBodyWithAuthHeader('GET', null, next, access_token)
  )
  const res = await result.json()
  return res
}

export async function getActivityByID(
  activity_id: any,
  next: any,
  access_token: string
) {
  const result = await fetch(
    `${getAPIUrl()}activities/id/${activity_id}`,
    RequestBodyWithAuthHeader('GET', null, next, access_token)
  )
  const res = await result.json()
  return res
}

export async function deleteActivity(activity_uuid: any, access_token: string) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}`,
    RequestBodyWithAuthHeader('DELETE', null, null, access_token)
  )
  const res = await result.json()
  return res
}

export async function getActivityWithAuthHeader(
  activity_uuid: any,
  next: any,
  access_token: string | null | undefined
) {
  const result = await fetch(
    `${getAPIUrl()}activities/activity_${activity_uuid}`,
    RequestBodyWithAuthHeader('GET', null, next, access_token || undefined)
  )
  const res = await result.json()
  return res
}

export async function updateActivity(
  data: any,
  activity_uuid: string,
  access_token: string
) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}`,
    RequestBodyWithAuthHeader('PUT', data, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getActivityUserGroups(
  activity_uuid: string,
  access_token: string
) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}/usergroups`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  return result.json()
}

export async function addUserGroupToActivity(
  activity_uuid: string,
  usergroup_uuid: string,
  access_token: string
) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}/usergroups/${usergroup_uuid}`,
    RequestBodyWithAuthHeader('POST', null, null, access_token)
  )
  return result.json()
}

export async function removeUserGroupFromActivity(
  activity_uuid: string,
  usergroup_uuid: string,
  access_token: string
) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}/usergroups/${usergroup_uuid}`,
    RequestBodyWithAuthHeader('DELETE', null, null, access_token)
  )
  return result.json()
}

export async function updateHostedVideoActivity(
  activityUuid: string,
  videoDetails: { startTime: number; endTime: number | null; autoplay: boolean; muted: boolean },
  access_token: string,
  name?: string,
  videoFile?: File | null,
) {
  const formData = new FormData()
  if (name) formData.append('name', name)
  formData.append('start_time', String(videoDetails.startTime))
  if (videoDetails.endTime !== null) formData.append('end_time', String(videoDetails.endTime))
  formData.append('autoplay', String(videoDetails.autoplay))
  formData.append('muted', String(videoDetails.muted))
  if (videoFile) formData.append('video_file', videoFile)
  const result = await fetch(
    `${getAPIUrl()}activities/video/${activityUuid}`,
    RequestBodyFormWithAuthHeader('PUT', formData, null, access_token)
  )
  return getResponseMetadata(result)
}

/**
 * Update a Google Drive video activity: rename and/or replace the file. The
 * playback fields of the hosted-video endpoint are deliberately never sent.
 */
export async function updateGDriveVideoActivity(
  activityUuid: string,
  args: { name?: string; videoFile?: File | null },
  access_token: string
) {
  const formData = new FormData()
  for (const [key, value] of buildGDriveVideoUpdateEntries({
    name: args.name,
    videoFile: args.videoFile,
  })) {
    formData.append(key, value)
  }
  const result = await fetch(
    `${getAPIUrl()}activities/video/${activityUuid}`,
    RequestBodyFormWithAuthHeader('PUT', formData, null, access_token)
  )
  return getResponseMetadata(result)
}

export interface CaptionsConfigPayload {
  enabled: boolean
  source_language: string
  languages: { code: string; label?: string }[]
}

/** Enable/disable AI captions + choose target languages (queues generation). */
export async function updateVideoCaptions(
  activityUuid: string,
  config: CaptionsConfigPayload,
  access_token: string,
) {
  const result = await fetch(
    `${getAPIUrl()}activities/video/${activityUuid}/captions`,
    RequestBodyWithAuthHeader('POST', config, null, access_token)
  )
  return getResponseMetadata(result)
}

export async function updateExternalVideoActivity(
  activityUuid: string,
  youtubeUrl: string,
  videoDetails: { startTime: number; endTime: number | null; autoplay: boolean; muted: boolean },
  access_token: string,
  name?: string,
) {
  const result = await fetch(
    `${getAPIUrl()}activities/external_video/${activityUuid}`,
    RequestBodyWithAuthHeader(
      'PUT',
      {
        name,
        uri: youtubeUrl,
        startTime: videoDetails.startTime,
        endTime: videoDetails.endTime,
        autoplay: videoDetails.autoplay,
        muted: videoDetails.muted,
      },
      null,
      access_token
    )
  )
  return getResponseMetadata(result)
}

export async function getUrlPreview(url: string) {
  const result = await fetch(
    `${getAPIUrl()}utils/link-preview?url=${encodeURIComponent(url)}`,
    RequestBodyWithAuthHeader('GET', null, null, undefined)
  )
  const res = await result.json()
  return res
}

// Versioning API functions

export async function getActivityVersions(
  activity_uuid: string,
  access_token: string,
  limit: number = 20,
  offset: number = 0
) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}/versions?limit=${limit}&offset=${offset}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await result.json()
  return res
}

export async function getActivityVersion(
  activity_uuid: string,
  version_number: number,
  access_token: string
) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}/versions/${version_number}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await result.json()
  return res
}

export async function getActivityState(
  activity_uuid: string,
  access_token: string
) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}/state`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await result.json()
  return res
}

export async function restoreActivityVersion(
  activity_uuid: string,
  version_number: number,
  access_token: string
) {
  const result = await fetch(
    `${getAPIUrl()}activities/${activity_uuid}/versions/${version_number}/restore`,
    RequestBodyWithAuthHeader('POST', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function updateDocumentActivity(
  activityUuid: string,
  access_token: string,
  name?: string,
  pdfFile?: File | null,
) {
  const formData = new FormData()
  if (name) formData.append('name', name)
  if (pdfFile) formData.append('pdf_file', pdfFile)
  const result = await fetch(
    `${getAPIUrl()}activities/documentpdf/${activityUuid}`,
    RequestBodyFormWithAuthHeader('PUT', formData, null, access_token)
  )
  return getResponseMetadata(result)
}
