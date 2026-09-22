import { ArrowUpOutlined } from '@ant-design/icons'
import { Alert, Button, Flex, Skeleton, Tag, Tooltip, Typography, theme } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useMemo, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { ContextIndicator } from './ContextIndicator'
import { kindLabel, statusView } from './labels'
import { useSession, useSessionEvents } from './queries'
import { SessionIdLabel } from './SessionIdLabel'
import { TurnBlockView } from './TurnBlock'
import { completedCount, groupTurns, isRunning, totalUsage } from './turns'
import { useSessionStream } from './useSessionStream'

/**
 * Журнал выбранной сессии.
 *
 * Перенесён из чата xip без поля ввода: страница служит прослеживаемости, и сообщений из неё
 * не отправляют. Поэтому модель сессии и индикатор контекста, стоявшие в xip под полем
 * ввода, перенесены в заголовок, а сам заголовок дополнен строкой сведений о сессии.
 */
export function SessionView() {
  const { token } = theme.useToken()
  const navigate = useNavigate()
  const { sessionId = '' } = useParams()
  const { data: session, error: sessionError } = useSession(sessionId)
  const { data: history, error: eventsError } = useSessionEvents(sessionId)
  const events = useSessionStream(sessionId, history)

  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  /** Держался ли пользователь у нижнего края к моменту прихода события. */
  const atBottomRef = useRef(true)

  const turns = useMemo(() => groupTurns(events), [events])
  const usage = useMemo(() => totalUsage(turns), [turns])
  // Признак хода берётся из журнала: событие приходит раньше, чем обновится запись сессии.
  const running = isRunning(turns) || session?.status === 'running'
  const status = running
    ? statusView.running
    : session === undefined
      ? undefined
      : statusView[session.status]
  // Дочерние сессии в перечне не показываются, и открытая отдельно дочерняя сессия несёт
  // переход к порождающей: иначе вернуться к родителю можно было бы только через историю.
  const parentId = session?.parentId ?? null

  /**
   * Прокрутка к последнему событию — но только если пользователь и так находится внизу.
   *
   * Ход добавляет события десятками, и безусловная прокрутка возвращала бы к нижнему краю
   * того, кто отлистал вверх, чтобы прочитать написанное. Прокрутка мгновенная, а не
   * плавная: анимации накладывались бы одна на другую, и вместо перемещения получалось бы
   * дрожание.
   *
   * Запас в 120 пикселей нужен потому, что «внизу» редко бывает точным: содержимое
   * дорисовывается, и положение смещается на несколько пикселей само собой.
   */
  useEffect(() => {
    if (!atBottomRef.current) {
      return
    }
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [events.length])

  const onScroll = (): void => {
    const box = scrollRef.current
    if (box === null) {
      return
    }
    atBottomRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 120
  }

  const facts =
    session === undefined
      ? []
      : [
          kindLabel[session.kind],
          `создана ${dayjs(session.createdAt).format('DD.MM.YYYY HH:mm:ss')}`,
          session.modelName === null
            ? 'модель не назначена'
            : `модель: ${session.modelProvider ?? '?'}/${session.modelName}`,
          session.agentId === null ? null : `карточка агента: ${session.agentId}`,
          `событий: ${events.length}`,
        ].filter((fact): fact is string => fact !== null)

  return (
    <Flex vertical style={{ height: '100%', minHeight: 0 }}>
      <Flex
        vertical
        gap={token.marginXXS}
        style={{
          paddingInline: token.paddingLG,
          paddingBlock: token.paddingSM,
          borderBlockEnd: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <Flex align="center" gap={token.marginXS} style={{ minWidth: 0 }}>
          {parentId === null ? null : (
            <Tooltip title="Порождающая сессия">
              <Button
                type="text"
                size="small"
                icon={<ArrowUpOutlined />}
                aria-label="Перейти к порождающей сессии"
                onClick={() => navigate(`/sessions/${encodeURIComponent(parentId)}`)}
              />
            </Tooltip>
          )}

          <Typography.Text strong ellipsis style={{ flex: 1 }}>
            {session?.title ?? '…'}
          </Typography.Text>

          {status === undefined ? null : (
            <Tag color={status.color} style={{ marginInlineEnd: 0 }}>
              {status.label}
            </Tag>
          )}

          <ContextIndicator
            sessionId={sessionId}
            modelName={session?.modelName ?? null}
            completedTurns={completedCount(turns)}
            usage={usage}
          />

          <SessionIdLabel sessionId={sessionId} />
        </Flex>

        {facts.length === 0 ? null : (
          <Typography.Text type="secondary" ellipsis style={{ fontSize: token.fontSizeSM }}>
            {facts.join(' · ')}
          </Typography.Text>
        )}
      </Flex>

      {/*
       * Место под полосу прокрутки зарезервировано всегда, а не с её появлением: иначе
       * при первом переполнении ширина колонки уменьшалась на ширину полосы и всё
       * содержимое смещалось.
       */}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          scrollbarGutter: 'stable both-edges',
          paddingInline: token.paddingLG,
          paddingBlock: token.padding,
        }}
      >
        <Flex vertical gap={token.marginLG} style={{ maxWidth: 880, marginInline: 'auto' }}>
          {sessionError === null ? null : (
            <Alert
              type="error"
              showIcon
              message="Сессия не получена"
              description={sessionError.message}
            />
          )}
          {eventsError === null || sessionError !== null ? null : (
            <Alert
              type="error"
              showIcon
              message="Журнал сессии не получен"
              description={eventsError.message}
            />
          )}
          {/* Пустота журнала утверждается только по прочитанному журналу: пока запрос не
              вернулся — в том числе когда его повтор отложен до возврата на вкладку, — на
              странице заполнитель, а не сообщение об отсутствии событий. */}
          {history === undefined && eventsError === null && turns.length === 0 ? (
            <Skeleton active paragraph={{ rows: 3 }} />
          ) : null}
          {history !== undefined && turns.length === 0 ? (
            <Typography.Text type="secondary">Событий в журнале пока нет</Typography.Text>
          ) : null}

          {session === undefined
            ? null
            : turns.map((turn) => <TurnBlockView key={turn.key} turn={turn} kind={session.kind} />)}
          <div ref={bottomRef} />
        </Flex>
      </div>
    </Flex>
  )
}
