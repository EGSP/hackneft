import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  ExpandAltOutlined,
  LinkOutlined,
  MinusOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { Button, Modal, Popover, Tag, Tooltip, Typography } from 'antd'
import dayjs from 'dayjs'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import type { Advice, AdviceAction, AdviceCardData, AdviceType, AdviceValue } from '../api'
import { eventAppearance } from './appearance'

// Оформление карточки совета: тип совета задаёт цвет полосы, метку и главный значок.
// Типов три, отдельной градации риска нет: она противоречила типу («предупреждение» при
// «ничего не делать»).

export const ADVICE_TYPE: Record<AdviceType, { label: string; color: string; icon: ReactNode }> = {
  hold: { label: 'Режим не менять', color: '#389e0d', icon: <CheckCircleOutlined /> },
  check: { label: 'Проверить', color: '#fa8c16', icon: <SearchOutlined /> },
  adjust: { label: 'Изменить режим', color: '#cf1322', icon: <ThunderboltOutlined /> },
}

function typeOf(card: AdviceCardData) {
  return ADVICE_TYPE[card.type] ?? ADVICE_TYPE.check
}

function formatValue(value: AdviceValue): string {
  return `${value.value.toLocaleString('ru-RU')} ${value.unit}`.trim()
}

// Названия параметров действий — тот же перечень, что в схеме карточки (advisor/advice.py).
const PARAMETER_LABEL: Record<string, string> = {
  ht_t6: 'температура реактора',
  ht_p13: 'давление реактора',
  ht_f9: 'подача сырья',
  ht_f25: 'расход ВСГ',
  ht_f2: 'расход газа',
  ht_q20: 'сера в сырье',
  'pack_24-2000:mg.sulfur': 'сера по ПАК',
  'lims_ht.2.mg.sulfur': 'сера по ЛИМС',
}

const CONFIDENCE: Record<AdviceCardData['confidence'], string> = {
  high: 'уверенность высокая',
  medium: 'уверенность средняя',
  low: 'уверенность низкая',
}

function formatTime(value: string): string {
  return dayjs(value).format('DD.MM HH:mm')
}

function DecisionIcon({ card }: { card: AdviceCardData }) {
  const look = typeOf(card)
  return (
    <Tooltip title={look.label}>
      <span style={{ color: look.color }}>{look.icon}</span>
    </Tooltip>
  )
}

function ActionIcon({ action }: { action: AdviceAction }) {
  if (action.type === 'check') {
    return <SearchOutlined />
  }
  if (action.direction === 'increase') {
    return <ArrowUpOutlined />
  }
  if (action.direction === 'decrease') {
    return <ArrowDownOutlined />
  }
  return <MinusOutlined />
}

function ActionRow({ action }: { action: AdviceAction }) {
  return (
    <li className="advice-action">
      <span className="advice-action-icon">
        <ActionIcon action={action} />
      </span>
      <span>
        {action.text}
        {action.parameter && (
          <Tooltip title={PARAMETER_LABEL[action.parameter] ?? action.parameter}>
            <span className="advice-code">
              {action.parameter}
              {action.current && `: ${formatValue(action.current)}`}
              {action.target && ` → ${formatValue(action.target)}`}
            </span>
          </Tooltip>
        )}
      </span>
    </li>
  )
}

