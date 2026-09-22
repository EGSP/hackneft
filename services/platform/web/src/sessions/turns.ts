import type { SessionEvent, SessionKind, TurnFailureReason } from './api'

/**
 * Ход в форме, удобной для отрисовки.
 *
 * Журнал — плоская последовательность событий, а интерфейс показывает переписку. Порядок
 * элементов внутри хода сохраняется журнальным: текст модели, вызовы инструментов и
 * размышление идут так, как происходили.
 *
 * Обращение к модели отдельным элементом не показывается. Само по себе оно ничего не
 * сообщает: пока ответа нет, достаточно указателя ожидания, а когда ответ пришёл — важен
 * он сам, а не факт обращения.
 */

/** Элемент работы агента: размышление либо вызов инструмента. Текста человеку не несёт. */
export type WorkItem =
  | {
      readonly kind: 'reasoning'
      readonly key: string
      readonly text: string
      readonly tokens: number
      /** Рассуждение оборвано пределом вывода: ответа за ним не последовало. */
      readonly cutOff: boolean
      /** Рассуждение получено повтором шага после оборванного. */
      readonly retry?: 'no_reasoning'
    }
  | {
      readonly kind: 'tool'
      readonly key: string
      readonly name: string
      readonly rawArguments: string
      result?: string
      ok?: boolean
      durationMs?: number
    }

/**
 * Группа подряд идущих элементов работы — контейнер действий агента.
 *
 * Образуется с первого же элемента, а не со второго. Иначе при появлении второго элемента
 * одиночный блок заменялся бы группой, то есть другим типом узла: React разбирал бы старый
 * узел и строил новый, теряя раскрытие, сделанное пользователем, и смещая содержимое.
 * Контейнер должен только пополняться, поэтому он один и тот же с самого начала.
 */
export type WorkGroup = {
  readonly kind: 'group'
  readonly key: string
  readonly items: WorkItem[]
}

/** Текст модели, адресованный человеку. */
export type TextItem = { readonly kind: 'text'; readonly key: string; readonly text: string }

/**
 * Порождённая дочерняя сессия. Показывается карточкой, раскрывающей её журнал: вложенность
 * существует только в отрисовке, а читается дочерняя сессия теми же запросами, что и любая
 * другая.
 */
export type ChildItem = {
  readonly kind: 'child'
  readonly key: string
  readonly childId: string
  readonly childKind: SessionKind
  readonly title: string
}

export type TurnItem = WorkItem | WorkGroup | TextItem | ChildItem

/**
 * Содержательный элемент несёт ответ и читается сам по себе; фоновый показывает, каким путём
 * агент к ответу пришёл.
 *
 * Различение нужно свёртке: фоновые элементы прячутся под общий заголовок, а содержательный
 * внутри свёрнутой группы оказался бы не показан вовсе. Признак определяется видом элемента,
 * а не именем инструмента: инструментов со временем станет много, и перечень имён здесь
 * пришлось бы править при добавлении каждого.
 */
export function isSubstantive(item: TurnItem): item is TextItem | ChildItem {
  return item.kind === 'text' || item.kind === 'child'
}

/**
 * Расход токенов по данным провайдера. Входные — запросы к модели: каждый несёт переписку
 * целиком, поэтому их сумма растёт быстрее контекста. Выходные — ответы вместе с рассуждением.
 */
export type TokenUsage = { readonly prompt: number; readonly completion: number }

