import {
  BulbOutlined,
  ClusterOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  ProfileOutlined,
  RobotOutlined,
  ToolOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { Bubble } from '@ant-design/x'
import { Alert, Avatar, Collapse, Flex, Spin, Typography, theme } from 'antd'
import dayjs from 'dayjs'
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'

import type { SessionKind, TurnFailureReason } from './api'
import { ChildSessionCard } from './ChildSessionCard'
import { MarkdownText } from './MarkdownText'
import { SnapshotDrawer } from './SnapshotDrawer'
import type { TurnBlock as Turn, WorkGroup, WorkItem } from './turns'

const failureLabel: Record<TurnFailureReason, string> = {
  model_missing: 'модели сессии нет в справочнике',
  model_unavailable: 'модель недоступна у провайдера',
  model_error: 'ошибка модели',
  step_limit: 'превышен предел шагов',
  output_limit: 'исчерпан бюджет вывода',
  aborted: 'прервано по команде',
  internal: 'внутренняя ошибка',
}

type Token = ReturnType<typeof theme.useToken>['token']

/**
 * Оформление блока данных: моноширинный шрифт и предел высоты. Результат бывает в тысячи
 * знаков, и без предела один вызов вытеснял бы собой всю переписку.
 */
const preStyle = (token: Token) =>
  ({
    margin: 0,
    padding: token.paddingXS,
    maxHeight: 320,
    overflow: 'auto',
    background: token.colorFillQuaternary,
    borderRadius: token.borderRadiusSM,
    fontFamily: token.fontFamilyCode,
    fontSize: token.fontSizeSM,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  }) as const

const seconds = new Intl.NumberFormat('ru', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

/** Длительность с десятичной запятой: «850 мс», «3,2 с». */
const duration = (ms: number): string => (ms < 1000 ? `${ms} мс` : `${seconds.format(ms / 1000)} с`)

const shorten = (value: string, limit: number): string =>
  value.length > limit ? `${value.slice(0, limit)}…` : value

/** Подписи интерфейса начинаются с заглавной буквы. */
const capitalize = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1)

/** Аргументы в компактной форме: без внешних скобок и кавычек у имён полей. */
function compactArgs(rawArguments: string): string {
  const flat = rawArguments.replace(/\s+/g, ' ').trim()
  if (flat === '' || flat === '{}') {
    return ''
  }
  const inner = flat.startsWith('{') && flat.endsWith('}') ? flat.slice(1, -1) : flat
  // Предел велик намеренно: по ширине строку обрезает разметка, а этот предел лишь
  // не даёт положить в неё килобайты аргументов.
  return shorten(inner.replace(/"([A-Za-z_][\w]*)":/g, '$1: '), 200)
}

/**
 * Результат вызова в читаемом виде.
 *
 * В журнале он лежит строкой JSON — той, что получила модель. Показанная как есть, она
 * читается плохо: данные идут одной строкой с экранированными кавычками и переводами строк.
 * Поэтому объект и массив выводятся с отступами, а строка, закодированная в JSON,
 * разворачивается обратно — перевод строки должен остаться переводом строки, а не парой
 * символов. Усечённый результат не разбирается: оборванный JSON и не должен разбираться.
 *
 * Отказ инструмента разбирается отдельно: модели он приходит объектом из сообщения и
 * подсказки, а человеку нужен смысл, а не устройство объекта.
 */
function resultText(result: string, ok: boolean | undefined): string {
  if (ok === false) {
    try {
      const parsed = JSON.parse(result) as { message?: unknown; hint?: unknown }
      const parts = [parsed.message, parsed.hint].filter(
        (part): part is string => typeof part === 'string',
      )
      if (parts.length > 0) {
        return parts.join('\n\n')
      }
    } catch {
      // Отказ пришёл не в ожидаемом виде — показывается как есть.
    }
  }
  return readable(result)
}

/** Разворачивает JSON в читаемый вид; всё прочее оставляет без изменений. */
function readable(value: string): string {
  const trimmed = value.trim()
  const first = trimmed[0]
  if (first !== '{' && first !== '[' && first !== '"') {
    return value
  }
  try {
    const parsed: unknown = JSON.parse(trimmed)
    return typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2)
  } catch {
    return value
  }
}

