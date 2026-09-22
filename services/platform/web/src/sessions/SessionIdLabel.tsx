import { Typography, theme } from 'antd'

/**
 * Идентификатор сессии в заголовке.
 *
 * Нужен потому, что по журналам, трассам и обращениям к API сессия опознаётся именно
 * идентификатором, а в интерфейсе виден только заголовок. Строка сделана копируемой: её
 * переносят в запрос либо в поиск по приёмнику трассировок, и набирать её вручную —
 * тридцать шесть знаков — неразумно.
 *
 * Сжатию не подлежит: обрезанный идентификатор бесполезен, поэтому место уступает
 * заголовок, у которого есть многоточие.
 */
export function SessionIdLabel({ sessionId }: { readonly sessionId: string }) {
  const { token } = theme.useToken()

  return (
    <Typography.Text
      type="secondary"
      copyable={{
        text: sessionId,
        tooltips: ['Скопировать идентификатор сессии', 'Скопировано'],
      }}
      style={{
        flex: '0 0 auto',
        fontFamily: token.fontFamilyCode,
        fontSize: token.fontSizeSM,
        whiteSpace: 'nowrap',
      }}
    >
      {sessionId}
    </Typography.Text>
  )
}
