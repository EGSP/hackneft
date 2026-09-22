import type { SessionKind, SessionStatus } from './api'

export const kindLabel: Record<SessionKind, string> = {
  agent: 'Агент',
  chat: 'Чат',
}

/** Метка состояния сессии: цвет метки antd и подпись. */
export const statusView: Record<SessionStatus, { color: string; label: string }> = {
  running: { color: 'processing', label: 'Выполняется' },
  completed: { color: 'success', label: 'Завершено' },
  failed: { color: 'error', label: 'Отказ' },
  idle: { color: 'default', label: 'Ожидает' },
}
