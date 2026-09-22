import { Alert, Collapse, Drawer, Flex, Skeleton, Tag, Typography, theme } from 'antd'
import dayjs from 'dayjs'
import type { ReactNode } from 'react'

import { useSnapshot } from './queries'

/**
 * Снимок запроса к модели: с какими указаниями и каким набором инструментов работал агент.
 *
 * Журнал хранит переписку, а постоянную часть запроса ИИ-сервис держит отдельно, одной
 * записью на каждое различающееся содержимое, и событие начала шага ссылается на неё.
 * В журнале этих указаний не видно, хотя поведение агента определяется ими наравне с
 * постановкой задачи, поэтому снимок показывается по ссылке из сводки хода. Интерфейс xip
 * снимки не показывает; здесь они добавлены ради прослеживаемости.
 */
export function SnapshotDrawer({
  snapshotId,
  open,
  onClose,
}: {
  readonly snapshotId: string
  readonly open: boolean
  readonly onClose: () => void
}) {
  const { token } = theme.useToken()
  const { data, error } = useSnapshot(snapshotId, open)

  const textStyle = {
    margin: 0,
    padding: token.paddingSM,
    background: token.colorFillQuaternary,
    borderRadius: token.borderRadiusSM,
    fontFamily: token.fontFamilyCode,
    fontSize: token.fontSizeSM,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  } as const

  return (
    <Drawer open={open} onClose={onClose} width={720} title="Запрос к модели">
      {error !== null ? (
        <Alert
          type="error"
          showIcon
          message="Снимок запроса не получен"
          description={error.message}
        />
      ) : data === undefined ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : (
        <Flex vertical gap={token.marginLG}>
          <Section title="Указания сервиса">
            <pre style={textStyle}>{data.prompt}</pre>
          </Section>

          {data.sections.length === 0 ? null : (
            <Section title="Инструкции серверов MCP">
              {data.sections.map((section, index) => (
                <pre key={index} style={textStyle}>
                  {section}
                </pre>
              ))}
            </Section>
          )}

          <Section title={`Инструменты: ${data.tools.length}`}>
            <Collapse
              size="small"
              items={data.tools.map((tool) => ({
                key: tool.name,
                label: (
                  <Flex align="center" gap={token.marginXS} style={{ minWidth: 0 }}>
                    <Typography.Text strong>{tool.name}</Typography.Text>
                    <Tag bordered={false} color={tool.source === 'mcp' ? 'blue' : undefined}>
                      {tool.source === 'mcp' ? 'MCP' : 'встроенный'}
                    </Tag>
                  </Flex>
                ),
                children: (
                  <Flex vertical gap={token.marginXS}>
                    <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                      {tool.description}
                    </Typography.Paragraph>
                    <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
                      Параметры
                    </Typography.Text>
                    <pre style={textStyle}>{JSON.stringify(tool.parameters, null, 2)}</pre>
                  </Flex>
                ),
              }))}
            />
          </Section>

          <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
            Снимок {data.id} записан {dayjs(data.createdAt).format('DD.MM.YYYY HH:mm:ss')}. Один
            снимок служит всем ходам с тем же содержимым запроса.
          </Typography.Text>
        </Flex>
      )}
    </Drawer>
  )
}

function Section({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  const { token } = theme.useToken()

  return (
    <Flex vertical gap={token.marginXS}>
      <Typography.Text strong>{title}</Typography.Text>
      {children}
    </Flex>
  )
}
