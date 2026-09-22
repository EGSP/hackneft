// Сессии ИИ-сервиса. Запросы идут к платформе по пути /api/ai/sessions, а платформа
// передаёт их ИИ-сервису и возвращает ответы без изменений (ai_sessions.py). Поэтому типы
// ниже повторяют схемы hackneft_common.ai, а имена полей записаны в camelCase — так их
// сериализует ИИ-сервис.
//
// Журнал событий и поток SSE ИИ-сервис отдаёт без пустых полей: необязательное поле события
// в JSON отсутствует, а не равно null. Запись сессии, напротив, приходит со всеми полями.

export type SessionKind = 'chat' | 'agent'

/**
 * Состояние сессии. `running` означает, что ход идёт прямо сейчас. Терминальные `completed`
 * и `failed` достижимы только для агентской сессии: чат между ходами остаётся в `idle`.
 */
export type SessionStatus = 'idle' | 'running' | 'completed' | 'failed'

export interface Session {
  id: string
  title: string
  kind: SessionKind
  status: SessionStatus
  createdAt: string
  updatedAt: string
  /** Число событий в журнале. */
  eventCount: number
  modelId: string | null
  /** Идентификатор модели у провайдера. Сохраняется и после удаления записи справочника. */
  modelName: string | null
  modelProvider: string | null
  /** Порождающая сессия. Пусто у корневых. */
  parentId: string | null
  /** Карточка агента, по которой создана сессия. */
  agentId: string | null
  /** Итог завершённой агентской сессии: текст, переданный терминальным вызовом. */
  result: unknown
  failureMessage: string | null
}

export type TurnFailureReason =
  | 'model_missing'
  | 'model_unavailable'
  | 'model_error'
  | 'step_limit'
  | 'output_limit'
  | 'aborted'
  | 'internal'

export type ToolOutcome =
  'ok' | 'unknown_tool' | 'bad_arguments' | 'schema_mismatch' | 'tool_failure' | 'defect'

interface EventBase {
  seq: number
  at: string
}

/** Вход хода: сообщение в чате либо постановка задачи агентской сессии. */
export interface UserMessageEvent extends EventBase {
  type: 'user_message'
  text: string
}

/** Начало шага. Записывается до обращения к модели. */
export interface StepStartedEvent extends EventBase {
  type: 'step_started'
  step: number
  maxSteps: number
  provider: string
  model: string
  snapshotId?: string
  /**
   * Повтор шага после того, как рассуждение исчерпало бюджет вывода: ответ с выключенным
   * рассуждением по рассуждению оборванной попытки. Отсутствует у первой попытки.
   */
  retry?: 'no_reasoning'
}

/** Ответ модели на обращение шага: расход токенов и рассуждение. */
export interface ModelReplyEvent extends EventBase {
  type: 'model_reply'
  step: number
  promptTokens: number
  completionTokens: number
  finishReason?: string
  reasoning?: string
}

/** Текст, пришедший от модели вместе с вызовами инструментов. */
export interface AssistantNoteEvent extends EventBase {
  type: 'assistant_note'
  step: number
  text: string
}

export interface ToolCallEvent extends EventBase {
  type: 'tool_call'
  callId: string
  name: string
  rawArguments: string
  step: number
  batchSize: number
  batchIndex: number
}

export interface ToolResultEvent extends EventBase {
  type: 'tool_result'
  callId: string
  name: string
  kind: ToolOutcome
  content: string
  durationMs: number
  step: number
  batchSize: number
  batchIndex: number
}

/** Итоговый ответ хода. */
export interface AssistantMessageEvent extends EventBase {
  type: 'assistant_message'
  text: string
}

export interface TurnFinishedEvent extends EventBase {
  type: 'turn_finished'
  steps: number
  toolCalls: number
  durationMs: number
}

export interface TurnFailedEvent extends EventBase {
  type: 'turn_failed'
  reason: TurnFailureReason
  message: string
  durationMs?: number
}

/** Порождена дочерняя сессия. Её содержимое читается её собственным журналом. */
export interface ChildSessionStartedEvent extends EventBase {
  type: 'child_session_started'
  childId: string
  kind: SessionKind
  title: string
}

