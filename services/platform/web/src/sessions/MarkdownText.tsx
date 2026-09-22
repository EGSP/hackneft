import { Typography, theme } from 'antd'
import type { ReactNode } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Вывод текста модели с разбором Markdown.
 *
 * Разбор применяется ко всему тексту без условий: обычный текст проходит через него без
 * изменений, поэтому проверять наличие разметки не нужно. Обратное — вывод как есть — давало
 * бы видимые звёздочки и решётки, потому что модели размечают ответ по умолчанию.
 *
 * Оформление берётся из токенов темы, а собственные размеры назначены только заголовкам:
 * заголовок первого уровня внутри сообщения крупнее самого сообщения и разрывает переписку,
 * тогда как в отдельном документе он уместен.
 */
export function MarkdownText({ text }: { readonly text: string }) {
  const { token } = theme.useToken()

  const heading = (level: number) => {
    const sizes = [token.fontSizeHeading5, token.fontSizeHeading5, token.fontSizeLG]
    return function Heading({ children }: { readonly children?: ReactNode }) {
      return (
        <Typography.Text
          strong
          style={{
            display: 'block',
            fontSize: sizes[Math.min(level, sizes.length) - 1],
            marginBlock: `${token.marginXS}px 0`,
          }}
        >
          {children}
        </Typography.Text>
      )
    }
  }

  const components: Components = {
    h1: heading(1),
    h2: heading(2),
    h3: heading(3),
    h4: heading(3),
    h5: heading(3),
    h6: heading(3),

    p: ({ children }) => (
      <Typography.Paragraph style={{ marginBottom: token.marginXS }}>
        {children}
      </Typography.Paragraph>
    ),

    ul: ({ children }) => (
      <ul style={{ marginBlock: 0, paddingInlineStart: token.paddingLG }}>{children}</ul>
    ),
    ol: ({ children }) => (
      <ol style={{ marginBlock: 0, paddingInlineStart: token.paddingLG }}>{children}</ol>
    ),

    a: ({ href, children }) => (
      <Typography.Link href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </Typography.Link>
    ),

    code: ({ children, className }) =>
      // Разметка различает встроенный код и блок наличием языка либо переводов строки;
      // react-markdown передаёт оба одним узлом, поэтому вид выбирается здесь.
      className === undefined && !String(children).includes('\n') ? (
        <Typography.Text code>{children}</Typography.Text>
      ) : (
        <Typography.Text
          code
          style={{
            display: 'block',
            whiteSpace: 'pre',
            overflowX: 'auto',
            padding: token.paddingXS,
          }}
        >
          {children}
        </Typography.Text>
      ),
    pre: ({ children }) => <>{children}</>,

    blockquote: ({ children }) => (
      <div
        style={{
          borderInlineStart: `2px solid ${token.colorBorder}`,
          paddingInlineStart: token.paddingSM,
          color: token.colorTextSecondary,
        }}
      >
        {children}
      </div>
    ),

    // Таблица шире сообщения прокручивается внутри своей области: иначе горизонтальная
    // прокрутка появилась бы у всей страницы.
    table: ({ children }) => (
      <div style={{ overflowX: 'auto', marginBlockEnd: token.marginXS }}>
        <table style={{ borderCollapse: 'collapse', fontSize: token.fontSizeSM, width: '100%' }}>
          {children}
        </table>
      </div>
    ),
    th: ({ children, style }) => (
      <th
        style={{
          ...style,
          textAlign: 'start',
          padding: `${token.paddingXXS}px ${token.paddingXS}px`,
          borderBottom: `1px solid ${token.colorBorder}`,
          whiteSpace: 'nowrap',
        }}
      >
        {children}
      </th>
    ),
    td: ({ children, style }) => (
      <td
        style={{
          ...style,
          padding: `${token.paddingXXS}px ${token.paddingXS}px`,
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        {children}
      </td>
    ),

    hr: () => <div style={{ borderBlockStart: `1px solid ${token.colorBorderSecondary}` }} />,

    // Изображения не показываются: платформа их не хранит, а адрес модель придумывает.
    // Вместо разорванного изображения выводится пометка — молчаливое исчезновение части
    // ответа хуже видимого следа.
    img: ({ alt }) => (
      <Typography.Text type="secondary" italic>
        [изображение не выводится{alt === undefined || alt === '' ? '' : `: ${alt}`}]
      </Typography.Text>
    ),
  }

  return (
    <div style={{ minWidth: 0 }}>
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </Markdown>
    </div>
  )
}
