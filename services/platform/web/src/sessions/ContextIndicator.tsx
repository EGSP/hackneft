import { RightOutlined } from '@ant-design/icons'
import { Button, Divider, Flex, Popover, Progress, Tooltip, Typography, theme } from 'antd'
import { useState } from 'react'

import type { ContextItem, ContextSegment, ContextSegmentKey, SessionContextResponse } from './api'
import { useSessionContext } from './queries'
import type { TokenUsage } from './turns'

const segmentLabels: Record<ContextSegmentKey, string> = {
  system_prompt: 'Системный промпт',
  mcp_instructions: 'Инструкции MCP',
  tools: 'Инструменты',
  mcp_tools: 'Инструменты MCP',
  messages: 'Сообщения',
}

const messageLabels: Record<string, string> = {
  user: 'Сообщения пользователя',
  assistant: 'Ответы модели',
  tool_calls: 'Вызовы инструментов',
  tool_results: 'Результаты инструментов',
}

/**
 * Индикатор заполненности контекста сессии.
 *
 * Кольцо показывает долю окна модели, занятую запросом, каким он ушёл бы сейчас; по
 * нажатию раскрываются состав и расход токенов за сессию. Состав — оценка сервера
 * токенизатором текущей модели сессии: провайдер сообщает число токенов одной суммой на
 * запрос и на части его не делит. Расход — числа провайдера, записанные в журнал.
 *
 * Контекст запрашивается заново по завершении хода и при смене модели. Между ходами он не
 * меняется, а следующий ход начнётся с того состояния, в котором закончился предыдущий, —
 * его индикатор и показывает.
 */
export function ContextIndicator({
  sessionId,
  modelName,
  completedTurns,
  usage,
}: {
  readonly sessionId: string
  readonly modelName: string | null
  /** Число завершённых ходов. Служит версией контекста: с каждым ходом он пересчитывается. */
  readonly completedTurns: number
  /** Расход токенов за сессию. */
  readonly usage: TokenUsage
}) {
  const { token } = theme.useToken()
  const [open, setOpen] = useState(false)

  const { data, error, isFetching } = useSessionContext(
    sessionId,
    `${completedTurns}:${modelName ?? ''}`,
  )

  const share = data === undefined ? 0 : data.used / data.window
  const percent = Math.min(100, Math.round(share * 100))
  const color =
    share >= 0.95 ? token.colorError : share >= 0.8 ? token.colorWarning : token.colorPrimary

  const hint =
    error !== null
      ? `Контекст не вычислен: ${error.message}`
      : data === undefined
        ? 'Контекст вычисляется'
        : `Контекст: ${formatTokens(data.used)} из ${formatTokens(data.window)} ` +
          `(${formatPercent(share)})`

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="bottomRight"
      arrow={false}
      content={
        data === undefined ? (
          <Typography.Text type="secondary">{hint}</Typography.Text>
        ) : (
          <ContextBreakdown context={data} usage={usage} refreshing={isFetching} />
        )
      }
    >
      <Tooltip title={open ? null : hint}>
        <Button
          type="text"
          size="small"
          aria-label={hint}
          icon={
            <Progress
              type="circle"
              size={16}
              percent={percent}
              showInfo={false}
              strokeWidth={16}
              strokeColor={color}
              trailColor={token.colorFillSecondary}
            />
          }
        />
      </Tooltip>
    </Popover>
  )
}

