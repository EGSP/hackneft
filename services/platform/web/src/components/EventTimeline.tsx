import { Tooltip, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  KIND_LABEL,
  RUN_STATUS_LABEL,
  SEVERITY_LABEL,
  describeEvidence,
  eventAppearance,
  runAppearance,
  type Appearance,
} from '../advisor/appearance'
import type { TimelineEvent, TimelineRun } from '../advisor/useAdvisorTimeline'
import type { Range } from './timeWindow'

interface Props {
  events: TimelineEvent[]
  runs: TimelineRun[]
  // Видимая часть окна — та же, что у графиков, поэтому шкалы совпадают.
  view: Range
  bands: Range[]
  // Поля дорожки равны полям сетки графика под ней: при равных отступах момент времени
  // стоит на одной вертикали на обоих.
  left: number
  right: number
}

// Значки ближе этого расстояния в пикселях объединяются в один с числом событий:
// иначе события одного такта обработки легли бы друг на друга.
const CLUSTER_PX = 20
const MARKER = 22
const MAX_TOOLTIP_ITEMS = 8
const BAND_COLOR = 'rgba(0, 0, 0, 0.035)'

type Item =
  | { type: 'event'; at: number; event: TimelineEvent; look: Appearance; rank: number }
  | { type: 'run'; at: number; run: TimelineRun; look: Appearance; rank: number }

const RANK = { info: 1, warning: 2, critical: 3 } as const

interface Cluster {
  x: number
  items: Item[]
  lead: Item
}

function itemKey(item: Item): string {
  return item.type === 'run' ? `run-${item.run.id}` : `event-${item.event.id}`
}

// Итог запуска — карточка совета; у прежних запусков итогом мог быть текст.
function adviceHeadline(result: unknown): string | null {
  if (result && typeof result === 'object' && 'headline' in result) {
    const { headline } = result as { headline: unknown }
    return typeof headline === 'string' ? headline : null
  }
  return null
}

function formatTime(at: number): string {
  return dayjs(at).format('DD.MM HH:mm')
}

function EventLine({ item }: { item: Item }) {
  const time = (
    <Typography.Text type="secondary" style={{ color: 'rgba(255,255,255,0.65)' }}>
      {formatTime(item.at)}
    </Typography.Text>
  )
  if (item.type === 'run') {
    const { run } = item
    return (
      <div className="timeline-tip-item">
        <span style={{ color: item.look.color }}>{item.look.icon}</span>
        <div>
          <div>
            {time} <strong>Запуск советника</strong> · {RUN_STATUS_LABEL[run.status] ?? run.status}
          </div>
          {adviceHeadline(run.result) && <div>Совет: {adviceHeadline(run.result)}</div>}
          {run.reasons.length > 0 && (
            <div>Причины: {run.reasons.map((reason) => reason.reason).join('; ')}</div>
          )}
          {run.error && <div>{run.error}</div>}
          {run.session_id && <div className="timeline-tip-hint">Нажмите, чтобы открыть сессию</div>}
        </div>
      </div>
    )
  }
  const { event } = item
  const evidence = describeEvidence(event)
  return (
    <div className="timeline-tip-item">
      <span style={{ color: item.look.color }}>{item.look.icon}</span>
      <div>
        <div>
          {time} <strong>{event.reason}</strong>
        </div>
        <div className="timeline-tip-hint">
          {KIND_LABEL[event.kind] ?? event.kind} · {SEVERITY_LABEL[event.severity]}
        </div>
        <div>{event.consequence}</div>
        {evidence && <div className="timeline-tip-hint">{evidence}</div>}
      </div>
    </div>
  )
}

function ClusterTip({ cluster }: { cluster: Cluster }) {
  const shown = cluster.items.slice(0, MAX_TOOLTIP_ITEMS)
  const rest = cluster.items.length - shown.length
  const lines: ReactNode[] = shown.map((item) => <EventLine key={itemKey(item)} item={item} />)
  return (
    <div className="timeline-tip">
      {lines}
      {rest > 0 && <div className="timeline-tip-hint">и ещё {rest}</div>}
    </div>
  )
}

