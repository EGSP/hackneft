import * as echarts from 'echarts'
import { useEffect, useRef } from 'react'

export type SulfurPoint = [number, number]

interface Props {
  pak: SulfurPoint[]
  lims: SulfurPoint[]
  // Подписи рядов — те же имена из справочника, по которым ряды запрошены у платформы
  // (см. Dashboard). Собственных названий график не хранит.
  pakName: string
  limsName: string
  limit: number
  loading: boolean
}

const PAK_COLOR = '#1677ff'
const LIMS_COLOR = '#fa8c16'
const LIMIT_COLOR = '#cf1322'

/**
 * Совмещённый график двух рядов серы.
 *
 * Экземпляр ECharts создаётся один раз и далее только получает новые данные через
 * setOption с параметром notMerge=false: полная пересборка сбрасывала бы положение
 * прокрутки, а оно меняется пользователем и обновлением данных затрагиваться не должно.
 */
export function SulfurChart({ pak, lims, pakName, limsName, limit, loading }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!containerRef.current) {
      return
    }
    const chart = echarts.init(containerRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart

    chart.setOption({
      animation: false,
      grid: { left: 56, right: 24, top: 44, bottom: 64 },
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
      // полоса под ним. Границы задаются в процентах от общего набора точек и по
      // умолчанию охватывают его целиком — период выбирается календарём, а прокрутка
      // служит для рассмотрения его частей.
      //
      // Колесо мыши изменяет масштаб только вместе с клавишей Ctrl: иначе график
      // перехватывал бы прокрутку страницы, когда указатель оказывается над ним.
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: 0,
          start: 0,
          end: 100,
          zoomOnMouseWheel: 'ctrl',
          moveOnMouseWheel: false,
          moveOnMouseMove: true,
        },
        { type: 'slider', xAxisIndex: 0, start: 0, end: 100, height: 24, bottom: 12 },
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
        },
        {
          name: limsName,
          type: 'line',
          // Лабораторные анализы выполняются раз в несколько часов, поэтому точки
          // показываются явно: без них редкий ряд выглядит как ломаная без измерений.
          showSymbol: true,
          symbolSize: 6,
          lineStyle: { width: 1.6, color: LIMS_COLOR, type: 'dotted' },
          itemStyle: { color: LIMS_COLOR },
          data: [],
        },
      ],
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
    chartRef.current?.setOption({
      series: [
        { name: pakName, data: pak },
        { name: limsName, data: lims },
      ],
    })
  }, [pak, lims, pakName, limsName])

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

  return <div ref={containerRef} style={{ width: '100%', height: 380 }} />
}
