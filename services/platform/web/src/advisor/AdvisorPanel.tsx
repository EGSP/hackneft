import {
  CheckCircleFilled,
  ClockCircleFilled,
  CloseCircleFilled,
  MinusCircleFilled,
  RobotOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Badge, Progress, Tag, Tooltip, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useState, type ReactNode } from 'react'

import { fetchAdvices, fetchAdvisorStatus, type AdvisorStatus } from '../api'
import { AdviceCard } from './AdviceCard'
import { RUN_STATUS_LABEL, eventAppearance } from './appearance'

type CheckState = 'ok' | 'wait' | 'block' | 'idle'

const CHECK_ICON: Record<CheckState, ReactNode> = {
  ok: <CheckCircleFilled style={{ color: '#52c41a' }} />,
  wait: <ClockCircleFilled style={{ color: '#fa8c16' }} />,
  block: <CloseCircleFilled style={{ color: '#cf1322' }} />,
  idle: <MinusCircleFilled style={{ color: '#bfbfbf' }} />,
}

const SULFUR_LEVEL: Record<AdvisorStatus['sulfur_level'], { label: string; color: string }> = {
  normal: { label: 'Норма', color: 'success' },
  warning: { label: 'Близко к норме', color: 'warning' },
  exceeded: { label: 'Превышение', color: 'error' },
}

function formatMoment(value: string | null): string {
  return value ? dayjs(value).format('DD.MM.YYYY HH:mm') : '—'
}

function formatMinutes(ms: number): string {
  const minutes = Math.ceil(ms / 60_000)
  return minutes >= 60 ? `${Math.floor(minutes / 60)} ч ${minutes % 60} мин` : `${minutes} мин`
}

function Check({ state, title, children }: { state: CheckState; title: string; children: ReactNode }) {
  return (
    <div className="advisor-check">
      {CHECK_ICON[state]}
      <div className="advisor-check-body">
        <Typography.Text strong>{title}</Typography.Text>
        <div className="advisor-check-text">{children}</div>
      </div>
    </div>
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
 * общий интервал между запусками и отсутствие выполняющегося запуска.
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

  let overall: { text: string; badge: 'processing' | 'error' | 'warning' | 'success' | 'default' }
  if (running) {
    overall = { text: 'Советник работает', badge: 'processing' }
  } else if (dataBlocked) {
    overall = { text: 'Запуск приостановлен', badge: 'error' }
  } else if (pending.length === 0) {
    overall = { text: 'Ожидает событий', badge: 'default' }
  } else if (remaining > 0) {
    overall = { text: `Запуск через ${formatMinutes(remaining)}`, badge: 'warning' }
  } else if (dataWaiting) {
    overall = { text: 'Ожидает данных окна', badge: 'warning' }
  } else {
    overall = { text: 'Готов к запуску', badge: 'success' }
  }

  const level = SULFUR_LEVEL[status.sulfur_level] ?? SULFUR_LEVEL.normal

  return (
    <div className="advisor-gate">
      <div className="advisor-gate-title">
        <Typography.Title level={5} style={{ margin: 0 }}>
          <RobotOutlined /> Советник
        </Typography.Title>
        <Badge status={overall.badge} text={overall.text} />
      </div>

      <div className="advisor-gate-meta">
        <span>
          Время данных: <strong>{formatMoment(status.observed_at)}</strong>
        </span>
        <Tooltip title="Интервал и таймер считаются по этим часам">
          <Tag bordered={false}>{live ? 'Реальное время' : 'Воспроизведение'}</Tag>
        </Tooltip>
      </div>
      <div className="advisor-gate-meta">
        <span>Сера по ПАК:</span>
        <Tag color={level.color} bordered={false}>
          {level.label}
        </Tag>
        <span>Активных рисков: {status.active_reasons.length}</span>
      </div>

      <Check
        state={dataBlocked ? 'block' : dataWaiting ? 'wait' : 'ok'}
        title="Данные"
      >
        {dataBlocked
          ? `Нет свежих достоверных показаний: ${status.blocked_by_data.join(', ')}`
          : status.window_open
            ? 'Окно данных ещё не закрыто'
            : status.inputs_pending
              ? 'Обрабатываются полученные пакеты'
              : 'Полные и достоверные'}
      </Check>

      <Check state={pending.length > 0 ? 'ok' : 'idle'} title="Поводы для запуска">
        {pending.length === 0 ? (
          'Новых причин нет'
        ) : (
          <div className="advisor-reasons">
            {pending.map((reason) => {
              const look = eventAppearance(reason)
              return (
                <Tooltip key={reason.kind} title={reason.consequence}>
                  <Tag icon={look.icon} color={look.color} bordered={false} style={{ marginInlineEnd: 0 }}>
                    {reason.reason}
                  </Tag>
                </Tooltip>
              )
            })}
          </div>
        )}
      </Check>

      <Check state={remaining > 0 ? 'wait' : 'ok'} title={`Интервал между запусками ${status.interval_minutes} мин`}>
        {nextAt === null ? (
          'Запусков ещё не было'
        ) : (
          <>
            {remaining > 0
              ? `Следующий не раньше ${dayjs(nextAt).format('HH:mm')}, осталось ${formatMinutes(remaining)}`
              : `Выдержан, последний запуск ${formatMoment(status.last_started_at)}`}
            <Progress
              percent={elapsedShare}
              showInfo={false}
              size="small"
              status={remaining > 0 ? 'active' : 'success'}
              style={{ margin: 0 }}
            />
          </>
        )}
      </Check>

      <Check state={running ? 'wait' : 'ok'} title="Выполнение">
        {running ? (
          <>
            <SyncOutlined spin /> Идёт запуск, новые удержаны
          </>
        ) : status.last_run ? (
          `Последний: ${RUN_STATUS_LABEL[status.last_run.status] ?? status.last_run.status}, ${formatMoment(status.last_run.requested_at)}`
        ) : (
          'Свободно'
        )}
      </Check>

      {status.last_run?.error && !running && (
        <Alert type="warning" showIcon message={status.last_run.error} style={{ fontSize: 12 }} />
      )}
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