export type TurnBlock = {
  readonly key: number
  /**
   * Вход хода: сообщение либо постановка задачи. Отсутствует у вводного блока — событий,
   * записанных в журнал до первого входа.
   */
  readonly question?: string
  /** Время записи входа. */
  readonly at?: string
  readonly items: TurnItem[]
  /**
   * Расход хода — сумма по ответам модели. Отсутствует, пока ни одного ответа не пришло:
   * нулевой расход утверждал бы, что обращение ничего не стоило, а это неизвестно.
   */
  usage?: TokenUsage
  /** Длительность хода. Известна по завершении; у хода, закрытого при запуске сервиса, её нет. */
  durationMs?: number
  /** Число начатых шагов — обращений к модели. */
  steps?: number
  /**
   * Модель последнего шага в виде «провайдер/модель». Модель отмечается на каждом шаге, а не
   * только в сессии: модель чата можно сменить между ходами, и без отметки не понять, какой
   * моделью получен ответ.
   */
  model?: string
  /**
   * Снимок постоянной части запроса хода: указаний и описаний инструментов. Набор в пределах
   * хода не меняется, и все шаги хода ссылаются на один снимок.
   */
  snapshotId?: string
  failure?: { readonly reason: TurnFailureReason; readonly message: string }
  /**
   * Отказ агентской сессии, которому не предшествовал отказ хода. Так завершается, например,
   * сессия, где агент задал уточняющий вопрос, а отвечать некому, либо сессия, не начавшая
   * ход вовсе. Отказ, повторяющий отказ хода, здесь не дублируется.
   */
  sessionFailure?: string
  /** Ход ещё идёт: итогового события не было. */
  running: boolean
  /** Ожидается ответ модели: шаг начат, но от модели ещё ничего не пришло. */
  awaitingModel: boolean
}

/**
 * Ключ вызова инструмента.
 *
 * Одного `callId` недостаточно: часть моделей возвращает вместо идентификатора имя
 * инструмента, и тогда все вызовы одного инструмента в ходе получают одинаковый ключ. Шаг
 * и порядковый номер в пачке делают ключ различимым при любом поведении провайдера.
 */
const callKey = (step: number, batchIndex: number, callId: string): string =>
  `${step}:${batchIndex}:${callId}`

export function groupTurns(events: readonly SessionEvent[]): TurnBlock[] {
  const turns: TurnBlock[] = []
  let current: TurnBlock | undefined
  // Повод повтора из начала шага: относится к ответу, который придёт следом.
  let retry: 'no_reasoning' | undefined

  // Вводный блок заводится по первому событию, пришедшему раньше входа. У xip такие события
  // отбрасывались, а здесь их порождает сама платформа: дочернюю сессию можно создать и у
  // сессии, где ещё не было ни одного хода, а агентская сессия, не сумевшая начать ход,
  // завершается отказом без записи постановки задачи.
  const target = (): TurnBlock => {
    if (current === undefined) {
      current = { key: 0, items: [], running: false, awaitingModel: false }
      turns.push(current)
    }
    return current
  }

  for (const event of events) {
    if (event.type === 'user_message') {
      current = {
        key: event.seq,
        question: event.text,
        at: event.at,
        items: [],
        running: true,
        awaitingModel: false,
      }
      turns.push(current)
      continue
    }

    switch (event.type) {
      case 'step_started': {
        const turn = target()
        // Ответ модели ещё не получен: до его прихода показывается только ожидание.
        turn.awaitingModel = true
        turn.steps = event.step
        turn.model = `${event.provider}/${event.model}`
        turn.snapshotId ??= event.snapshotId
        retry = event.retry
        break
      }

      case 'model_reply': {
        const turn = target()
        turn.awaitingModel = false
        turn.usage = {
          prompt: (turn.usage?.prompt ?? 0) + event.promptTokens,
          completion: (turn.usage?.completion ?? 0) + event.completionTokens,
        }
        if (event.reasoning !== undefined) {
          turn.items.push({
            kind: 'reasoning',
            key: `r-${event.seq}`,
            text: event.reasoning,
            tokens: event.completionTokens,
            cutOff: event.finishReason === 'length',
            retry,
          })
        }
        break
      }

      // Текст, пришедший вместе с вызовами, и итоговый ответ — одно и то же с точки зрения
      // чтения: и то и другое модель адресует человеку.
      case 'assistant_note':
      case 'assistant_message': {
        const turn = target()
        turn.awaitingModel = false
        if (event.text !== '') {
          turn.items.push({ kind: 'text', key: `t-${event.seq}`, text: event.text })
        }
        break
      }

      case 'tool_call': {
        const turn = target()
        turn.awaitingModel = false
        turn.items.push({
          kind: 'tool',
          key: callKey(event.step, event.batchIndex, event.callId),
          name: event.name,
          rawArguments: event.rawArguments,
        })
        break
      }

      case 'tool_result': {
        const key = callKey(event.step, event.batchIndex, event.callId)
        const item = current?.items.find(
          (candidate): candidate is Extract<WorkItem, { kind: 'tool' }> =>
            candidate.kind === 'tool' && candidate.key === key,
        )
        if (item === undefined) {
          break
        }
        item.result = event.content
        item.ok = event.kind === 'ok'
        item.durationMs = event.durationMs
        break
      }

      case 'child_session_started': {
        const turn = target()
        turn.awaitingModel = false
        turn.items.push({
          kind: 'child',
          key: `c-${event.seq}`,
          childId: event.childId,
          childKind: event.kind,
          title: event.title,
        })
        break
      }

      case 'turn_finished': {
        const turn = target()
        turn.durationMs = event.durationMs
        turn.running = false
        turn.awaitingModel = false
        break
      }

      case 'turn_failed': {
        const turn = target()
        turn.failure = { reason: event.reason, message: event.message }
        if (event.durationMs !== undefined) {
          turn.durationMs = event.durationMs
        }
        turn.running = false
        turn.awaitingModel = false
        break
      }

      case 'session_failed': {
        const turn = target()
        if (turn.failure === undefined) {
          turn.sessionFailure = event.message
        }
        turn.running = false
        turn.awaitingModel = false
        break
      }

      // Итог агентской сессии повторяет итоговый ответ хода, который уже показан, а
      // состояние сессии видно в заголовке.
      case 'session_completed':
        break
    }
  }

  return turns.map((turn) => ({ ...turn, items: groupWork(turn.items) }))
}

