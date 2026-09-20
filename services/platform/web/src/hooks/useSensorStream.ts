import { useEffect, useRef, useState } from 'react'

import type { SensorEvent } from '../api'

export type StreamStatus = 'connecting' | 'open' | 'closed'

/**
 * Подписка на поток событий /api/stream.
 *
 * Обработчик хранится в ref и подменяется без пересоздания соединения: иначе каждое
 * изменение состояния страницы, от которого зависит обработчик, разрывало бы поток
 * и открывало новый. Разорванное соединение EventSource восстанавливает сам.
 */
export function useSensorStream(onEvent: (event: SensorEvent) => void): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent

  useEffect(() => {
    const source = new EventSource('/api/stream')

    source.onopen = () => setStatus('open')
    source.onerror = () => setStatus('closed')
    source.onmessage = (message) => {
      setStatus('open')
      try {
        handlerRef.current(JSON.parse(message.data) as SensorEvent)
      } catch {
        // Строка, не являющаяся событием (например, комментарий keep-alive),
        // до onmessage не доходит, так что сюда попадает только испорченное тело.
      }
    }

    return () => source.close()
  }, [])

  return status
}
