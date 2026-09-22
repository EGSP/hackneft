import { AimOutlined, ExpandOutlined } from '@ant-design/icons'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Row,
  Segmented,
  Space,
  Statistic,
  Tooltip,
  Typography,
} from 'antd'
import dayjs from 'dayjs'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  fetchLatestTimestamp,
  fetchSensorView,
  fetchSulfurSettings,
  type SensorEvent,
  type SulfurSettings,
} from '../api'
import { SulfurChart, type SulfurPoint } from '../components/SulfurChart'
import {
  DEFAULT_MODE,
  MODES,
  bands,
  modeRange,
  type Range,
  type WindowMode,
} from '../components/timeWindow'
import { useSensorStream } from '../hooks/useSensorStream'

// Доля ширины видимой части, в пределах которой её правый край считается совпадающим с
// концом окна. Перетаскивание не попадает в край точно, а следование не должно
// отключаться от сдвига на несколько пикселей.
const FOLLOW_TOLERANCE = 0.01

// Состояние окна графика.
//
// extent — окно режима: границы оси и полосы прокрутки. view — видимая его часть,
// которую пользователь меняет прокруткой. anchor — время последней известной записи.
// При следовании окно и видимая часть сдвигаются вслед за anchor; без следования окно
// неподвижно, а anchor продолжает обновляться, чтобы кнопка «Следовать» знала, куда
// переходить.
interface WindowState {
  anchor: number
  extent: Range
  view: Range
  following: boolean
}

function initialWindow(mode: WindowMode, anchor: number): WindowState {
  const extent = modeRange(mode, anchor)
  return { anchor, extent, view: extent, following: true }
}

