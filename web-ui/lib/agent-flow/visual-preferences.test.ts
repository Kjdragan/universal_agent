import { describe, expect, it } from 'vitest'
import {
  DEFAULT_VISUAL_PREFERENCES,
  normalizeVisualPreferences,
} from './visual-preferences'

describe('visual preferences', () => {
  it('normalizes missing and invalid values to safe defaults', () => {
    expect(normalizeVisualPreferences({
      textDisplayMode: 'giant',
      replayPacingMode: 'slow',
      textScale: 99,
      readableHoldMultiplier: -2,
      thinkingDisplay: 'verbose',
      autoFadeText: 'yes',
      pinOnHover: 'no',
    })).toEqual({
      ...DEFAULT_VISUAL_PREFERENCES,
      textScale: 1.3,
      readableHoldMultiplier: 0.5,
    })
  })

  it('preserves supported choices', () => {
    expect(normalizeVisualPreferences({
      textDisplayMode: 'inline',
      replayPacingMode: 'dramatic',
      textScale: 1.15,
      readableHoldMultiplier: 1.5,
      thinkingDisplay: 'bubbles',
      autoFadeText: false,
      pinOnHover: false,
    })).toEqual({
      textDisplayMode: 'inline',
      replayPacingMode: 'dramatic',
      textScale: 1.15,
      readableHoldMultiplier: 1.5,
      thinkingDisplay: 'bubbles',
      autoFadeText: false,
      pinOnHover: false,
    })
  })
})
