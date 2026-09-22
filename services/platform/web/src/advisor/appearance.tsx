import {
  ApiOutlined,
  CheckCircleOutlined,
  CloudOutlined,
  ControlOutlined,
  DisconnectOutlined,
  ExperimentOutlined,
  FunnelPlotOutlined,
  InfoCircleOutlined,
  RobotOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import type { ReactNode } from 'react'

import type { AdvisorReason, AdvisorRun, AdvisorSeverity } from '../api'

// Оформление событий обработчиков (advisor/rules.py) и запусков советника: значок,
// цвет и подписи. Общее для таймлайна и колонки советника.

export const SEVERITY_COLOR: Record<AdvisorSeverity, string> = {
  info: '#1677ff',
  warning: '#fa8c16',
  critical: '#cf1322',
}
const RESOLVED_COLOR = '#389e0d'

export const SEVERITY_LABEL: Record<AdvisorSeverity, string> = {
  info: 'Сведения',
  warning: 'Предупреждение',
  critical: 'Критично',
}

export const KIND_LABEL: Record<string, string> = {
  sulfur_risk: 'Риск по сере',
  lab_result: 'Анализ ЛИМС',
  data_quality: 'Качество данных',
  feed_changed: 'Изменение сырья',
  regime_changed: 'Изменение режима',
  gas_supply: 'Подача газа',
}

export interface Appearance {
  icon: ReactNode
  color: string
}

/**
 * Значок и цвет события. Вид события задаёт значок, важность — цвет; события
 * возврата к норме («сера снизилась», «данные восстановлены») отмечены зелёным,
 * чтобы не читаться как новая неприятность.
 */
export function eventAppearance(event: AdvisorReason): Appearance {
  const color = SEVERITY_COLOR[event.severity] ?? SEVERITY_COLOR.info
  switch (event.kind) {
    case 'sulfur_risk':
      return event.evidence.level === 'normal'
        ? { icon: <CheckCircleOutlined />, color: RESOLVED_COLOR }
        : { icon: <WarningOutlined />, color }
    case 'lab_result':
      return { icon: <ExperimentOutlined />, color }
    case 'data_quality':
      return Array.isArray(event.evidence.codes) && event.evidence.codes.length > 0
        ? { icon: <DisconnectOutlined />, color }
        : { icon: <ApiOutlined />, color: RESOLVED_COLOR }
    case 'feed_changed':
      return { icon: <FunnelPlotOutlined />, color }
    case 'regime_changed':
      return { icon: <ControlOutlined />, color }
    case 'gas_supply':
      return { icon: <CloudOutlined />, color }
    default:
      return { icon: <InfoCircleOutlined />, color }
  }
}

export const RUN_STATUS_LABEL: Record<string, string> = {
  queued: 'В очереди',
  sending: 'Отправляется',
  running: 'Выполняется',
  completed: 'Завершён',
  failed: 'Ошибка',
  cancelled: 'Отменён',
  unknown: 'Исход неизвестен',
}

const RUN_STATUS_COLOR: Record<string, string> = {
  completed: RESOLVED_COLOR,
  failed: SEVERITY_COLOR.critical,
  unknown: SEVERITY_COLOR.warning,
  cancelled: '#8c8c8c',
}

export function runAppearance(run: AdvisorRun): Appearance {
  return { icon: <RobotOutlined />, color: RUN_STATUS_COLOR[run.status] ?? '#722ed1' }
}

function formatNumber(value: number): string {
  const digits = Math.abs(value) >= 100 ? 0 : Math.abs(value) >= 1 ? 2 : 4
  return value.toLocaleString('ru-RU', { maximumFractionDigits: digits })
}

/** Доказательства события одной строкой: значения, датчики, время отбора пробы. */
export function describeEvidence(event: AdvisorReason): string {
  const { evidence } = event
  const parts: string[] = []
  if (typeof evidence.value === 'number') {
    parts.push(`значение ${formatNumber(evidence.value)} мг/кг`)
  }
  if (typeof evidence.before === 'number' && typeof evidence.after === 'number') {
    const subject = evidence.sensor_code ?? evidence.ratio
    parts.push(
      `${subject ? `${subject}: ` : ''}${formatNumber(evidence.before)} → ${formatNumber(evidence.after)}`,
    )
  }
  if (Array.isArray(evidence.codes) && evidence.codes.length > 0) {
    parts.push(`датчики: ${evidence.codes.join(', ')}`)
  }
  if (typeof evidence.sampled_at === 'string') {
    parts.push(`отбор пробы ${dayjs(evidence.sampled_at).format('DD.MM HH:mm')}`)
  }
  return parts.join('; ')
}