type ToolWork = Extract<WorkItem, { kind: 'tool' }>
type ReasoningWork = Extract<WorkItem, { kind: 'reasoning' }>

/**
 * Строка ряда фонового элемента: вид, имя, приметы аргументов и величина у правого края.
 *
 * Оформление у вызова и размышления одно, иначе перечень действий читается как набор
 * разнородных карточек. Строка одна: имя не переносится и не обрезается, а место под него
 * освобождают аргументы — они приглушены и обрезаются, поскольку в свёрнутом виде служат лишь
 * приметой вызова. Величина у правого края не сжимается вовсе.
 *
 * Исход вызова передаётся цветом имени, а не отдельным значком: значок перед именем удлинял
 * строку и дублировал то, что и так читается по имени.
 */
function RowLine({
  outcome,
  icon,
  name,
  detail,
  meta,
}: {
  /** Исход вызова. У размышления и у незавершённого вызова отсутствует. */
  readonly outcome?: 'success' | 'danger'
  readonly icon: ReactNode
  readonly name: string
  /** Аргументы вызова в сокращённой записи. */
  readonly detail?: string
  /** Длительность либо расход токенов. */
  readonly meta?: string
}) {
  const { token } = theme.useToken()

  return (
    <Flex align="center" gap={token.marginXS} style={{ minWidth: 0, overflow: 'hidden' }}>
      {icon}
      {/* Имя не сжимается: сокращать следует приметы, а не то, по чему вызов опознают. */}
      <Typography.Text strong type={outcome} style={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
        {name}
      </Typography.Text>
      {detail === undefined || detail === '' ? null : (
        <Typography.Text
          type="secondary"
          style={{
            flex: 1,
            minWidth: 0,
            fontFamily: token.fontFamilyCode,
            fontSize: token.fontSizeSM,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {detail}
        </Typography.Text>
      )}
      {meta === undefined ? null : (
        <Typography.Text
          type="secondary"
          style={{
            flexShrink: 0,
            whiteSpace: 'nowrap',
            fontSize: token.fontSizeSM,
            marginInlineStart: 'auto',
          }}
        >
          {meta}
        </Typography.Text>
      )}
    </Flex>
  )
}

/**
 * Вызов инструмента в свёрнутом виде: имя, окрашенное по исходу, приметы аргументов и
 * длительность.
 *
 * Пока вызов выполняется, на месте значка инструмента стоит указатель выполнения. Место
 * у них общее, поэтому по завершении строка не сдвигается.
 */
function ToolLine({ item }: { readonly item: ToolWork }) {
  const { token } = theme.useToken()
  const pending = item.result === undefined

  return (
    <RowLine
      outcome={pending ? undefined : item.ok === false ? 'danger' : 'success'}
      icon={
        pending ? <LoadingOutlined /> : <ToolOutlined style={{ color: token.colorTextTertiary }} />
      }
      name={item.name}
      detail={compactArgs(item.rawArguments)}
      meta={item.durationMs === undefined ? undefined : duration(item.durationMs)}
    />
  )
}

/**
 * Вызов инструмента в раскрытом виде: аргументы и результат целиком. В обычном чтении они не
 * нужны, а при разборе раскрываются.
 */
function ToolDetails({ item }: { readonly item: ToolWork }) {
  const { token } = theme.useToken()

  return (
    <Flex vertical gap={token.marginXS}>
      <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
        Аргументы
      </Typography.Text>
      <pre style={preStyle(token)}>
        {item.rawArguments === '' ? '{}' : readable(item.rawArguments)}
      </pre>

      <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
        Результат
      </Typography.Text>
      <pre
        style={{ ...preStyle(token), ...(item.ok === false ? { color: token.colorError } : {}) }}
      >
        {item.result === undefined ? 'Выполняется…' : resultText(item.result, item.ok)}
      </pre>
    </Flex>
  )
}

/** Размышление модели: та же строка, что у вызова. Читается редко и бывает длинным. */
function ReasoningLine({ item }: { readonly item: ReasoningWork }) {
  const { token } = theme.useToken()

  return (
    <RowLine
      icon={<BulbOutlined style={{ color: token.colorTextTertiary }} />}
      name="Размышление"
      meta={`${item.tokens} ток.`}
    />
  )
}

/**
 * Перечень фоновых элементов: вызовов инструментов и размышлений.
 *
 * Ряды — панели одного Collapse, разделённые линией, а не отдельные блоки с промежутком между
 * ними: линия отделяет ряд от соседнего и при малом промежутке, и перечень читается как список.
 * Высота ряда равна высоте стандартного элемента управления.
 *
 * Блоку подписи в заголовке задан `min-width: 0` правилом `.sessions-page
 * .ant-collapse-header-text` в index.css: antd назначает ему `flex: auto`, но автоматический
 * минимум ширины не снимает, и блок не сжимается уже своего содержимого. Без этого обрезка
 * аргументов многоточием не срабатывала бы, а длинные аргументы выталкивали строку за правый
 * край вместе с длительностью.
 */
function WorkRows({ items }: { readonly items: readonly WorkItem[] }) {
  const { token } = theme.useToken()
  const divider = `${token.lineWidth}px ${token.lineType} ${token.colorBorderSecondary}`

  return (
    <Collapse
      ghost
      size="small"
      items={items.map((item, index) => ({
        key: item.key,
        style: index === 0 ? undefined : { borderTop: divider },
        styles: {
          header: {
            alignItems: 'center',
            minHeight: token.controlHeight,
            paddingBlock: token.paddingXXS,
          },
          body: { padding: `0 ${token.paddingSM}px ${token.paddingSM}px` },
        },
        label: item.kind === 'tool' ? <ToolLine item={item} /> : <ReasoningLine item={item} />,
        children:
          item.kind === 'tool' ? (
            <ToolDetails item={item} />
          ) : (
            <Typography.Paragraph
              type="secondary"
              style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}
            >
              {item.text}
            </Typography.Paragraph>
          ),
      }))}
    />
  )
}

/**
 * Заголовок контейнера: сколько чего было сделано.
 *
 * Имена инструментов сюда не выносятся: в ходе их бывает десяток, перечень не помещается и
 * обрывается многоточием, ничего не сообщая. Имя видно в самом ряду, для того ряд и нужен.
 *
 * Подпись начинается со слова — «Вызовов: 4», а не «4 вызова»: подписи интерфейса начинаются
 * с заглавной буквы, а у подписи, начатой числом, её нет. Такая запись к тому же не требует
 * согласовывать слово с числом.
 */
function groupLabel(items: readonly WorkItem[]): string {
  const reasoning = items.filter((item) => item.kind === 'reasoning').length
  const tools = items.filter((item) => item.kind === 'tool').length

  const parts: string[] = []
  if (reasoning > 0) {
    parts.push(`размышлений: ${reasoning}`)
  }
  if (tools > 0) {
    parts.push(`вызовов: ${tools}`)
  }
  return capitalize(parts.join(', '))
}

/**
 * Контейнер действий агента.
 *
 * Раскрытием распоряжается только пользователь. Если бы контейнер раскрывался сам, пока
 * что-то выполняется, и сворачивался по завершении, он на каждой пачке вызовов менял бы
 * высоту дважды, смещая всё, что ниже, и сбивая прокрутку. Ход работы виден и по заголовку:
 * он несёт указатель выполнения и счётчики, которые пополняются без раскрытия.
 *
 * Изначально контейнер свёрнут: в обычном чтении важен ответ, а не перечень шагов. Поле
 * раскрытого контейнера лишено отступов, чтобы линии между рядами доходили до его рамки.
 */
function WorkGroupItem({ group }: { readonly group: WorkGroup }) {
  const { token } = theme.useToken()
  const [open, setOpen] = useState(false)

  const pending = group.items.some((item) => item.kind === 'tool' && item.result === undefined)
  const failed = group.items.some((item) => item.kind === 'tool' && item.ok === false)
  const total = group.items.reduce(
    (sum, item) => sum + (item.kind === 'tool' ? (item.durationMs ?? 0) : 0),
    0,
  )

  return (
    <Collapse
      size="small"
      activeKey={open ? [group.key] : []}
      onChange={(keys) => setOpen(keys.includes(group.key))}
      items={[
        {
          key: group.key,
          styles: { body: { padding: 0 } },
          label: (
            <Flex align="center" gap={token.marginXS} style={{ minWidth: 0 }}>
              {pending ? (
                <LoadingOutlined />
              ) : failed ? (
                <CloseCircleOutlined style={{ color: token.colorError }} />
              ) : (
                <ClusterOutlined style={{ color: token.colorTextTertiary }} />
              )}
              <Typography.Text type="secondary" ellipsis>
                {groupLabel(group.items)}
              </Typography.Text>
              {total === 0 ? null : (
                <Typography.Text
                  type="secondary"
                  style={{
                    flexShrink: 0,
                    whiteSpace: 'nowrap',
                    fontSize: token.fontSizeSM,
                    marginInlineStart: 'auto',
                  }}
                >
                  {duration(total)}
                </Typography.Text>
              )}
            </Flex>
          ),
          children: <WorkRows items={group.items} />,
        },
      ]}
    />
  )
}

/** Сколько строк входа видно в свёрнутом виде. */
const QUESTION_ROWS = 8

/**
 * Вход хода. Постановка задачи агентской сессии бывает длинной — платформа передаёт в ней
 * снимок показаний, — поэтому текст сверх нескольких строк сворачивается и раскрывается по
 * требованию: иначе вход вытеснял бы из виду работу агента и его ответ.
 *
 * Строки обрезает CSS, а не свойство `ellipsis` из antd. Ширина блока входа зависит от длины
 * текста, а antd подбирает обрезку, измеряя текст при заданной ширине: при ширине, которая
 * сама меняется от обрезки, подбор неустойчив. Ссылка раскрытия показывается, только если
 * текст действительно не поместился: это определяется измерением после отрисовки и
 * повторяется при изменении ширины.
 */
function QuestionText({ text }: { readonly text: string }) {
  const { token } = theme.useToken()
  const ref = useRef<HTMLDivElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [clipped, setClipped] = useState(false)

  useLayoutEffect(() => {
    const element = ref.current
    if (element === null || expanded) {
      return
    }
    const measure = () => setClipped(element.scrollHeight > element.clientHeight + 1)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [text, expanded])

  return (
    <Flex vertical align="flex-start" gap={token.marginXXS}>
      <div
        ref={ref}
        style={{
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
          ...(expanded
            ? {}
            : {
                display: '-webkit-box',
                WebkitBoxOrient: 'vertical',
                WebkitLineClamp: QUESTION_ROWS,
                overflow: 'hidden',
              }),
        }}
      >
        {text}
      </div>
      {clipped || expanded ? (
        <Typography.Link onClick={() => setExpanded((value) => !value)}>
          {expanded ? 'Свернуть' : 'Показать полностью'}
        </Typography.Link>
      ) : null}
    </Flex>
  )
}

/**
 * Один ход: вход, работа агента и ответ.
 *
 * Текст модели показывается на общем уровне независимо от того, пришёл он вместе с вызовами
 * инструментов или отдельным итоговым сообщением: и то и другое адресовано человеку.
 *
 * Вход агентской сессии — не реплика человека, а постановка задачи от платформы либо от
 * порождающей сессии, поэтому он подписан и отмечен другим значком.
 */
export function TurnBlockView({
  turn,
  kind,
}: {
  readonly turn: Turn
  /** Вид сессии, которой принадлежит ход. */
  readonly kind: SessionKind
}) {
  const { token } = theme.useToken()
  const [snapshotOpen, setSnapshotOpen] = useState(false)
  const hasContent =
    turn.items.length > 0 || turn.failure !== undefined || turn.sessionFailure !== undefined
  const task = kind === 'agent'

  // Сводка показывает то, что о ходе уже известно. Время начала и модель известны с первого
  // шага, а длительность, число шагов и расход — только по завершении: пока ход идёт, эти
  // числа меняются на каждом событии. Неудачный ход тоже обращался к модели и расходовал
  // токены, поэтому сводка есть у завершённого хода любым исходом. Токены — сумма входных и
  // выходных по всем ответам хода. Подпись начинается со слова, а не с числа — по той же
  // причине, что и заголовок контейнера действий (см. groupLabel).
  const facts = [
    turn.at === undefined ? null : `начат ${dayjs(turn.at).format('DD.MM.YYYY HH:mm:ss')}`,
    turn.running || turn.durationMs === undefined ? null : `время: ${duration(turn.durationMs)}`,
    turn.running || turn.steps === undefined ? null : `шагов: ${turn.steps}`,
    turn.running || turn.usage === undefined
      ? null
      : `токенов: ${(turn.usage.prompt + turn.usage.completion).toLocaleString('ru')}`,
    turn.model === undefined ? null : `модель: ${turn.model}`,
  ].filter((fact): fact is string => fact !== null)
  const summary =
    facts.length === 0 && turn.snapshotId === undefined ? null : (
      <Flex wrap="wrap" align="baseline" gap={token.marginXS}>
        <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
          {capitalize(facts.join(' · '))}
        </Typography.Text>
        {turn.snapshotId === undefined ? null : (
          <Typography.Link
            style={{ fontSize: token.fontSizeSM }}
            onClick={() => setSnapshotOpen(true)}
          >
            Запрос к модели
          </Typography.Link>
        )}
      </Flex>
    )

  return (
    <Flex vertical gap={token.margin}>
      {/* Вход ограничен по ширине: короткая реплика во всю строку читается хуже, а различие
          в ширине само по себе отделяет вход от ответа. Блок прижат к правому краю колонки
          целиком: внутри себя он размещает содержимое у правого края, но сам без выравнивания
          встал бы у левого. */}
      {turn.question === undefined ? null : (
        <Bubble
          placement="end"
          header={task ? 'Постановка задачи' : undefined}
          content={<QuestionText text={turn.question} />}
          avatar={<Avatar icon={task ? <ProfileOutlined /> : <UserOutlined />} />}
          variant="filled"
          style={{ maxWidth: task ? '80%' : '66%', alignSelf: 'flex-end' }}
        />
      )}

      {/* Ответ агента занимает всю ширину колонки независимо от объёма: он содержит
          таблицы и сворачиваемые блоки, ширина которых не должна зависеть от длины текста,
          а переменная ширина ответов делает переписку неровной. */}
      <Bubble
        placement="start"
        avatar={<Avatar icon={<RobotOutlined />} style={{ background: token.colorPrimary }} />}
        variant="outlined"
        loading={!hasContent && turn.running}
        footer={summary}
        style={{ width: '100%' }}
        styles={{ content: { flex: 1, minWidth: 0, alignSelf: 'stretch' } }}
        content={
          <Flex vertical gap={token.marginSM} style={{ minWidth: 0 }}>
            {turn.items.map((item) =>
              item.kind === 'text' ? (
                <MarkdownText key={item.key} text={item.text} />
              ) : item.kind === 'child' ? (
                <ChildSessionCard
                  key={item.key}
                  childId={item.childId}
                  childKind={item.childKind}
                  title={item.title}
                />
              ) : item.kind === 'group' ? (
                <WorkGroupItem key={item.key} group={item} />
              ) : (
                <WorkRows key={item.key} items={[item]} />
              ),
            )}

            {/* Ожидание ответа модели показывается указателем, а не отдельной записью. */}
            {turn.awaitingModel && hasContent ? (
              <Spin size="small" style={{ alignSelf: 'flex-start' }} />
            ) : null}

            {turn.failure === undefined ? null : (
              <Alert
                type={turn.failure.reason === 'aborted' ? 'warning' : 'error'}
                showIcon
                message={`Ход не завершён — ${failureLabel[turn.failure.reason]}`}
                description={turn.failure.message}
              />
            )}

            {turn.sessionFailure === undefined ? null : (
              <Alert
                type="error"
                showIcon
                message="Сессия завершилась отказом"
                description={turn.sessionFailure}
              />
            )}

            {!hasContent && !turn.running ? (
              <Typography.Text type="secondary">Ответа нет</Typography.Text>
            ) : null}
          </Flex>
        }
      />

      {turn.snapshotId === undefined ? null : (
        <SnapshotDrawer
          snapshotId={turn.snapshotId}
          open={snapshotOpen}
          onClose={() => setSnapshotOpen(false)}
        />
      )}
    </Flex>
  )
}