/** Содержимое карточки: одно и то же в развёрнутой карточке, предпросмотре и окне совета. */
function AdviceBody({ advice, full }: { advice: Advice; full: boolean }) {
  const { card } = advice
  return (
    <div className="advice-body">
      <div className="advice-headline">
        <DecisionIcon card={card} />
        <Typography.Text strong>{card.headline}</Typography.Text>
      </div>
      {card.actions.length > 0 ? (
        <ul className="advice-actions">
          {card.actions.map((action, index) => (
            <ActionRow key={index} action={action} />
          ))}
        </ul>
      ) : (
        <div className="advice-hold">Режим не менять, действий не требуется</div>
      )}
      <div className="advice-because">{card.because}</div>
      <dl className="advice-agents">
        <dt>Защита</dt>
        <dd>{card.protection}</dd>
        <dt>Производство</dt>
        <dd>{card.production}</dd>
        <dt>Эффект</dt>
        <dd>
          {card.expected_effect}
          {card.recheck_after_minutes !== null && `, проверить через ${card.recheck_after_minutes} мин`}
        </dd>
      </dl>
      {full && advice.reasons.length > 0 && (
        <div className="advice-reasons">
          {advice.reasons.map((reason) => {
            const look = eventAppearance(reason)
            return (
              <Tag key={reason.kind} icon={look.icon} color={look.color} bordered={false}>
                {reason.reason}
              </Tag>
            )
          })}
        </div>
      )}
    </div>
  )
}

function AdviceMeta({ advice }: { advice: Advice }) {
  const look = typeOf(advice.card)
  return (
    <div className="advice-meta">
      <Tag color={look.color} bordered={false} style={{ marginInlineEnd: 0 }}>
        {look.label}
      </Tag>
      <span>{formatTime(advice.created_at)}</span>
      <span>{CONFIDENCE[advice.card.confidence]}</span>
      {advice.session_id && (
        <Link to={`/sessions/${advice.session_id}`} onClick={(event) => event.stopPropagation()}>
          <LinkOutlined /> Сессия
        </Link>
      )}
    </div>
  )
}

/** Окно совета: карточка целиком с причинами запуска и ссылкой на сессию. */
function AdviceModal({ advice, onClose }: { advice: Advice; onClose: () => void }) {
  return (
    <Modal
      open
      onCancel={onClose}
      footer={null}
      width={520}
      title={`Совет от ${dayjs(advice.created_at).format('DD.MM.YYYY HH:mm')}`}
    >
      <AdviceMeta advice={advice} />
      <AdviceBody advice={advice} full />
    </Modal>
  )
}

interface Props {
  advice: Advice
  // Актуален только последний совет: он показан развёрнутым, прежние — одной строкой.
  current: boolean
}

/**
 * Карточка совета.
 *
 * Действий оператора карточка не ждёт: у неё нет кнопок подтверждения, а актуальность
 * определяется только тем, вышел ли совет новее. Наведение показывает предпросмотр,
 * нажатие открывает совет в окне.
 */
export function AdviceCard({ advice, current }: Props) {
  const [open, setOpen] = useState(false)
  const look = typeOf(advice.card)

  const card = current ? (
    <div
      className="advice-card advice-card--current"
      style={{ borderLeftColor: look.color }}
      onClick={() => setOpen(true)}
    >
      <div className="advice-card-top">
        <AdviceMeta advice={advice} />
        <Tooltip title="Открыть совет">
          <Button type="text" size="small" icon={<ExpandAltOutlined />} aria-label="Открыть совет" />
        </Tooltip>
      </div>
      <AdviceBody advice={advice} full={false} />
    </div>
  ) : (
    <div
      className="advice-card advice-card--stale"
      style={{ borderLeftColor: look.color }}
      onClick={() => setOpen(true)}
    >
      <DecisionIcon card={advice.card} />
      <span className="advice-card-line">{advice.card.headline}</span>
      <span className="advice-card-time">{formatTime(advice.created_at)}</span>
    </div>
  )

  return (
    <>
      <Popover
        placement="left"
        mouseEnterDelay={0.35}
        // Пока открыто окно совета, предпросмотр не нужен.
        open={open ? false : undefined}
        overlayClassName="advice-preview"
        title={
          <div className="advice-preview-title">
            <span>Предпросмотр совета</span>
            {!current && <Tag bordered={false}>Неактуален</Tag>}
          </div>
        }
        content={
          <>
            <AdviceMeta advice={advice} />
            <AdviceBody advice={advice} full />
          </>
        }
      >
        {card}
      </Popover>
      {open && <AdviceModal advice={advice} onClose={() => setOpen(false)} />}
    </>
  )
}