// Видимая часть той же ширины, прижатая к концу окна.
function snapView(view: Range, extent: Range): Range {
  const width = view[1] - view[0]
  return [Math.max(extent[0], extent[1] - width), extent[1]]
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

// Точки, вышедшие за начало окна, отбрасываются: при следовании окно движется
// непрерывно, и без отсечения ряд рос бы без предела.
function trimBefore(points: SulfurPoint[], start: number): SulfurPoint[] {
  if (points.length === 0 || points[0][0] >= start) {
    return points
  }
  const index = points.findIndex(([moment]) => moment >= start)
  return index === -1 ? [] : points.slice(index)
}

function formatMoment(moment: number): string {
  return dayjs(moment).format('DD.MM.YYYY HH:mm')
}

export function Dashboard() {
  const [settings, setSettings] = useState<SulfurSettings | null>(null)
  const [mode, setMode] = useState<WindowMode>(DEFAULT_MODE)
  const [win, setWin] = useState<WindowState | null>(null)
  // Период, за который загружены ряды. Меняется только явными действиями — выбором
  // режима, кнопками и первой загрузкой; сдвиг окна при следовании ряды не
  // перезагружает, а дополняет событиями потока.
  const [loaded, setLoaded] = useState<Range | null>(null)
  const [pak, setPak] = useState<SulfurPoint[]>([])
  const [lims, setLims] = useState<SulfurPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const modeRef = useRef(mode)
  modeRef.current = mode
  // Текущее окно для обработчиков кнопок: им нужно прочитать состояние и по нему задать
  // и окно, и период загрузки, а функция обновления состояния побочных действий
  // содержать не должна.
  const winRef = useRef(win)
  winRef.current = win

  useEffect(() => {
    fetchSulfurSettings().then(setSettings).catch((reason: Error) => setError(reason.message))
  }, [])

  // Окно отсчитывается от последней записи. Пока записей нет, опорой служит текущий
  // момент: первая же запись из потока сдвинет окно к себе.
  useEffect(() => {
    fetchLatestTimestamp()
      .then((latest) => {
        const anchor = latest?.getTime() ?? Date.now()
        const state = initialWindow(modeRef.current, anchor)
        setWin((current) => current ?? state)
        setLoaded((current) => current ?? state.extent)
      })
      .catch((reason: Error) => setError(reason.message))
  }, [])

  useEffect(() => {
    if (!settings || !loaded) {
      return
    }
    let cancelled = false
    setLoading(true)
    fetchSensorView([settings.pak_name, settings.lims_name], new Date(loaded[0]), new Date(loaded[1]))
      .then((items) => {
        if (cancelled) {
          return
        }
        setPak(toPoints(items, settings.pak_name))
        setLims(toPoints(items, settings.lims_name))
        setError(null)
      })
      .catch((reason: Error) => {
        if (!cancelled) {
          setError(reason.message)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [settings, loaded])

  const selectMode = useCallback((next: WindowMode) => {
    setMode(next)
    const current = winRef.current
    if (!current) {
      return
    }
    const state = initialWindow(next, current.anchor)
    setWin(state)
    setLoaded(state.extent)
  }, [])

  // «Следовать»: окно переходит к последней записи, видимая часть сохраняет ширину и
  // прижимается к концу, и дальше окно движется вслед за новыми записями. Ряды
  // перезагружаются: без следования события за пределами окна не накапливались.
  const follow = useCallback(() => {
    const current = winRef.current
    if (!current) {
      return
    }
    const extent = modeRange(modeRef.current, current.anchor)
    setWin({ ...current, extent, view: snapView(current.view, extent), following: true })
    setLoaded(extent)
  }, [])

  // «Восстановить»: видимая часть снова охватывает окно режима целиком.
  const restore = useCallback(() => {
    setWin((current) => (current ? { ...current, view: current.extent } : current))
  }, [])

  const changeView = useCallback((view: Range) => {
    setWin((current) => {
      if (!current) {
        return current
      }
      const width = view[1] - view[0]
      const atEnd = view[1] >= current.extent[1] - width * FOLLOW_TOLERANCE
      return { ...current, view, following: current.following && atEnd }
    })
  }, [])

  const handleEvent = useCallback(
    (event: SensorEvent) => {
      if (!settings) {
        return
      }
      const isPak = event.name === settings.pak_name
      const isLims = event.name === settings.lims_name
      const moment = new Date(event.timestamp).getTime()

      // Опора окна — последняя запись по любому датчику, как и при открытии страницы.
      setWin((current) => {
        if (!current || moment <= current.anchor) {
          return current
        }
        if (!current.following) {
          return { ...current, anchor: moment }
        }
        const extent = modeRange(modeRef.current, moment)
        const shift = extent[1] - current.extent[1]
        const view: Range = [Math.max(extent[0], current.view[0] + shift), current.view[1] + shift]
        return { anchor: moment, extent, view, following: true }
      })

      if (!isPak && !isLims) {
        return
      }
      // Событие потока несёт имя ряда, определённое сервером по справочнику, поэтому
      // здесь сравниваются имена — так же, как при выборке за период.
      const point: SulfurPoint = [moment, event.value]
      const update = (points: SulfurPoint[]) => insertPoint(points, point)
      if (isPak) {
        setPak(update)
      } else {
        setLims(update)
      }
    },
    [settings],
  )

  // Точки, вышедшие за начало окна, отсекаются при каждом его сдвиге.
  const windowStart = win?.extent[0]
  useEffect(() => {
    if (windowStart === undefined) {
      return
    }
    setPak((points) => trimBefore(points, windowStart))
    setLims((points) => trimBefore(points, windowStart))
  }, [windowStart])

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

  const extent = win?.extent ?? null
  const chartBands = useMemo(
    () => (extent ? bands(MODES[mode].band, extent) : []),
    [extent, mode],
  )
  const isRestored = win ? win.view[0] === win.extent[0] && win.view[1] === win.extent[1] : true

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

      <Card
        title={
          <Space size="middle" wrap>
            <Segmented<WindowMode>
              value={mode}
              onChange={selectMode}
              options={(Object.keys(MODES) as WindowMode[]).map((key) => ({
                value: key,
                label: MODES[key].label,
              }))}
            />
            {win && (
              <Typography.Text type="secondary" style={{ fontWeight: 'normal' }}>
                {formatMoment(win.extent[0])} — {formatMoment(win.extent[1])}
              </Typography.Text>
            )}
          </Space>
        }
        extra={
          <Space>
            <Tooltip title="Перевести окно к последней записи и двигать его вслед за новыми">
              <Button
                icon={<AimOutlined />}
                type={win?.following ? 'primary' : 'default'}
                disabled={!win}
                onClick={follow}
              >
                Следовать
              </Button>
            </Tooltip>
            <Tooltip title="Показать окно выбранного режима целиком">
              <Button icon={<ExpandOutlined />} disabled={!win || isRestored} onClick={restore}>
                Восстановить
              </Button>
            </Tooltip>
          </Space>
        }
      >
        {win && (
          <SulfurChart
            pak={pak}
            lims={lims}
            pakName={pakName}
            limsName={limsName}
            limit={limit}
            now={win.anchor}
            loading={loading}
            extent={win.extent}
            view={win.view}
            bands={chartBands}
            onViewChange={changeView}
          />
        )}
      </Card>
    </Space>
  )
}
