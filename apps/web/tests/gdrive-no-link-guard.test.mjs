import { describe, expect, test } from 'bun:test'
import fs from 'node:fs'
import path from 'node:path'

/**
 * Guards the Google Drive video security invariants that neither TypeScript
 * nor ESLint can see (SC-008 / FR-009b):
 *
 *   - the client never renders a link to the Drive file (an <a href> would let
 *     a learner open the file outside the sandboxed embed);
 *   - the Drive host name is built in exactly one function, so there is one
 *     place to audit;
 *   - the player iframe is sandboxed without popups or top-navigation;
 *   - the strip laid over the embed's top bar is inert for assistive tech
 *     (FR-009a: no keyboard focus, hidden from screen readers) and takes its
 *     height from the single shared constant.
 */

const webRoot = path.resolve(import.meta.dirname, '..')
const read = (p) => fs.readFileSync(path.join(webRoot, p), 'utf8')

const DRIVE_HOST = 'drive.google.com'
const VIDEO_SOURCE = 'components/Objects/Activities/Video/videoSource.ts'

const GUARDED_FILES = [
  'components/Objects/Activities/Video/Video.tsx',
  'components/Objects/Activities/ActivityPreview/ActivityPreview.tsx',
  'components/Objects/Modals/Activities/Edit/EditVideoActivityModal.tsx',
  'components/Objects/Modals/Activities/Create/NewActivityModal/VideoActivityModal.tsx',
  VIDEO_SOURCE,
]

const countOccurrences = (haystack, needle) => haystack.split(needle).length - 1

describe('Drive video: no links to the file', () => {
  test('no href= whose value mentions the Drive host or webViewLink', () => {
    const offenders = []
    for (const file of GUARDED_FILES) {
      const src = read(file)
      // A JSX expression, a double-quoted or a single-quoted attribute value.
      const hrefs = [...src.matchAll(/href\s*=\s*(\{[^}]*\}|"[^"]*"|'[^']*')/g)]
      for (const m of hrefs) {
        if (m[1].includes(DRIVE_HOST) || m[1].includes('webViewLink')) {
          offenders.push(`${file}: ${m[0]}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  test('webViewLink is never referenced', () => {
    const offenders = GUARDED_FILES.filter((file) => read(file).includes('webViewLink'))
    expect(offenders).toEqual([])
  })
})

describe('Drive video: single URL builder', () => {
  test('the Drive host appears in no guarded file other than videoSource.ts', () => {
    const offenders = GUARDED_FILES.filter(
      (file) => file !== VIDEO_SOURCE && read(file).includes(DRIVE_HOST)
    )
    expect(offenders).toEqual([])
  })

  test('inside videoSource.ts the host only appears in resolveDrivePreviewUrl', () => {
    const src = read(VIDEO_SOURCE)
    const fn = src.match(
      /export function resolveDrivePreviewUrl\s*\([^)]*\)[^{]*\{[\s\S]*?\n\}/
    )
    expect(fn).not.toBeNull()
    const inFunction = countOccurrences(fn[0], DRIVE_HOST)
    const inFile = countOccurrences(src, DRIVE_HOST)
    expect(inFunction).toBeGreaterThan(0)
    expect(inFile).toBe(inFunction)
  })

  // The whole web tree, not just the five files above: a helper or a locale
  // string that starts carrying the host is exactly the drift this prevents.
  test('the Drive host appears nowhere else in apps/web', () => {
    const SKIP_DIRS = new Set(['node_modules', '.next', 'tests', '.git', '.turbo', 'coverage', 'out'])
    const TEXT_EXT = new Set([
      '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.json', '.md', '.mdx',
      '.css', '.scss', '.html', '.txt', '.yml', '.yaml', '.svg',
    ])
    const hits = []
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.isDirectory()) {
          if (!SKIP_DIRS.has(entry.name)) walk(path.join(dir, entry.name))
          continue
        }
        if (!entry.isFile() || !TEXT_EXT.has(path.extname(entry.name))) continue
        const full = path.join(dir, entry.name)
        if (fs.readFileSync(full, 'utf8').includes(DRIVE_HOST)) {
          hits.push(path.relative(webRoot, full))
        }
      }
    }
    walk(webRoot)
    expect(hits).toEqual([VIDEO_SOURCE])
  })
})

describe('Drive video: new UI strings go through i18n (FR-003)', () => {
  test('ActivityPreview does not hard-code the Google Drive label', () => {
    const src = read('components/Objects/Activities/ActivityPreview/ActivityPreview.tsx')
    // JSX text or a string literal both count as a hard-coded label.
    expect(src).not.toMatch(/>\s*Google Drive Video\b|["'`]Google Drive Video["'`]/)
    expect(src).toContain("t('activities.video_gdrive.label')")
  })

  test('EditVideoActivityModal Drive branch has no hard-coded replace hint', () => {
    const src = read('components/Objects/Modals/Activities/Edit/EditVideoActivityModal.tsx')
    expect(src).not.toMatch(/Leave empty to keep the current video/)
    expect(src).toContain("t('activities.video_gdrive.replace_hint')")
    for (const locale of ['en', 'vi']) {
      const json = JSON.parse(read(`locales/${locale}.json`))
      expect(typeof json.activities.video_gdrive.replace_hint).toBe('string')
      expect(json.activities.video_gdrive.replace_hint.length).toBeGreaterThan(0)
    }
  })
})

describe('Drive video: iframe sandbox', () => {
  const src = read('components/Objects/Activities/Video/Video.tsx')

  test('the player iframe is sandboxed without popups or top navigation', () => {
    const iframes = [...src.matchAll(/<iframe\b[\s\S]*?>/g)]
    expect(iframes.length).toBeGreaterThan(0)
    for (const m of iframes) {
      const sandbox = m[0].match(/sandbox="([^"]*)"/)
      expect(sandbox).not.toBeNull()
      const tokens = sandbox[1].split(/\s+/).filter(Boolean)
      expect(tokens).toContain('allow-scripts')
      expect(tokens).not.toContain('allow-popups')
      expect(tokens).not.toContain('allow-popups-to-escape-sandbox')
      expect(tokens).not.toContain('allow-top-navigation')
      expect(tokens).not.toContain('allow-top-navigation-by-user-activation')
    }
  })

  test('the iframe src comes from the single URL builder', () => {
    expect(src).toContain('resolveDrivePreviewUrl(')
    expect(src).not.toContain(DRIVE_HOST)
  })
})

describe('Drive video: overlay is inert for assistive tech (FR-009a)', () => {
  const src = read('components/Objects/Activities/Video/Video.tsx')
  // The overlay is the element whose height comes from the shared constant.
  const overlays = [...src.matchAll(/<div\b[^>]*?>/g)]
    .map((m) => m[0])
    .filter((tag) => tag.includes('DRIVE_OVERLAY_HEIGHT_PX'))

  test('there is exactly one overlay element', () => {
    expect(overlays).toHaveLength(1)
  })

  test('the overlay takes no keyboard focus and is hidden from screen readers', () => {
    expect(overlays[0]).toContain('aria-hidden="true"')
    expect(overlays[0]).toMatch(/tabIndex=\{\s*-1\s*\}/)
  })

  test('the overlay height is never hard-coded next to the constant', () => {
    expect(overlays[0]).not.toMatch(/height:\s*['"]?\d/)
  })
})
