import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { sessionStreamUrl, type SessionEvent } from './api'
import { sessionKeys } from './queries'

/** События, после которых состояние записи сессии отличается от прочитанного. */
const TERMINAL = new Set<SessionEvent['type']>([
  'turn_finished',
  'turn_failed',
  'session_completed',
  'session_failed',
])

/** Объединение двух частей журнала по порядковому номеру: без повторов и по порядку. */
function merge(current: readonly SessionEvent[], fresh: readonly SessionEvent[]): SessionEvent[] {
  const bySeq = new Map<number, SessionEvent>()
  for (const event of fresh) {
    bySeq.set(event.seq, event)
  }
  for (const event of current) {
    if (!bySeq.has(event.seq)) {
      bySeq.set(event.seq, event)
    }
  }
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq)
}

/**
 * Подписка на поток событий сессии.
 *
 * Ход агента не привязан к времени жизни соединения: он выполняется на сервере фоном, а
 * поток лишь показывает происходящее. Поэтому при переподключении клиент передаёт номер
 * последнего полученного события и догружает пропущенное, а повторы отбрасывает по номеру —
 * догрузка и живые события могут пересечься.
 *
 * Журнал, перечитанный запросом, дополняет полученное из потока, а не заменяет его. Журнал
 * перечитывается по завершении хода, и событие, пришедшее потоком, пока ответ на этот запрос
 * был в пути, в ответе отсутствует: замена стёрла бы его со страницы. Заменяется журнал
 * только при переходе к другой сессии.
 */
export function useSessionStream(
  sessionId: string,
  initial: readonly SessionEvent[] | undefined,
): SessionEvent[] {
  const [events, setEvents] = useState<SessionEvent[]>([])
  const seenRef = useRef<Set<number>>(new Set())
  const sessionRef = useRef(sessionId)
  const queryClient = useQueryClient()

  useEffect(() => {
    const fresh = initial ?? []
    if (sessionRef.current !== sessionId) {
      sessionRef.current = sessionId
      seenRef.current = new Set(fresh.map((event) => event.seq))
      setEvents([...fresh])
      return
    }
    fresh.forEach((event) => seenRef.current.add(event.seq))
    setEvents((previous) => merge(previous, fresh))
  }, [sessionId, initial])

  useEffect(() => {
    const lastSeq = Math.max(0, ...Array.from(seenRef.current))
    const source = new EventSource(sessionStreamUrl(sessionId, lastSeq))

    source.addEventListener('event', (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as SessionEvent
      if (seenRef.current.has(event.seq)) {
        return
      }
      seenRef.current.add(event.seq)
      setEvents((previous) => merge(previous, [event]))

      // Завершение хода либо сессии меняет состояние записи и порядок в списке, а запись
      // читается отдельным запросом, который сам о происходящем не узнаёт. У агентской
      // сессии исход идёт следом за завершением хода отдельным событием, и без него
      // состояние в заголовке менялось бы по событию соседней природы, а не по своему.
      if (TERMINAL.has(event.type)) {
        void queryClient.invalidateQueries({ queryKey: sessionKeys.all })
        void queryClient.invalidateQueries({ queryKey: sessionKeys.one(sessionId) })
      }
    })

    return () => source.close()
  }, [sessionId, queryClient])

  return events
}
