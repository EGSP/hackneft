import { Card, Space, Typography } from 'antd'
import dayjs from 'dayjs'
import * as echarts from 'echarts'
import { useEffect, useMemo, useRef } from 'react'

import type { SulfurPoint } from './SulfurChart'
import type { Range } from './timeWindow'

interface Props {
  title: string
  unit: string
  color: string
  points: SulfurPoint[]
  view: Range
  loading: boolean
}

const MAX_GAP = 15 * 60_000
const FRESH_FOR = 30 * 60_000
const valid = (value: number) => Number.isFinite(value) && value >= 0 && value !== 307

/** Один компактный ряд. Периодом и масштабом управляет общий график серы. */
export function ProcessChart({ title, unit, color, points, view, loading }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const visible = useMemo(
    () => points.filter(([at]) => at >= view[0] && at <= view[1]),
    [points, view],
  )
  const last = visible.at(-1)
  const lastValue = last && valid(last[1]) && view[1] - last[0] <= FRESH_FOR ? last[1] : null
  const hasData = visible.some(([, value]) => valid(value))

  useEffect(() => {
    if (!containerRef.current) return
    const chart = echarts.init(containerRef.current)
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(containerRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    // Отсутствующие такты и код 307 показываются разрывом, а не скачком к нулю.
    const data: [number, number | null][] = []
    for (const [index, point] of visible.entries()) {
      const previous = visible[index - 1]
      if (previous && point[0] - previous[0] > MAX_GAP) {
        data.push([previous[0] + MAX_GAP, null])
      }
      data.push([point[0], valid(point[1]) ? point[1] : null])
    }
    chartRef.current?.setOption({
      animation: false,
      grid: { left: 44, right: 36, top: 14, bottom: 32, containLabel: true },
      tooltip: {
        trigger: 'axis',
        confine: true,
        axisPointer: { type: 'line' },
        valueFormatter: (value: unknown) =>
          typeof value === 'number' ? `${value.toFixed(1)} ${unit}` : 'Нет данных',
      },
      xAxis: {
        type: 'time', min: view[0], max: view[1], splitNumber: 3,
        axisLabel: { color: '#595959', hideOverlap: true, showMaxLabel: false, fontSize: 10, formatter: (at: number) => (
          dayjs(at).format(view[1] - view[0] <= 24 * 3600_000 ? 'HH:mm' : 'DD.MM HH:mm')
        ) },
        axisLine: { lineStyle: { color: '#d9d9d9' } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value', scale: true, splitNumber: 3,
        axisLabel: { fontSize: 11 },
        splitLine: { lineStyle: { color: '#f0f0f0' } },
      },
      series: [{
        name: title, type: 'line', data, connectNulls: false,
        showSymbol: visible.length === 1, symbolSize: 5,
        lineStyle: { width: 1.8, color }, itemStyle: { color },
      }],
      graphic: !hasData && !loading ? [{
        type: 'text', left: 'center', top: 'middle',
        style: { text: 'Нет данных за выбранный период', fill: '#8c8c8c', fontSize: 12 },
      }] : [],
    }, { replaceMerge: ['graphic'] })
    if (loading) {
      chartRef.current?.showLoading('default', { text: 'Загрузка', maskColor: '#ffffffcc' })
    } else {
      chartRef.current?.hideLoading()
    }
  }, [visible, view, title, unit, color, hasData, loading])

  return (
    <Card size="small" styles={{ body: { padding: '10px 12px 0' } }}>
      <div className="process-chart-heading">
        <Typography.Text strong>{title}</Typography.Text>
        <Space size={4}>
          <Typography.Text strong style={{ fontSize: 20, color }}>
            {lastValue === null ? '—' : lastValue.toLocaleString('ru-RU', {
              minimumFractionDigits: 1, maximumFractionDigits: 1,
            })}
          </Typography.Text>
          <Typography.Text type="secondary">{unit}</Typography.Text>
        </Space>
      </div>
      <div
        ref={containerRef}
        role="img"
        aria-label={`${title}, ${unit}. ${lastValue === null ? 'Нет свежего значения' : lastValue.toFixed(1)}`}
        style={{ width: '100%', height: 180 }}
      />
    </Card>
  )
}
