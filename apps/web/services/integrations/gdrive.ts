import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader } from '@services/utils/ts/requests'

/**
 * Google Drive video storage — client side of the integration.
 *
 * The backend reports whether the operator's Drive integration is usable via
 * `GET /integrations/gdrive/status`. Anything that is not a well-formed,
 * successful answer is treated as "not ready", so the Drive option only ever
 * appears once the server has positively confirmed it works.
 */

export type GDriveStatus = {
  enabled: boolean
  ready: boolean
  reason: string | null
}

function failClosed(): GDriveStatus {
  return { enabled: false, ready: false, reason: 'network_error' }
}

/** Pure: coerce an arbitrary JSON body into a GDriveStatus, failing closed. */
export function parseGDriveStatus(json: unknown): GDriveStatus {
  if (!json || typeof json !== 'object' || Array.isArray(json)) return failClosed()
  const body = json as Record<string, unknown>
  if (typeof body.enabled !== 'boolean' || typeof body.ready !== 'boolean') {
    return failClosed()
  }
  const rawReason = body.reason
  const reason: string | null =
    typeof rawReason === 'string' || rawReason === null
      ? rawReason
      : body.ready
        ? null
        : 'network_error'
  return { enabled: body.enabled, ready: body.ready, reason }
}

export async function getGDriveStatus(accessToken: string): Promise<GDriveStatus> {
  try {
    const result = await fetch(
      `${getAPIUrl()}integrations/gdrive/status`,
      RequestBodyWithAuthHeader('GET', null, null, accessToken)
    )
    if (result.status !== 200) return failClosed()
    const json: unknown = await result.json()
    return parseGDriveStatus(json)
  } catch {
    return failClosed()
  }
}
