import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { LineChart } from './LineChart'

const blue = '#58a6ff'

describe('LineChart', () => {
  it('says so instead of drawing an empty box when nothing is measurable', () => {
    render(
      <LineChart
        ariaLabel="trống"
        labels={['1', '2']}
        series={[{ name: 'IS', color: blue, values: [null, undefined] }]}
        emptyText="Chưa có checkpoint"
      />,
    )
    expect(screen.getByText('Chưa có checkpoint')).toBeTruthy()
    expect(document.querySelector('svg')).toBeNull()
  })

  it('breaks the line at a gap rather than drawing through it as 0', () => {
    const { container } = render(
      <LineChart
        ariaLabel="có khoảng trống"
        labels={['1', '2', '3', '4']}
        series={[{ name: 'IS', color: blue, values: [1, null, 2, 3] }]}
      />,
    )
    const lines = container.querySelectorAll('polyline')
    expect(lines.length).toBe(1)
    expect(container.querySelectorAll('circle').length).toBe(1)
    expect(lines[0]!.getAttribute('points')).not.toContain('NaN')
  })

  it('draws axis ticks so the values stay readable when zoomed', () => {
    const { container } = render(
      <LineChart
        ariaLabel="Sharpe theo checkpoint"
        labels={['32', '64', '96']}
        series={[{ name: 'IS', color: blue, values: [1, 1.5, 2] }]}
      />,
    )
    expect(container.querySelectorAll('text').length).toBeGreaterThanOrEqual(6)
    expect(screen.getByRole('img', { name: 'Sharpe theo checkpoint' })).toBeTruthy()
    expect(screen.getByText('32')).toBeTruthy()
    expect(screen.getByText('96')).toBeTruthy()
  })

  it('keeps a flat series on the chart instead of dividing by a zero range', () => {
    const { container } = render(
      <LineChart
        ariaLabel="phẳng"
        labels={['1', '2']}
        series={[{ name: 'IS', color: blue, values: [2, 2] }]}
      />,
    )
    expect(container.querySelector('polyline')!.getAttribute('points')).not.toContain('NaN')
  })
})
