import { ExportOutlined, MessageOutlined, RobotOutlined } from '@ant-design/icons'
import { Alert, Button, Collapse, Flex, Skeleton, Tag, Tooltip, Typography, theme } from 'antd'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import type { SessionKind } from './api'
import { kindLabel, statusView } from './labels'
import { useSession, useSessionEvents } from './queries'
import { TurnBlockView } from './TurnBlock'
import { groupTurns } from './turns'
import { useSessionStream } from './useSessionStream'

/**
 * Дочерняя сессия внутри журнала родителя.
 *
 * Содержимое подгружается только при раскрытии: журнал и поток дочерней сессии читаются
 * теми же запросами, что и у любой другой, и открывать соединение на каждую упомянутую
 * сессию заранее означало бы держать их по числу порождённых сессий.
 *
 * Дочерние сессии в перечне страницы не показываются, поэтому карточка позволяет открыть
 * сессию отдельно: так её журнал читается во всю ширину, а у открытой сессии есть переход
 * обратно к порождающей.
 */
export function ChildSessionCard({
  childId,
  childKind,
  title,
}: {
  readonly childId: string
  readonly childKind: SessionKind
  readonly title: string
}) {
  const { token } = theme.useToken()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const { data: session } = useSession(childId)
  const status = session === undefined ? undefined : statusView[session.status]

  return (
    <Collapse
      size="small"
      activeKey={open ? [childId] : []}
      onChange={(keys) => setOpen(keys.length > 0)}
      items={[
        {
          key: childId,
          label: (
            <Flex align="center" gap={token.marginXS} style={{ minWidth: 0 }}>
              {childKind === 'agent' ? (
                <RobotOutlined style={{ color: token.colorTextTertiary }} />
              ) : (
                <MessageOutlined style={{ color: token.colorTextTertiary }} />
              )}
              <Typography.Text type="secondary" style={{ flexShrink: 0 }}>
                {kindLabel[childKind]}
              </Typography.Text>
              <Typography.Text ellipsis>{session?.title ?? title}</Typography.Text>
              {status === undefined ? null : (
                <Tag color={status.color} style={{ marginInlineStart: 'auto', marginInlineEnd: 0 }}>
                  {status.label}
                </Tag>
              )}
            </Flex>
          ),
          extra: (
            <Tooltip title="Открыть отдельно">
              <Button
                type="text"
                size="small"
                icon={<ExportOutlined />}
                aria-label="Открыть дочернюю сессию отдельно"
                onClick={(event) => {
                  // Щелчок по заголовку раскрывает карточку, а кнопка лежит в заголовке.
                  event.stopPropagation()
                  navigate(`/sessions/${encodeURIComponent(childId)}`)
                }}
              />
            </Tooltip>
          ),
          children: open ? <ChildSessionBody id={childId} kind={childKind} /> : null,
        },
      ]}
    />
  )
}

/**
 * Содержимое дочерней сессии. Вынесено отдельным компонентом намеренно: подписка на поток
 * начинается при его создании, поэтому свёрнутая карточка не должна его порождать.
 */
function ChildSessionBody({ id, kind }: { readonly id: string; readonly kind: SessionKind }) {
  const { token } = theme.useToken()
  const { data: history, error } = useSessionEvents(id)
  const events = useSessionStream(id, history)
  const turns = useMemo(() => groupTurns(events), [events])

  if (error !== null) {
    return (
      <Alert type="error" showIcon message="Журнал сессии не получен" description={error.message} />
    )
  }
  if (turns.length === 0) {
    // Как и в окне сессии, пустота утверждается только по прочитанному журналу.
    return history === undefined ? (
      <Skeleton active paragraph={{ rows: 2 }} />
    ) : (
      <Typography.Text type="secondary">Событий в журнале пока нет</Typography.Text>
    )
  }

  return (
    <Flex vertical gap={token.margin} style={{ minWidth: 0 }}>
      {turns.map((turn) => (
        <TurnBlockView key={turn.key} turn={turn} kind={kind} />
      ))}
    </Flex>
  )
}
