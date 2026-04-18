import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DEFAULT_VISUAL_PREFERENCES } from '@/lib/agent-flow/visual-preferences'
import { VisualsPanel } from './visuals-panel'

describe('VisualsPanel', () => {
  it('updates controls live through onChange callbacks', () => {
    const onChange = vi.fn()
    render(
      <VisualsPanel
        visible={true}
        preferences={DEFAULT_VISUAL_PREFERENCES}
        onChange={onChange}
        onReset={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'inline' }))
    expect(onChange).toHaveBeenCalledWith({ textDisplayMode: 'inline' })

    fireEvent.click(screen.getByRole('button', { name: 'dramatic' }))
    expect(onChange).toHaveBeenCalledWith({ replayPacingMode: 'dramatic' })
  })
})
