import { RobotOutlined } from '@ant-design/icons'
import { Welcome } from '@ant-design/x'
import { Flex, Tag, Tooltip, Typography, theme } from 'antd'

import { useTools } from './queries'

/**
 * Раздел сессий до выбора сессии: назначение страницы и инструменты, доступные агентам.
 *
 * Перечень инструментов показан здесь потому, что по нему читается журнал любой сессии:
 * вызовы в нём называют инструменты именами, а назначение инструмента видно в подсказке.
 */
export function EmptyState() {
  const { token } = theme.useToken()
  const { data: tools } = useTools()

  return (
    <Flex align="center" justify="center" style={{ flex: 1, padding: token.paddingLG }}>
      <Flex vertical gap={token.margin} style={{ maxWidth: 620 }}>
        <Welcome
          icon={<RobotOutlined style={{ fontSize: 32 }} />}
          title="Выберите сессию в перечне справа"
          description={
            'Журнал сессии показывает ход работы агента: постановку задачи, обращения к модели, ' +
            'вызовы инструментов с аргументами и результатами, порождённые дочерние сессии и ' +
            'итог. Сессии создают платформа и сами агенты, страница их только показывает.'
          }
          variant="borderless"
        />

        {tools !== undefined && tools.length > 0 ? (
          <Flex vertical gap={token.marginXS}>
            <Typography.Text type="secondary">Инструменты агентов</Typography.Text>
            <Flex wrap="wrap" gap={token.marginXXS}>
              {tools.map((tool) => (
                <Tooltip key={tool.name} title={tool.description}>
                  <Tag bordered={false} color={tool.source === 'mcp' ? 'blue' : undefined}>
                    {tool.name}
                  </Tag>
                </Tooltip>
              ))}
            </Flex>
            {tools.some((tool) => tool.source === 'mcp') ? (
              <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
                Синим отмечены инструменты серверов MCP, в том числе платформы; остальные встроены в
                ИИ-сервис.
              </Typography.Text>
            ) : null}
          </Flex>
        ) : null}
      </Flex>
    </Flex>
  )
}
