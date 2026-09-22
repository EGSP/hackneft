import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  MessageOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import { Conversations, type ConversationsProps } from '@ant-design/x'
import { Alert, Empty, Flex, Segmented, Skeleton, Typography, theme } from 'antd'
import dayjs from 'dayjs'
import { useState, type ReactNode } from 'react'
import { useMatch, useNavigate } from 'react-router-dom'

import type { Session, SessionKind } from './api'
import { kindLabel } from './labels'
import { useSessions } from './queries'

type KindFilter = SessionKind | 'all'

const FILTER_OPTIONS: { label: string; value: KindFilter }[] = [
  { label: 'Все', value: 'all' },
  { label: 'Агенты', value: 'agent' },
  { label: 'Чаты', value: 'chat' },
]

/**
 * Значок сессии в перечне. Состояние показывается у каждой строки, чтобы идущие и
 * завершившиеся отказом сессии находились взглядом. У ожидающей сессии состояние не
 * примечательно, и на месте значка стоит её вид: без значка название съезжало бы влево
 * относительно соседних строк.
 */
function statusIcon(
  session: Session,
  token: ReturnType<typeof theme.useToken>['token'],
): ReactNode {
  switch (session.status) {
    case 'running':
      return <LoadingOutlined />
    case 'completed':
      return <CheckCircleOutlined style={{ color: token.colorSuccess }} />
    case 'failed':
      return <CloseCircleOutlined style={{ color: token.colorError }} />
    case 'idle':
      return session.kind === 'agent' ? (
        <RobotOutlined style={{ color: token.colorTextTertiary }} />
      ) : (
        <MessageOutlined style={{ color: token.colorTextTertiary }} />
      )
  }
}

/**
 * Перечень сессий. Используется компонент `Conversations` — штатный для перечня диалогов:
 * он берёт на себя выделение активного и усечение длинных названий.
 *
 * В перечне только корневые сессии: дочерние показываются в журнале порождающей сессии, в
 * том месте, где агент их породил. Создания, переименования и удаления здесь нет — страница
 * служит прослеживаемости, а сессии создают платформа и сами агенты.
 *
 * Под названием указаны вид сессии и время создания: названия агентских сессий, которые
 * создаёт платформа, однотипны, и различаются такие сессии прежде всего временем.
 */
export function SessionList() {
  const { token } = theme.useToken()
  const navigate = useNavigate()
  const [filter, setFilter] = useState<KindFilter>('all')
  const { data: sessions, error } = useSessions(filter === 'all' ? undefined : filter)

  // Перечень стоит на уровне раздела, выше маршрута сессии, поэтому выбранная сессия
  // определяется по адресу, а не параметром маршрута.
  const activeId = useMatch('/sessions/:sessionId')?.params.sessionId

  const items: ConversationsProps['items'] = (sessions ?? []).map((session) => ({
    key: session.id,
    icon: statusIcon(session, token),
    label: (
      <>
        <span
          title={session.title}
          style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {session.title}
        </span>
        <span
          style={{
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            fontSize: token.fontSizeSM,
            color: token.colorTextTertiary,
          }}
        >
          {kindLabel[session.kind]} · {dayjs(session.createdAt).format('DD.MM.YYYY HH:mm')}
        </span>
      </>
    ),
  }))

  return (
    <Flex vertical gap={token.marginSM} style={{ height: '100%', padding: token.padding }}>
      <Flex align="baseline" justify="space-between" gap={token.marginXS}>
        <Typography.Text strong>Сессии</Typography.Text>
        {sessions === undefined ? null : (
          <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
            Всего: {sessions.length}
          </Typography.Text>
        )}
      </Flex>

      <Segmented<KindFilter> block options={FILTER_OPTIONS} value={filter} onChange={setFilter} />

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}>
        {sessions === undefined && error === null ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : null}

        {error === null ? null : (
          <Alert
            type="error"
            showIcon
            message="Перечень сессий не получен"
            description={error.message}
          />
        )}

        {sessions !== undefined && items.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="Сессий пока нет"
            style={{ marginBlockStart: token.marginXL }}
          />
        ) : null}

        {items.length > 0 ? (
          <Conversations
            items={items}
            activeKey={activeId}
            onActiveChange={(key) => navigate(`/sessions/${encodeURIComponent(key)}`)}
            // Строка вмещает две строки текста: высота элемента по умолчанию рассчитана
            // на одну.
            styles={{
              item: {
                height: 'auto',
                minHeight: token.controlHeightLG,
                paddingBlock: token.paddingXXS,
              },
            }}
            style={{ padding: 0 }}
          />
        ) : null}
      </div>
    </Flex>
  )
}