export interface SessionCompletedEvent extends EventBase {
  type: 'session_completed'
  result?: unknown
}

export interface SessionFailedEvent extends EventBase {
  type: 'session_failed'
  message: string
}

export type SessionEvent =
  | UserMessageEvent
  | StepStartedEvent
  | ModelReplyEvent
  | AssistantNoteEvent
  | ToolCallEvent
  | ToolResultEvent
  | AssistantMessageEvent
  | TurnFinishedEvent
  | TurnFailedEvent
  | ChildSessionStartedEvent
  | SessionCompletedEvent
  | SessionFailedEvent

export type ContextSegmentKey =
  'system_prompt' | 'mcp_instructions' | 'tools' | 'mcp_tools' | 'messages'

export interface ContextItem {
  name: string
  tokens: number
  count: number
}

export interface ContextSegment {
  key: ContextSegmentKey
  tokens: number
  items: ContextItem[]
}

/** Состав контекста сессии — оценка ИИ-сервиса, а не показания провайдера. */
export interface SessionContextResponse {
  model: string
  window: number
  windowSource: 'known' | 'assumed'
  tokenizer: string
  used: number
  segments: ContextSegment[]
}

export interface ToolInfo {
  name: string
  description: string
  source: 'builtin' | 'mcp'
}

/** Инструмент в том виде, в каком его описание ушло модели. */
export interface SnapshotTool extends ToolInfo {
  parameters: Record<string, unknown>
}

/**
 * Снимок постоянной части запроса к модели: всё, что уходит в запрос помимо переписки.
 * Идентификатор — хеш содержимого, поэтому снимок с данным идентификатором неизменен.
 */
export interface RequestSnapshot {
  id: string
  createdAt: string
  /** Указания сервиса: системный промпт. */
  prompt: string
  /** Секции, дописанные к указаниям: текстовые инструкции серверов MCP. */
  sections: string[]
  /** Инструменты в том порядке, в каком ушли модели, включая терминальные. */
  tools: SnapshotTool[]
}

const BASE = '/api/ai/sessions'

/** Отказ с кодом ответа: по коду решается, есть ли смысл повторять запрос. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`)
  if (!response.ok) {
    // Отказ приходит в одном из двух видов. Сама платформа — например, когда ИИ-сервис
    // недоступен, — отвечает, как принято в FastAPI, полем detail. Отказ ИИ-сервиса
    // передаётся как есть, а у него причина лежит в поле message, как в API xip.
    let reason = `Ошибка ${response.status}`
    try {
      const payload = (await response.json()) as { detail?: unknown; message?: unknown }
      if (typeof payload.message === 'string') {
        reason = payload.message
      } else if (typeof payload.detail === 'string') {
        reason = payload.detail
      }
    } catch {
      // тело не является JSON — остаётся сообщение с кодом ответа
    }
    throw new ApiError(reason, response.status)
  }
  return (await response.json()) as T
}

const sessionPath = (id: string, suffix = ''): string => `/${encodeURIComponent(id)}${suffix}`

export const sessionsApi = {
  /** Корневые сессии по времени последнего события. Дочерние в перечень не входят. */
  list: (kind?: SessionKind) =>
    request<{ sessions: Session[] }>(kind === undefined ? '' : `?kind=${kind}`),
  get: (id: string) => request<Session>(sessionPath(id)),
  events: (id: string, after = 0) =>
    request<{ events: SessionEvent[]; lastSeq: number }>(sessionPath(id, `/events?after=${after}`)),
  context: (id: string) => request<SessionContextResponse>(sessionPath(id, '/context')),
  tools: () => request<{ tools: ToolInfo[] }>('/tools'),
  snapshot: (id: string) => request<RequestSnapshot>(`/snapshots/${encodeURIComponent(id)}`),
}

/** Адрес потока событий сессии. `after` — номер последнего уже полученного события. */
export function sessionStreamUrl(id: string, after: number): string {
  return `${BASE}${sessionPath(id, `/stream?after=${after}`)}`
}
