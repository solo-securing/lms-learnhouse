import { describe, expect, test } from 'bun:test'
import fs from 'node:fs'
import path from 'node:path'

import {
  DRIVE_TOUCH_FRAME_WIDTH_PX,
  DRIVE_TOUCH_MAX_CONTAINER_PX,
  resolveDriveTouchFrame,
} from '../components/Objects/Activities/Video/videoSource.ts'

/**
 * Guards the phone-width video fixes (bug: mobile-video-overlap) that neither
 * TypeScript nor ESLint can see:
 *
 *   - the video.js player runs in responsive mode, so it gets vjs-layout-*
 *     classes and drops controls instead of clipping fullscreen off the bar;
 *   - on narrow layouts the progress bar and fullscreen stay usable;
 *   - the Drive embed frame is never shorter than Drive's own player;
 *   - the fixed activity bar does not cover the player below sm;
 *   - the closed mobile menu cannot swallow taps.
 */

const webRoot = path.resolve(import.meta.dirname, '..')
const read = (p) => fs.readFileSync(path.join(webRoot, p), 'utf8')

const PLAYER = 'components/Objects/Activities/Video/LearnHousePlayer.tsx'
const PLAYER_CSS = 'components/Objects/Activities/Video/player-controls.css'
const VIDEO = 'components/Objects/Activities/Video/Video.tsx'
const VIDEO_SOURCE = 'components/Objects/Activities/Video/videoSource.ts'
const SECONDARY_BAR = 'components/Pages/Activity/FixedActivitySecondaryBar.tsx'
const ORG_MENU = 'components/Objects/Menus/OrgMenu.tsx'

// Every CSS rule as { selectors, body }, comments stripped.
const rules = (css) =>
  [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^}]*)\}/g)].map((m) => ({
    selectors: m[1].split(',').map((s) => s.trim()),
    body: m[2],
  }))

const hiddenSelectors = (css) =>
  rules(css)
    .filter((r) => /display\s*:\s*none/.test(r.body))
    .flatMap((r) => r.selectors)

describe('video player: phone-width controls', () => {
  test('video.js is created with responsive: true', () => {
    expect(read(PLAYER)).toMatch(/responsive:\s*true/)
  })

  test('fullscreen and play are never hidden by our narrow-layout rules', () => {
    const hidden = hiddenSelectors(read(PLAYER_CSS))
    const offenders = hidden.filter(
      (s) => s.includes('vjs-fullscreen-control') || s.includes('vjs-play-control')
    )
    expect(offenders).toEqual([])
  })

  test('PiP and the volume panel are dropped on small and x-small layouts', () => {
    const hidden = hiddenSelectors(read(PLAYER_CSS))
    for (const layout of ['vjs-layout-small', 'vjs-layout-x-small']) {
      for (const control of ['vjs-picture-in-picture-control', 'vjs-volume-panel']) {
        expect(hidden.some((s) => s.includes(layout) && s.includes(control))).toBe(true)
      }
    }
  })

  test('x-small keeps the progress bar that stock video.js hides', () => {
    const shown = rules(read(PLAYER_CSS)).filter(
      (r) =>
        r.selectors.some((s) => s.includes('vjs-layout-x-small') && s.includes('vjs-progress-control')) &&
        /display\s*:\s*flex/.test(r.body)
    )
    expect(shown.length).toBeGreaterThan(0)
  })
})

describe('Drive embed: never shorter than Drive lays its player out', () => {
  // Drive's inner player is at least 300px tall; a shorter frame crops its
  // bottom, controls included (measured: a 335x188 frame holds a 335x300 player).
  test('the minimum height constant is 300px', () => {
    expect(read(VIDEO_SOURCE)).toMatch(/export const DRIVE_PLAYER_MIN_HEIGHT_PX = 300\b/)
  })

  test('the Drive frame applies it as its min-height', () => {
    const src = read(VIDEO)
    const embed = src.slice(src.indexOf('function DriveEmbed'), src.indexOf('function VideoActivity'))
    expect(embed).toMatch(/minHeight:\s*DRIVE_PLAYER_MIN_HEIGHT_PX/)
  })
})

describe('Drive embed on touch screens: near-square frame, scaled to fit', () => {
  // Drive's touch player pins its seek bar under the top strip and crops the
  // play/pause row in any frame wider than tall, lays big controls over the
  // picture below ~520px of width, and moves one compact control row below the
  // picture from ~520px. Measured on the real embed with an iPhone profile.
  test('a phone-width container gets a near-square frame wide enough for the compact controls', () => {
    expect(DRIVE_TOUCH_FRAME_WIDTH_PX).toBeGreaterThanOrEqual(520)
    for (const w of [300, 334, 375, 559]) {
      const f = resolveDriveTouchFrame(w)
      expect(f.width).toBe(DRIVE_TOUCH_FRAME_WIDTH_PX)
      expect(f.height).toBeGreaterThanOrEqual(f.width)
      expect(f.scale).toBeCloseTo(w / DRIVE_TOUCH_FRAME_WIDTH_PX, 6)
      // The scaled frame fills the container width exactly.
      expect(f.width * f.scale).toBeCloseTo(w, 6)
      expect(f.boxHeight).toBeCloseTo(f.height * f.scale, 6)
    }
  })

  test('a container wider than the frame is laid out at its own width, unscaled', () => {
    const f = resolveDriveTouchFrame(600)
    expect(f.width).toBe(600)
    expect(f.scale).toBe(1)
    expect(f.height).toBeGreaterThanOrEqual(600)
  })

  test('no touch frame before a width is measured or from the sm breakpoint up', () => {
    expect(resolveDriveTouchFrame(0)).toBeNull()
    expect(resolveDriveTouchFrame(Number.NaN)).toBeNull()
    expect(resolveDriveTouchFrame(DRIVE_TOUCH_MAX_CONTAINER_PX)).toBeNull()
    expect(resolveDriveTouchFrame(1024)).toBeNull()
  })

  test('the touch layout is gated on a coarse pointer, and the strip scales with the frame', () => {
    const src = read(VIDEO)
    const embed = src.slice(src.indexOf('function DriveEmbed'), src.indexOf('function VideoActivity'))
    expect(embed).toMatch(/useMediaQuery\('\(pointer: coarse\)'/)
    expect(embed).toMatch(/isTouch \? resolveDriveTouchFrame\(width\) : null/)
    // The strip must live inside the scaled layer (after the transform is set),
    // or it would stop covering Drive's top bar once the frame is scaled.
    const transformAt = embed.indexOf('transform: `scale(')
    const stripAt = embed.indexOf('height: DRIVE_OVERLAY_HEIGHT_PX')
    expect(transformAt).toBeGreaterThan(-1)
    expect(stripAt).toBeGreaterThan(transformAt)
  })
})

describe('activity page: nothing fixed over the player on phones', () => {
  test('the fixed secondary bar is hidden below sm', () => {
    const src = read(SECONDARY_BAR)
    const fixedBar = src.match(/className=\{`([^`]*fixed top-\[60px\][^`]*)`/)
    expect(fixedBar).not.toBeNull()
    expect(fixedBar[1]).toMatch(/(^|\s)hidden\s+sm:block(\s|$)/)
  })

  test('the closed mobile menu is invisible and ignores pointer events', () => {
    const src = read(ORG_MENU)
    const closed = src.match(/isMenuOpen \? 'opacity-100' : '([^']*)'/)
    expect(closed).not.toBeNull()
    expect(closed[1]).toContain('invisible')
    expect(closed[1]).toContain('pointer-events-none')
  })
})
