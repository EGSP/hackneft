import {
  CheckCircleFilled,
  ClockCircleFilled,
  CloseCircleFilled,
  DatabaseOutlined,
  ExperimentOutlined,
  FieldTimeOutlined,
  FlagOutlined,
  HourglassOutlined,
  MinusCircleFilled,
  RobotOutlined,
  SyncOutlined,
  WarningFilled,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Badge, Progress, Tooltip, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useState, type ReactNode } from 'react'

import { fetchAdvices, fetchAdvisorStatus, type AdvisorReason, type AdvisorStatus } from '../api'
import { AdviceCard } from './AdviceCard'
import { RUN_STATUS_LABEL, eventAppearance } from './appearance'

type CheckState = 'ok' | 'wait' | 'block' | 'idle' | 'work'

const STATE_COLOR: Record<CheckState, string> = {
  ok: '#52c41a',
  wait: '#fa8c16',
  block: '#cf1322',
  idle: '#bfbfbf',
  work: '#722ed1',
}

const SULFUR_LEVEL: Record<AdvisorStatus['sulfur_level'], { label: string; state: CheckState }> = {
  normal: { label: 'ниже порога', state: 'ok' },
  warning: { label: 'близко к порогу', state: 'wait' },
  exceeded: { label: 'выше порога', state: 'block' },
}

function formatMoment(value: string | null): string {
  return value ? dayjs(value).format('DD.MM.YYYY HH:mm') : '—'
}

function formatMinutes(ms: number): string {
  const minutes = Math.ceil(ms / 60_000)
  return minutes >= 60 ? `${Math.floor(minutes / 60)} ч ${minutes % 60} мин` : `${minutes} мин`
}

/** Состояние одним значком; заголовок и подробности — в подсказке при наведении. */
function StateIcon({
  state,
  title,
  details,
  children,
}: {
  state: CheckState
  title: string
  details?: ReactNode
  children: ReactNode
}) {
  return (
    <Tooltip
      title={
        <div className="advisor-tip">
          <strong>{title}</strong>
          {details && <div>{details}</div>}
        </div>
      }
    >
      <span className="advisor-state" style={{ color: STATE_COLOR[state] }} aria-label={title}>
        {children}
      </span>
    </Tooltip>
  )
}

function ReasonList({ reasons }: { reasons: AdvisorReason[] }) {
  return (
    <ul className="advisor-tip-list">
      {reasons.map((reason) => {
        const look = eventAppearance(reason)
        return (
          <li key={reason.kind}>
            <span style={{ color: look.color }}>{look.icon}</span> {reason.reason}.{' '}
            {reason.consequence}
          </li>
        )
      })}
    </ul>
  )
}

// Текущий момент для обратного отсчёта в режиме реального времени.
function useNow(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) {
      return
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [enabled])
  return now
}

/**
 * Показатели модуля запуска советника — те же условия, по которым AdvisorService.reserve
 * решает, создавать ли запуск: полнота и достоверность данных, наличие новых причин,
 * общий интервал между запусками и отсутствие выполняющегося запуска. Каждое состояние
 * показано значком, подробности — в подсказке.
 *
 * Интервал отсчитывается по времени данных: при воспроизведении архива (clock=replay)
 * это время последнего окна, а не часы компьютера, поэтому и таймер показывает остаток
 * по времени данных.
 */
