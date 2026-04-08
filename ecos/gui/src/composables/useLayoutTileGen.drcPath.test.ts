import { describe, it, expect } from 'vitest'
import { deriveDrcStepPathFromLayoutJsonRelative } from '@/composables/useLayoutTileGen'

describe('deriveDrcStepPathFromLayoutJsonRelative', () => {
  it('maps output/ layout dir to feature/drc.step.json', () => {
    expect(deriveDrcStepPathFromLayoutJsonRelative('templates/t18/drc_ecc/output/cell.json')).toBe(
      'templates/t18/drc_ecc/feature/drc.step.json',
    )
  })

  it('keeps same-dir drc when layout is already under feature/', () => {
    expect(deriveDrcStepPathFromLayoutJsonRelative('drc_ecc/feature/layout.json')).toBe(
      'drc_ecc/feature/drc.step.json',
    )
  })

  it('handles top-level output/', () => {
    expect(deriveDrcStepPathFromLayoutJsonRelative('output/foo.json')).toBe('feature/drc.step.json')
  })
})