function ContextBreakdown({
  context,
  usage,
  refreshing,
}: {
  readonly context: SessionContextResponse
  readonly usage: TokenUsage
  readonly refreshing: boolean
}) {
  const { token } = theme.useToken()
  const [expanded, setExpanded] = useState<ReadonlySet<ContextSegmentKey>>(new Set())

  const colors: Record<ContextSegmentKey, string> = {
    system_prompt: token.colorTextTertiary,
    mcp_instructions: token.purple,
    tools: token.orange,
    mcp_tools: token.green,
    messages: token.blue,
  }
  const free = Math.max(0, context.window - context.used)

  const toggle = (key: ContextSegmentKey): void =>
    setExpanded((previous) => {
      const next = new Set(previous)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })

  return (
    <Flex vertical gap={token.marginSM} style={{ width: 360, maxWidth: '80vw' }}>
      <Flex align="baseline" justify="space-between" gap={token.marginXS}>
        <Typography.Text strong>Контекст</Typography.Text>
        <Typography.Text type="secondary" style={{ whiteSpace: 'nowrap' }}>
          {formatTokens(context.used)} / {formatTokens(context.window)} (
          {formatPercent(context.used / context.window)})
        </Typography.Text>
      </Flex>

      <Flex
        style={{
          height: 6,
          borderRadius: 3,
          overflow: 'hidden',
          background: token.colorFillSecondary,
          opacity: refreshing ? 0.6 : 1,
        }}
      >
        {context.segments.map((segment) => (
          <div
            key={segment.key}
            style={{
              width: `${(segment.tokens / context.window) * 100}%`,
              background: colors[segment.key],
            }}
          />
        ))}
      </Flex>

      <Flex vertical gap={token.marginXXS}>
        {context.segments.map((segment) => (
          <SegmentRow
            key={segment.key}
            segment={segment}
            window={context.window}
            color={colors[segment.key]}
            expanded={expanded.has(segment.key)}
            onToggle={() => toggle(segment.key)}
          />
        ))}
        <Row
          color={token.colorFillSecondary}
          label="Свободно"
          tokens={free}
          window={context.window}
        />
      </Flex>

      <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
        Оценка по составу текста, токенизатор {context.tokenizer}. Модель {context.model}
        {context.windowSource === 'assumed'
          ? `: размер окна неизвестен, принято ${formatTokens(context.window)}.`
          : '.'}
      </Typography.Text>

      {usage.prompt + usage.completion > 0 ? (
        <>
          <Divider style={{ margin: 0 }} />
          <UsageSummary usage={usage} />
        </>
      ) : null}
    </Flex>
  )
}

/**
 * Расход токенов за сессию одной полосой из двух частей — входные и выходные токены.
 * Сумма обычно больше контекста: каждое обращение к модели отправляет переписку заново.
 */
function UsageSummary({ usage }: { readonly usage: TokenUsage }) {
  const { token } = theme.useToken()
  const total = usage.prompt + usage.completion
  const parts = [
    { label: 'Входные', tokens: usage.prompt, color: token.cyan },
    { label: 'Выходные', tokens: usage.completion, color: token.magenta },
  ]

  return (
    <Flex vertical gap={token.marginSM}>
      <Flex align="baseline" justify="space-between" gap={token.marginXS}>
        <Typography.Text strong>Расход за сессию</Typography.Text>
        <Typography.Text type="secondary" style={{ whiteSpace: 'nowrap' }}>
          {formatTokens(total)}
        </Typography.Text>
      </Flex>

      <Flex
        style={{
          height: 6,
          borderRadius: 3,
          overflow: 'hidden',
          background: token.colorFillSecondary,
        }}
      >
        {parts.map((part) => (
          <div
            key={part.label}
            style={{ width: `${(part.tokens / total) * 100}%`, background: part.color }}
          />
        ))}
      </Flex>

      <Flex vertical gap={token.marginXXS}>
        {parts.map((part) => (
          <Row
            key={part.label}
            color={part.color}
            label={part.label}
            tokens={part.tokens}
            window={total}
          />
        ))}
      </Flex>

      <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
        Сумма по всем обращениям к модели по данным провайдера.
      </Typography.Text>
    </Flex>
  )
}

/**
 * Часть контекста. Раскрывается, если в ней больше одной составляющей: у системного
 * промпта раскрывать нечего.
 */
