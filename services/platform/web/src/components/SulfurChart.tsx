import * as echarts from 'echarts'
// Русская локаль ECharts: подписи месяцев на оси времени и надписи элементов графика.
// Полная сборка ECharts содержит только английскую и китайскую.
import langRU from 'echarts/i18n/langRU-obj.js'
import { useEffect, useRef } from 'react'

import type { Range } from './timeWindow'

export type SulfurPoint = [number, number]

interface Props {
  pak: SulfurPoint[]
  lims: SulfurPoint[]
  // Подписи рядов — те же имена из справочника, по которым ряды запрошены у платформы
  // (см. Dashboard). Собственных названий график не хранит.
  pakName: string
  limsName: string
  limit: number
  // Текущий момент симуляции — время последней полученной записи по любому датчику.
  // До него продлевается последнее известное значение ЛИМС.
  now: number
  loading: boolean
  // Окно режима: границы оси времени и полосы прокрутки.
  extent: Range
  // Видимая часть окна. Задаётся страницей и меняется пользователем через onViewChange.
  view: Range
  // Полосы фона — каждая вторая единица деления окна.
  bands: Range[]
  onViewChange: (view: Range) => void
}

const PAK_COLOR = '#1677ff'
const LIMS_COLOR = '#fa8c16'
const LIMS_AREA_COLOR = 'rgba(250, 140, 22, 0.15)'
const LIMIT_COLOR = '#cf1322'
const BAND_COLOR = 'rgba(0, 0, 0, 0.035)'

// Поля сетки по горизонтали. Экспортируются для таймлайна событий над графиком: при
// равных полях его шкала времени совпадает со шкалой графика.
export const SULFUR_GRID_LEFT = 56
export const SULFUR_GRID_RIGHT = 24

echarts.registerLocale('RU', langRU)

/**
 * Совмещённый график двух рядов серы.
 *
 * Экземпляр ECharts создаётся один раз и далее только получает изменения через
 * setOption. Границы оси и видимая часть задаются абсолютным временем, а не процентами:
 * при поступлении новых точек процентная прокрутка сместила бы видимую часть.
 *
 * Изменение, внесённое через setOption, события datazoom не порождает, поэтому это
 * событие означает действие пользователя и передаётся странице.
 */
export function SulfurChart({
  pak,
  lims,
  pakName,
  limsName,
  limit,
  now,
  loading,
  extent,
  view,
  bands,
  onViewChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const onViewChangeRef = useRef(onViewChange)
  onViewChangeRef.current = onViewChange

  useEffect(() => {
    if (!containerRef.current) {
      return
    }
    const chart = echarts.init(containerRef.current, undefined, {
      renderer: 'canvas',
      locale: 'RU',
    })
    chartRef.current = chart

    chart.setOption({
      animation: false,
      grid: { left: SULFUR_GRID_LEFT, right: SULFUR_GRID_RIGHT, top: 40, bottom: 56 },
      legend: { data: [pakName, limsName], top: 8 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        valueFormatter: (value: unknown) =>
          typeof value === 'number' ? `${value.toFixed(2)} мг/кг` : String(value),
      },
      xAxis: { type: 'time' },
      yAxis: {
        type: 'value',
        name: 'мг/кг',
        scale: true,
        splitLine: { lineStyle: { color: '#f0f0f0' } },
      },
      // Прокрутка по оси времени: перетаскивание внутри области графика и отдельная
      // полоса под ним. Колесо мыши изменяет масштаб только вместе с клавишей Ctrl:
      // иначе график перехватывал бы прокрутку страницы.
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: 0,
          filterMode: 'none',
          zoomOnMouseWheel: 'ctrl',
          moveOnMouseWheel: false,
          moveOnMouseMove: true,
        },
        { type: 'slider', xAxisIndex: 0, filterMode: 'none', height: 24, bottom: 8 },
      ],
      series: [
        {
          name: pakName,
          type: 'line',
          showSymbol: false,
          smooth: false,
          lineStyle: { width: 1.6, color: PAK_COLOR },
          itemStyle: { color: PAK_COLOR },
          data: [],
          // Порог наносится на ряд ПАК, а не отдельным рядом: линия принадлежит
          // шкале значений, и собственные точки ей не нужны.
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: LIMIT_COLOR, type: 'dashed', width: 1.6 },
            label: { formatter: `Норма ${limit} мг/кг`, position: 'insideEndTop' },
            data: [{ yAxis: limit }],
          },
          // Полосы фона принадлежат оси времени, но в ECharts область наносится только
          // рядом; ряд ПАК присутствует всегда, поэтому полосы закреплены за ним.
          markArea: { silent: true, itemStyle: { color: BAND_COLOR }, data: [] },
        },
        {
          name: limsName,
          type: 'line',
          // Лабораторные анализы выполняются раз в несколько часов, поэтому точки
          // показываются явно: без них редкий ряд выглядит как ломаная без измерений.
          showSymbol: true,
          symbolSize: 6,
          lineStyle: { width: 1.6, color: LIMS_COLOR },
          itemStyle: { color: LIMS_COLOR },
          // Заливка отмечает период, за который лабораторные результаты уже получены.
          areaStyle: { color: LIMS_AREA_COLOR },
          data: [],
          // Точка ЛИМС стоит в момент отбора пробы, а приходит после готовности
          // результата, поэтому ряд заканчивается раньше текущего момента. Промежуток
          // до текущего момента показан пунктиром на уровне последнего значения, без
          // заливки: новых результатов за этот период ещё нет. Линия закреплена за
          // рядом ЛИМС, чтобы скрываться вместе с ним при отключении ряда в легенде.
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: LIMS_COLOR, type: 'dashed', width: 1.6 },
            label: { show: false },
            data: [],
          },
        },
      ],
    })

    chart.on('datazoom', () => {
      const zoom = (chart.getOption().dataZoom as { startValue?: number; endValue?: number }[])[0]
      if (typeof zoom?.startValue === 'number' && typeof zoom.endValue === 'number') {
        onViewChangeRef.current([zoom.startValue, zoom.endValue])
      }
    })

    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [limit, pakName, limsName])

  useEffect(() => {
    const last = lims.length > 0 ? lims[lims.length - 1] : null
    const pending =
      last !== null && now > last[0]
        ? [[{ coord: [last[0], last[1]] }, { coord: [now, last[1]] }]]
        : []
    chartRef.current?.setOption({
      series: [
        { name: pakName, data: pak },
        { name: limsName, data: lims, markLine: { data: pending } },
      ],
    })
  }, [pak, lims, now, pakName, limsName])

  useEffect(() => {
    chartRef.current?.setOption({
      xAxis: { min: extent[0], max: extent[1] },
      dataZoom: [
        { startValue: view[0], endValue: view[1] },
        { startValue: view[0], endValue: view[1] },
      ],
      series: [
        {
          name: pakName,
          markArea: { data: bands.map(([start, end]) => [{ xAxis: start }, { xAxis: end }]) },
        },
      ],
    })
  }, [extent, view, bands, pakName, limit, limsName])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) {
      return
    }
    if (loading) {
      chart.showLoading('default', { text: 'Загрузка', maskColor: 'rgba(255,255,255,0.6)' })
    } else {
      chart.hideLoading()
    }
  }, [loading])

  // Высоту задаёт страница: график заполняет отведённый ему контейнер.
  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}
