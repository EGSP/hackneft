import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { ApiError, sessionsApi, type SessionKind } from './api'

export const sessionKeys = {
  all: ['sessions'] as const,
  ofKind: (kind: SessionKind | 'all') => ['sessions', 'kind', kind] as const,
  one: (id: string) => ['sessions', id] as const,
  events: (id: string) => ['sessions', id, 'events'] as const,
}

/**
 * Период опроса перечня сессий. Сессии создаёт не эта страница, а платформа и сами агенты,
 * поэтому о новой сессии страница узнаёт только опросом: поток событий есть лишь у каждой
 * сессии в отдельности, общего потока перечня у ИИ-сервиса нет.
 */
const LIST_REFRESH_MS = 5000

/**
 * Период перечитывания записи сессии, пока в ней идёт ход. Открытая сессия узнаёт о
 * завершении из своего потока, а свёрнутая карточка дочерней сессии потока не открывает,
 * и без опроса её состояние оставалось бы «Выполняется» до перезагрузки страницы.
 */
const RUNNING_REFRESH_MS = 3000

/**
 * Повтор неудачного запроса — один и только при отказе соединения либо сбое на стороне
 * сервера. Отказ с кодом 4xx повтором не исправляется: сессии, которой нет, не станет и
 * через секунду. К тому же повтор откладывается, пока вкладка браузера скрыта, и всё это
 * время страница не показывала бы ни данных, ни причины отказа.
 */
const retryTransient = (failureCount: number, error: Error): boolean =>
  failureCount < 1 && !(error instanceof ApiError && error.status < 500)

/** Перечень корневых сессий. Отбор по виду выполняет ИИ-сервис. */
export function useSessions(kind?: SessionKind) {
  return useQuery({
    queryKey: sessionKeys.ofKind(kind ?? 'all'),
    queryFn: async () => (await sessionsApi.list(kind)).sessions,
    refetchInterval: LIST_REFRESH_MS,
    retry: retryTransient,
  })
}

export function useSession(id: string) {
  return useQuery({
    queryKey: sessionKeys.one(id),
    queryFn: () => sessionsApi.get(id),
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? RUNNING_REFRESH_MS : false,
    retry: retryTransient,
  })
}

export function useSessionEvents(id: string) {
  return useQuery({
    queryKey: sessionKeys.events(id),
    queryFn: async () => (await sessionsApi.events(id)).events,
    retry: retryTransient,
  })
}

/**
 * Состав контекста сессии. Сервер вычисляет его на каждый запрос, поэтому версия в ключе
 * задаёт, когда спрашивать заново: по завершении хода и при смене модели. Прежний ответ
 * показывается, пока идёт новый, — иначе индикатор на время запроса оставался бы пустым.
 */
export function useSessionContext(id: string, version: string) {
  return useQuery({
    queryKey: [...sessionKeys.one(id), 'context', version],
    queryFn: () => sessionsApi.context(id),
    placeholderData: keepPreviousData,
    // Оценка контекста требует модели сессии. Если модели нет, отказ показывается в
    // подсказке индикатора, и повторять запрос бесполезно.
    retry: false,
  })
}

/**
 * Снимок запроса к модели. Запрашивается, только когда его открыли, и больше не
 * перечитывается: идентификатор снимка — хеш содержимого, и содержимое по нему не меняется.
 */
export function useSnapshot(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ['snapshots', id],
    queryFn: () => sessionsApi.snapshot(id),
    enabled,
    staleTime: Infinity,
    retry: retryTransient,
  })
}

/** Инструменты агентов. Состав меняется редко: при подключении серверов MCP. */
export function useTools() {
  return useQuery({
    queryKey: ['tools'],
    queryFn: async () => (await sessionsApi.tools()).tools,
    staleTime: 60_000,
    retry: retryTransient,
  })
}