function SegmentRow({
  segment,
  window,
  color,
  expanded,
  onToggle,
}: {
  readonly segment: ContextSegment
  readonly window: number
  readonly color: string
  readonly expanded: boolean
  readonly onToggle: () => void
}) {
  const { token } = theme.useToken()
  const expandable = segment.items.length > 1 || segment.key === 'messages'
  const count = segment.items.reduce((total, item) => total + item.count, 0)
  const label =
    segment.key === 'system_prompt'
      ? segmentLabels[segment.key]
      : `${segmentLabels[segment.key]}: ${count}`

  return (
    <>
      <Row
        color={color}
        label={label}
        tokens={segment.tokens}
        window={window}
        expanded={expandable ? expanded : undefined}
        onToggle={expandable ? onToggle : undefined}
      />
      {expandable && expanded ? (
        <Flex
          vertical
          gap={token.marginXXS}
          style={{ maxHeight: 180, overflowY: 'auto', paddingInlineStart: token.paddingLG }}
        >
          {segment.items.map((item) => (
            <Row
              key={item.name}
              label={itemLabel(segment.key, item)}
              tokens={item.tokens}
              window={window}
              secondary
            />
          ))}
        </Flex>
      ) : null}
    </>
  )
}

function Row({
  color,
  label,
  tokens,
  window,
  expanded,
  onToggle,
  secondary = false,
}: {
  readonly color?: string
  readonly label: string
  readonly tokens: number
  readonly window: number
  readonly expanded?: boolean
  readonly onToggle?: () => void
  readonly secondary?: boolean
}) {
  const { token } = theme.useToken()
  const type = secondary ? 'secondary' : undefined

  return (
    <Flex
      align="center"
      gap={token.marginXS}
      onClick={onToggle}
      style={{ cursor: onToggle === undefined ? 'default' : 'pointer', minWidth: 0 }}
    >
      {onToggle === undefined ? (
        <span style={{ width: 10, flex: 'none' }} />
      ) : (
        <RightOutlined
          style={{
            width: 10,
            flex: 'none',
            fontSize: 9,
            color: token.colorTextTertiary,
            transform: expanded ? 'rotate(90deg)' : undefined,
            transition: 'transform 0.15s',
          }}
        />
      )}
      {color === undefined ? null : (
        <span style={{ width: 8, height: 8, borderRadius: 2, flex: 'none', background: color }} />
      )}
      <Typography.Text type={type} ellipsis={{ tooltip: label }} style={{ flex: 1 }}>
        {label}
      </Typography.Text>
      <Typography.Text type={type} style={{ fontVariantNumeric: 'tabular-nums' }}>
        {formatTokens(tokens)}
      </Typography.Text>
      <Typography.Text
        type="secondary"
        style={{ width: 48, textAlign: 'end', fontVariantNumeric: 'tabular-nums' }}
      >
        {formatPercent(tokens / window)}
      </Typography.Text>
    </Flex>
  )
}

function itemLabel(key: ContextSegmentKey, item: ContextItem): string {
  if (key !== 'messages') {
    return item.name
  }
  return `${messageLabels[item.name] ?? item.name}: ${item.count}`
}

/** 812 — «812», 79 600 — «79,6k», 1 048 576 — «1M». */
function formatTokens(value: number): string {
  if (value < 1000) {
    return String(value)
  }
  const [divisor, suffix] = value >= 1_000_000 ? [1_000_000, 'M'] : [1000, 'k']
  const scaled = value / divisor
  return `${scaled.toLocaleString('ru', { maximumFractionDigits: scaled < 100 ? 1 : 0 })}${suffix}`
}

/**
 * Доля в процентах: число отделено от знака неразрывным пробелом, как принято в проекте.
 * Для долей меньше десятой процента оставлен знак «<»: подпись стоит в узкой колонке.
 */
function formatPercent(share: number): string {
  const percent = share * 100
  if (percent > 0 && percent < 0.1) {
    return '<0,1 %'
  }
  return `${percent.toLocaleString('ru', { maximumFractionDigits: 1 })} %`
}
