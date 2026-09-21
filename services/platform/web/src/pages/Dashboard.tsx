import { Alert, Badge, Card, Col, DatePicker, Row, Space, Statistic, Typography } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  fetchSensorView,
  fetchSulfurSettings,
  type SensorEvent,
  type SulfurSettings,
} from '../api'
import { SulfurChart, type SulfurPoint } from '../components/SulfurChart'
import { useSensorStream } from '../hooks/useSensorStream'

const { RangePicker } = DatePicker

// Период, показываемый при открытии страницы: двое суток, заканчивающиеся текущим
// моментом. Дальнейший выбор делается календарём.
const DEFAULT_RANGE_DAYS = 2

// Насколько конец выбранного периода может отстоять от текущего момента, чтобы период
// считался открытым в настоящее. Для такого периода показания из потока наносятся на
// график сразу; для периода, целиком лежащего в прошлом, они отбрасываются, иначе ряд
// перестал бы соответствовать тому, что указано в календаре.
const LIVE_TOLERANCE_MINUTES = 5

function defaultRange(): [Dayjs, Dayjs] {
  const end = dayjs()
  return [end.subtract(DEFAULT_RANGE_DAYS, 'day'), end]
}

function isLiveRange(end: Dayjs): boolean {
  return end.isAfter(dayjs().subtract(LIVE_TOLERANCE_MINUTES, 'minute'))
}

// Ряд отбирается по имени, под которым он запрошен (поле requested_name ответа), а не
// по коду датчика: страница знает ряды только по именам из справочника, и какой код за
// именем стоит, ей несущественно.
function toPoints(
  items: { timestamp: string; value: number; requested_name: string }[],
  name: string,
): SulfurPoint[] {
  return items
    .filter((item) => item.requested_name === name)
    .map((item) => [new Date(item.timestamp).getTime(), item.value] as SulfurPoint)
}

// Добавляет точку в ряд с сохранением порядка по времени. Событие из потока обычно
// оказывается новее всех имеющихся, поэтому обычный случай — дописывание в конец;
// перебор с конца нужен для показания, доставленного с задержкой.
function insertPoint(points: SulfurPoint[], point: SulfurPoint): SulfurPoint[] {
  if (points.length === 0 || point[0] >= points[points.length - 1][0]) {
    return [...points, point]
  }
  const index = points.findIndex((existing) => existing[0] > point[0])
  return [...points.slice(0, index), point, ...points.slice(index)]
}

export function Dashboard() {
  const [settings, setSettings] = useState<SulfurSettings | null>(null)
  const [range, setRange] = useState<[Dayjs, Dayjs]>(defaultRange)
  const [pak, setPak] = useState<SulfurPoint[]>([])
  const [lims, setLims] = useState<SulfurPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Границы периода нужны обработчику событий, но не должны пересоздавать подписку,
  // поэтому хранятся ещё и в ref.
  const rangeRef = useRef(range)
  rangeRef.current = range

  useEffect(() => {
    fetchSulfurSettings().then(setSettings).catch((reason: Error) => setError(reason.message))
  }, [])

  const load = useCallback(async () => {
    if (!settings) {
      return
    }
    setLoading(true)
    try {
      const items = await fetchSensorView(
        [settings.pak_name, settings.lims_name],
        range[0].toDate(),
        range[1].toDate(),
      )
      setPak(toPoints(items, settings.pak_name))
      setLims(toPoints(items, settings.lims_name))
      setError(null)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setLoading(false)
    }
  }, [settings, range])

  useEffect(() => {
    void load()
  }, [load])

  const handleEvent = useCallback(
    (event: SensorEvent) => {
      if (!settings) {
        return
      }
      const moment = new Date(event.timestamp).getTime()
      const [start, end] = rangeRef.current
      if (moment < start.valueOf()) {
        return
      }
      // Верхняя граница проверяется только у периода, целиком лежащего в прошлом.
      // У периода, доведённого до настоящего момента, показание, пришедшее позже
      // открытия страницы, неизбежно оказывается за его концом — и именно его
      // страница должна показать.
      if (moment > end.valueOf() && !isLiveRange(end)) {
        return
      }

      // Событие потока несёт имя ряда, определённое сервером по справочнику, поэтому
      // здесь сравниваются имена — так же, как при выборке за период.
      const point: SulfurPoint = [moment, event.value]
      if (event.name === settings.pak_name) {
        setPak((points) => insertPoint(points, point))
      } else if (event.name === settings.lims_name) {
        setLims((points) => insertPoint(points, point))
      }
    },
    [settings],
  )

  const streamStatus = useSensorStream(handleEvent)

  const lastPak = pak.length > 0 ? pak[pak.length - 1][1] : null
  const lastLims = lims.length > 0 ? lims[lims.length - 1][1] : null
  const limit = settings?.limit ?? 10
  // Пока параметры не получены, ряды пусты, и имена нужны только для подписей.
  const pakName = settings?.pak_name ?? 'ПАК'
  const limsName = settings?.lims_name ?? 'ЛИМС'

  const exceedances = useMemo(
    () => pak.filter(([, value]) => value > limit).length,
    [pak, limit],
  )

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Row justify="space-between" align="middle" gutter={[16, 16]}>
        <Col>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Содержание серы в гидроочищенном ДТ
          </Typography.Title>
          <Typography.Text type="secondary">
            Поточный анализатор (ПАК) и лабораторный анализ (ЛИМС), мг/кг
          </Typography.Text>
        </Col>
        <Col>
          <Space size="middle">
            <Badge
              status={streamStatus === 'open' ? 'processing' : 'default'}
              text={
                streamStatus === 'open'
                  ? 'Поток событий подключён'
                  : streamStatus === 'connecting'
                    ? 'Подключение к потоку'
                    : 'Поток событий недоступен'
              }
            />
            <RangePicker
              showTime={{ format: 'HH:mm' }}
              format="DD.MM.YYYY HH:mm"
              allowClear={false}
              value={range}
              onChange={(value) => {
                if (value && value[0] && value[1]) {
                  setRange([value[0], value[1]])
                }
              }}
              presets={[
                { label: 'Сутки', value: [dayjs().subtract(1, 'day'), dayjs()] },
                { label: 'Двое суток', value: [dayjs().subtract(2, 'day'), dayjs()] },
                { label: 'Неделя', value: [dayjs().subtract(7, 'day'), dayjs()] },
                { label: 'Месяц', value: [dayjs().subtract(30, 'day'), dayjs()] },
              ]}
            />
          </Space>
        </Col>
      </Row>

      {error && <Alert type="error" showIcon message={error} />}

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title={`Последнее показание: ${pakName}`}
              value={lastPak ?? '—'}
              precision={lastPak === null ? undefined : 2}
              suffix={lastPak === null ? '' : 'мг/кг'}
              valueStyle={{ color: lastPak !== null && lastPak > limit ? '#cf1322' : undefined }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title={`Последний анализ: ${limsName}`}
              value={lastLims ?? '—'}
              precision={lastLims === null ? undefined : 2}
              suffix={lastLims === null ? '' : 'мг/кг'}
              valueStyle={{ color: lastLims !== null && lastLims > limit ? '#cf1322' : undefined }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title={`Превышений нормы ${limit} мг/кг (${pakName})`}
              value={exceedances}
              suffix={`из ${pak.length}`}
            />
          </Card>
        </Col>
      </Row>

      <Card>
        <SulfurChart
          pak={pak}
          lims={lims}
          pakName={pakName}
          limsName={limsName}
          limit={limit}
          loading={loading}
        />
      </Card>
    </Space>
  )
}