/**
 * Таймлайн событий обработчиков ситуаций и запусков советника над графиком серы.
 *
 * Дорожка рисуется разметкой, а не отдельным графиком ECharts: значки — это значки antd,
 * а подсказка по наведению содержит несколько строк с пояснениями. Шкала задаётся
 * видимой частью окна `view`, общей для всех графиков страницы, поэтому прокрутка и
 * масштаб графика серы сдвигают и таймлайн.
 */
export function EventTimeline({ events, runs, view, bands, left, right }: Props) {
  const navigate = useNavigate()
  const trackRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const track = trackRef.current
    if (!track) {
      return
    }
    const observer = new ResizeObserver(() => setWidth(track.clientWidth))
    observer.observe(track)
    setWidth(track.clientWidth)
    return () => observer.disconnect()
  }, [])

  const span = view[1] - view[0]
  const toX = (at: number) => ((at - view[0]) / span) * width

  const clusters = useMemo(() => {
    if (width <= 0 || span <= 0) {
      return []
    }
    const items: Item[] = [
      ...events.map((event): Item => ({
        type: 'event', at: event.at, event, look: eventAppearance(event), rank: RANK[event.severity] ?? 1,
      })),
      // Запуск — итог событий, поэтому в общем значке он уступает только критичному.
      ...runs.map((run): Item => ({ type: 'run', at: run.at, run, look: runAppearance(run), rank: 2.5 })),
    ]
      .filter((item) => item.at >= view[0] && item.at <= view[1])
      .sort((a, b) => a.at - b.at)

    const result: Cluster[] = []
    for (const item of items) {
      const x = ((item.at - view[0]) / span) * width
      const last = result.at(-1)
      if (last && x - last.x < CLUSTER_PX) {
        last.items.push(item)
        if (item.rank >= last.lead.rank) {
          last.lead = item
        }
      } else {
        result.push({ x, items: [item], lead: item })
      }
    }
    return result
  }, [events, runs, view, span, width])

  const openRun = (cluster: Cluster) => {
    const run = [...cluster.items].reverse().find((item) => item.type === 'run' && item.run.session_id)
    if (run?.type === 'run' && run.run.session_id) {
      navigate(`/sessions/${run.run.session_id}`)
    }
  }

  return (
    <div className="event-timeline" style={{ paddingLeft: left, paddingRight: right }}>
      <Typography.Text type="secondary" className="event-timeline-label" style={{ width: left }}>
        События
      </Typography.Text>
      <div ref={trackRef} className="event-timeline-track">
        {bands.map(([start, end]) => {
          const from = Math.max(start, view[0])
          const to = Math.min(end, view[1])
          return to > from ? (
            <div
              key={start}
              className="event-timeline-band"
              style={{ left: toX(from), width: toX(to) - toX(from), background: BAND_COLOR }}
            />
          ) : null
        })}
        <div className="event-timeline-axis" />
        {clusters.map((cluster) => {
          const hasSession = cluster.items.some((item) => item.type === 'run' && item.run.session_id)
          return (
            <Tooltip
              // Ключ — первое событие группы, а не её положение: при следовании окна
              // значки сдвигаются, и открытая подсказка не должна закрываться.
              key={itemKey(cluster.items[0])}
              title={<ClusterTip cluster={cluster} />}
              overlayStyle={{ maxWidth: 420 }}
              mouseEnterDelay={0.05}
            >
              <button
                type="button"
                className="event-timeline-marker"
                aria-label={`События: ${cluster.items.length}, ${formatTime(cluster.lead.at)}`}
                style={{
                  left: cluster.x - MARKER / 2,
                  width: MARKER,
                  height: MARKER,
                  color: cluster.lead.look.color,
                  borderColor: cluster.lead.look.color,
                  cursor: hasSession ? 'pointer' : 'default',
                }}
                onClick={() => openRun(cluster)}
              >
                {cluster.lead.look.icon}
                {cluster.items.length > 1 && (
                  <span className="event-timeline-count" style={{ background: cluster.lead.look.color }}>
                    {cluster.items.length}
                  </span>
                )}
              </button>
            </Tooltip>
          )
        })}
      </div>
    </div>
  )
}
