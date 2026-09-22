import { useEffect, useMemo, useState } from 'react'

import { fetchAdvisorEvents, fetchAdvisorRuns, type AdvisorEvent, type AdvisorRun } from '../api'
import type { Range } from '../components/timeWindow'

// Период опроса совпадает по порядку с тактом работника советника (advisor/runtime.py,
// 2 секунды): чаще новых записей не появляется.
const POLL_MS = 3000

export interface TimelineEvent extends AdvisorEvent {
  at: number
}

export interface TimelineRun extends AdvisorRun {
  at: number
}

/**
 * События обработчиков и запуски советника для окна графика.
 *
 * Загрузка привязана к тому же периоду `loaded`, что и ряды показаний: при его смене
 * события перечитываются с начала периода. Дальше журнал только дополняется —
 * запрашиваются записи новее последней полученной, поэтому сдвиг окна при следовании
 * повторной загрузки не вызывает. Запуски перечитываются целиком: их мало, а статус
 * выполняющегося запуска меняется. Записи до начала окна отбрасываются, как и точки рядов.
 */
export function useAdvisorTimeline(loaded: Range | null, windowStart: number | undefined) {
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [runs, setRuns] = useState<TimelineRun[]>([])

  useEffect(() => {
    if (!loaded) {
      return
    }
    let cancelled = false
    let lastId: number | undefined
    const start = new Date(loaded[0])
    const withTime = (items: AdvisorEvent[]) =>
      items.map((item) => ({ ...item, at: new Date(item.occurred_at).getTime() }))

    const pollEvents = async () => {
      const items = await fetchAdvisorEvents(lastId === undefined ? { start } : { afterId: lastId })
      if (cancelled) {
        return
      }
      if (lastId === undefined) {
        setEvents(withTime(items))
      } else if (items.length > 0) {
        setEvents((current) => [...current, ...withTime(items)])
      }
      lastId = items.at(-1)?.id ?? lastId ?? 0
    }
    const pollRuns = async () => {
      const items = await fetchAdvisorRuns(start)
      if (!cancelled) {
        setRuns(items.map((item) => ({ ...item, at: new Date(item.requested_at).getTime() })))
      }
    }
    // Следующий опрос не начинается, пока не завершён предыдущий: иначе два запроса
    // с одним и тем же lastId дописали бы одни и те же события дважды.
    let busy = false
    const poll = () => {
      if (busy) {
        return
      }
      busy = true
      // Ошибка опроса не показывается: журнал вспомогательный, а недоступность
      // платформы страница и так сообщает по загрузке рядов.
      Promise.allSettled([pollEvents(), pollRuns()]).finally(() => {
        busy = false
      })
    }

    poll()
    const timer = window.setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [loaded])

  const visibleEvents = useMemo(
    () => (windowStart === undefined ? events : events.filter((e) => e.at >= windowStart)),
    [events, windowStart],
  )
  const visibleRuns = useMemo(
    () => (windowStart === undefined ? runs : runs.filter((r) => r.at >= windowStart)),
    [runs, windowStart],
  )
  return { events: visibleEvents, runs: visibleRuns }
}
