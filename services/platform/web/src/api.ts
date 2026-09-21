// Обращения к API платформы. Все пути относительные: в рабочем контуре интерфейс
// отдаёт тот же FastAPI, что и API, а в разработке запросы перенаправляет сервер Vite
// (см. vite.config.ts), поэтому адрес сервера нигде не записан.

export interface SensorViewItem {
  timestamp: string
  sensor_code: string
  sensor_name: string
  requested_name: string
  value: number
}

export interface SensorNameItem {
  sensor_code: string
  name: string
  is_code: boolean
  // Имя, по которому главная страница запрашивает ряд серы: сервер отклоняет его
  // удаление, поэтому кнопка удаления для такой строки не показывается.
  in_use: boolean
}

// Параметры главной страницы. Ряды задаются именами из справочника, а не кодами
// датчиков: по имени выполняется и выборка за период, и разбор потока событий, и
// подпись ряда на графике.
export interface SulfurSettings {
  pak_name: string
  lims_name: string
  limit: number
}

// Событие потока SSE: поля dataclass SensorDataCreated (events.py) и добавленное
// сервером имя ряда, к которому относится показание (handlers/sulfur_stream.py).
export interface SensorEvent {
  event_id: string
  data_id: number
  timestamp: string
  sensor_code: string
  value: number
  source: string
  name: string
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    // FastAPI возвращает описание ошибки в поле detail; если тело не разобралось,
    // показывается код ответа, иначе пользователь увидит сообщение без содержания.
    let detail = `Ошибка ${response.status}`
    try {
      const payload = (await response.json()) as { detail?: string }
      if (payload.detail) {
        detail = payload.detail
      }
    } catch {
      // тело не является JSON — остаётся сообщение с кодом ответа
    }
    throw new Error(detail)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

// Время последней записи по всем датчикам. Пусто, если записей нет: сервер отвечает
// кодом 404, и это не ошибка страницы, а отсутствие данных.
export async function fetchLatestTimestamp(): Promise<Date | null> {
  const response = await fetch('/api/sensor-data')
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(`Ошибка ${response.status}`)
  }
  const payload = (await response.json()) as { timestamp: string }
  return new Date(payload.timestamp)
}

export function fetchSulfurSettings(): Promise<SulfurSettings> {
  return request<SulfurSettings>('/api/sulfur/settings')
}

// Граница периода передаётся в местном времени и без указания смещения: показания
// приходят от источников с местным временем и хранятся так же. toISOString перевёл бы
// границу в UTC, и выборка сместилась бы на величину часового пояса.
function formatBoundary(value: Date): string {
  const pad = (part: number) => String(part).padStart(2, '0')
  return (
    `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}` +
    `T${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`
  )
}

// Выборка за период сразу по нескольким именам датчиков. Имя — код либо любой его
// синоним: сервер сопоставляет их через справочник sensor_names.
export async function fetchSensorView(
  names: string[],
  start: Date,
  end: Date,
): Promise<SensorViewItem[]> {
  const params = new URLSearchParams()
  params.set('start', formatBoundary(start))
  params.set('end', formatBoundary(end))
  names.forEach((name) => params.append('name', name))

  const payload = await request<{ items: SensorViewItem[] }>(
    `/api/sensor-data/view?${params.toString()}`,
  )
  return payload.items
}

export async function fetchSensorNames(): Promise<SensorNameItem[]> {
  const payload = await request<{ items: SensorNameItem[] }>('/api/sensor-names')
  return payload.items
}

export function createSensorName(sensorCode: string, name: string): Promise<SensorNameItem> {
  return request<SensorNameItem>('/api/sensor-names', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sensor_code: sensorCode, name }),
  })
}

export function deleteSensorName(sensorCode: string, name: string): Promise<void> {
  const params = new URLSearchParams({ sensor_code: sensorCode, name })
  return request<void>(`/api/sensor-names?${params.toString()}`, { method: 'DELETE' })
}