function LaunchGate({ status }: { status: AdvisorStatus }) {
  const live = status.clock === 'live'
  const now = useNow(live)
  const reference = live ? now : status.observed_at ? dayjs(status.observed_at).valueOf() : null
  const nextAt = status.next_allowed_at ? dayjs(status.next_allowed_at).valueOf() : null
  const remaining = nextAt !== null && reference !== null ? nextAt - reference : 0
  const intervalMs = status.interval_minutes * 60_000
  const elapsedShare = nextAt === null ? 100 : Math.min(100, Math.max(0, 100 - (remaining / intervalMs) * 100))

  const dataBlocked = status.blocked_by_data.length > 0
  const dataWaiting = status.window_open || status.inputs_pending
  const pending = status.pending_reasons
  const running = status.active_run_id !== null

  let overall: { text: string; state: CheckState; icon: ReactNode }
  if (running) {
    overall = { text: 'Советник работает', state: 'work', icon: <SyncOutlined spin /> }
  } else if (dataBlocked) {
    overall = { text: 'Запуск приостановлен', state: 'block', icon: <CloseCircleFilled /> }
  } else if (pending.length === 0) {
    overall = { text: 'Ожидает событий', state: 'idle', icon: <MinusCircleFilled /> }
  } else if (remaining > 0) {
    overall = {
      text: `Запуск через ${formatMinutes(remaining)}`,
      state: 'wait',
      icon: <ClockCircleFilled />,
    }
  } else if (dataWaiting) {
    overall = { text: 'Ожидает данных окна', state: 'wait', icon: <HourglassOutlined /> }
  } else {
    overall = { text: 'Готов к запуску', state: 'ok', icon: <CheckCircleFilled /> }
  }

  const level = SULFUR_LEVEL[status.sulfur_level] ?? SULFUR_LEVEL.normal
  const dataState: CheckState = dataBlocked ? 'block' : dataWaiting ? 'wait' : 'ok'
  const lastError = !running ? status.last_run?.error : null

  return (
    <div className="advisor-gate">
      <div className="advisor-gate-title">
        <Typography.Title level={5} style={{ margin: 0 }}>
          <RobotOutlined /> Советник
        </Typography.Title>
        <div className="advisor-states">
          <StateIcon state={overall.state} title={overall.text}>
            {overall.icon}
          </StateIcon>

          <StateIcon
            state="idle"
            title={`Время данных: ${formatMoment(status.observed_at)}`}
            details={
              live
                ? 'Интервал и таймер считаются по часам компьютера (реальное время)'
                : 'Интервал и таймер считаются по времени данных (воспроизведение архива)'
            }
          >
            <FieldTimeOutlined style={{ color: live ? STATE_COLOR.work : undefined }} />
          </StateIcon>

          <StateIcon
            state={level.state}
            title={`Сера по ПАК ${level.label} 10 мг/кг`}
            details={
              status.active_reasons.length > 0 ? (
                <>
                  Активные риски:
                  <ReasonList reasons={status.active_reasons} />
                </>
              ) : (
                'Активных рисков нет'
              )
            }
          >
            <ExperimentOutlined />
          </StateIcon>

          <StateIcon
            state={dataState}
            title="Данные"
            details={
              dataBlocked
                ? `Нет свежих достоверных показаний: ${status.blocked_by_data.join(', ')}`
                : status.window_open
                  ? 'Окно данных ещё не закрыто'
                  : status.inputs_pending
                    ? 'Обрабатываются полученные пакеты'
                    : 'Полные и достоверные'
            }
          >
            <DatabaseOutlined />
          </StateIcon>

          <StateIcon
            state={pending.length > 0 ? 'ok' : 'idle'}
            title="Причины запуска"
            details={pending.length === 0 ? 'Новых причин нет' : <ReasonList reasons={pending} />}
          >
            <Badge count={pending.length} size="small" offset={[4, -2]}>
              <FlagOutlined style={{ color: STATE_COLOR[pending.length > 0 ? 'ok' : 'idle'] }} />
            </Badge>
          </StateIcon>

          <StateIcon
            state={remaining > 0 ? 'wait' : 'ok'}
            title={`Интервал между запусками ${status.interval_minutes} мин`}
            details={
              nextAt === null
                ? 'Запусков ещё не было'
                : remaining > 0
                  ? `Следующий не раньше ${dayjs(nextAt).format('HH:mm')}, осталось ${formatMinutes(remaining)}`
                  : `Выдержан, последний запуск ${formatMoment(status.last_started_at)}`
            }
          >
            <Progress
              type="circle"
              size={16}
              percent={elapsedShare}
              showInfo={false}
              strokeColor={STATE_COLOR[remaining > 0 ? 'wait' : 'ok']}
            />
          </StateIcon>

          <StateIcon
            state={running ? 'work' : 'ok'}
            title="Выполнение"
            details={
              running
                ? 'Идёт запуск, новые удержаны; агрегатор переведён в реальное время'
                : status.last_run
                  ? `Последний: ${RUN_STATUS_LABEL[status.last_run.status] ?? status.last_run.status}, ${formatMoment(status.last_run.requested_at)}`
                  : 'Свободно'
            }
          >
            <RobotOutlined />
          </StateIcon>

          {lastError && (
            <StateIcon state="wait" title="Последний запуск завершился с ошибкой" details={lastError}>
              <WarningFilled />
            </StateIcon>
          )}
        </div>
      </div>
    </div>
  )
}

/** Правая колонка главной страницы: допуск советника и его выводы. */
export function AdvisorPanel() {
  const { data, error } = useQuery({
    queryKey: ['advisor', 'status'],
    queryFn: fetchAdvisorStatus,
    refetchInterval: 3000,
  })
  // Новый совет делает прежние неактуальными: первый в перечне показан развёрнутым.
  const { data: advices } = useQuery({
    queryKey: ['advisor', 'advices'],
    queryFn: () => fetchAdvices(),
    refetchInterval: 3000,
  })

  return (
    <div className="advisor-panel">
      <div className="advisor-panel-header">
        {data ? (
          <LaunchGate status={data} />
        ) : error ? (
          <Alert type="error" showIcon message={`Состояние советника недоступно: ${error.message}`} />
        ) : (
          <Typography.Text type="secondary">Загрузка состояния советника…</Typography.Text>
        )}
      </div>
      <div className="advisor-panel-body">
        {advices && advices.length > 0 ? (
          <div className="advice-list">
            {advices.map((advice, index) => (
              <AdviceCard key={advice.id} advice={advice} current={index === 0} />
            ))}
          </div>
        ) : (
          <div className="advisor-placeholder">
            <RobotOutlined style={{ fontSize: 32 }} />
            <Typography.Text type="secondary">Советов пока нет</Typography.Text>
          </div>
        )}
      </div>
    </div>
  )
}