/**
 * Сводит подряд идущие элементы работы в контейнер действий.
 *
 * Содержательный элемент последовательность разрывает: он адресован человеку и разделяет
 * этапы работы по смыслу.
 *
 * Ключ контейнера строится по первому его элементу и потому не меняется, сколько бы
 * элементов ни добавилось следом. Это существенно: ход отрисовывается заново на каждое
 * событие журнала, и только неизменный ключ позволяет React считать контейнер тем же
 * узлом — иначе раскрытие, сделанное пользователем, терялось бы при каждом новом действии.
 */
function groupWork(items: readonly TurnItem[]): TurnItem[] {
  const result: TurnItem[] = []
  let run: WorkItem[] = []

  const flush = (): void => {
    if (run.length === 0) {
      return
    }
    result.push({ kind: 'group', key: `g-${run[0].key}`, items: run })
    run = []
  }

  for (const item of items) {
    if (item.kind === 'group' || isSubstantive(item)) {
      flush()
      result.push(item)
    } else {
      run.push(item)
    }
  }

  flush()
  return result
}

/** Идёт ли сейчас ход. Признак берётся из журнала: он приходит раньше обновления записи. */
export function isRunning(turns: readonly TurnBlock[]): boolean {
  return turns.at(-1)?.running ?? false
}

/** Число завершённых ходов, каким бы ни был исход. */
export function completedCount(turns: readonly TurnBlock[]): number {
  return turns.filter((turn) => !turn.running).length
}

/** Расход за сессию: сумма по ходам. */
export function totalUsage(turns: readonly TurnBlock[]): TokenUsage {
  let prompt = 0
  let completion = 0
  for (const turn of turns) {
    prompt += turn.usage?.prompt ?? 0
    completion += turn.usage?.completion ?? 0
  }
  return { prompt, completion }
}
