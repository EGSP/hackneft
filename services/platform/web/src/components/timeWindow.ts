import dayjs, { type ManipulateType } from 'dayjs'

// Окно графика отсчитывается от последней записи, а не от текущего момента: источник
// данных может воспроизводить прошлые годы, и окно, привязанное к настоящему, было бы
// пустым.

export type WindowMode = 'twoDays' | 'week' | 'month' | 'quarter'

export const DEFAULT_MODE: WindowMode = 'twoDays'

// Длина окна задаётся календарными единицами, а не миллисекундами: месяц и квартал
// имеют разную длину.
//
// Полосы фона чередуются по единице деления окна. Единица выбрана так, чтобы полос
// было от двух до пятнадцати: при меньшем числе чередование не заметно, при большем
// полосы сливаются.
export const MODES: Record<
  WindowMode,
  { label: string; amount: number; unit: ManipulateType; band: BandUnit }
> = {
  twoDays: { label: '2 дня', amount: 2, unit: 'day', band: 'day' },
  week: { label: 'Неделя', amount: 7, unit: 'day', band: 'day' },
  month: { label: 'Месяц', amount: 1, unit: 'month', band: 'week' },
  quarter: { label: 'Квартал', amount: 3, unit: 'month', band: 'month' },
}

export type BandUnit = 'day' | 'week' | 'month'

export type Range = [number, number]

/** Окно режима, заканчивающееся моментом `end`. */
export function modeRange(mode: WindowMode, end: number): Range {
  const { amount, unit } = MODES[mode]
  return [dayjs(end).subtract(amount, unit).valueOf(), end]
}

/**
 * Полосы фона: каждая вторая единица деления внутри окна. Чётность считается от
 * абсолютного номера единицы, а не от начала окна, поэтому при сдвиге окна полоса
 * остаётся за тем же днём, неделей или месяцем, а не перекрашивается.
 */
export function bands(unit: BandUnit, [start, end]: Range): Range[] {
  const result: Range[] = []
  let cursor = startOf(unit, start)
  while (cursor.valueOf() < end) {
    const next = cursor.add(1, unit)
    if (unitIndex(unit, cursor) % 2 === 0) {
      result.push([Math.max(cursor.valueOf(), start), Math.min(next.valueOf(), end)])
    }
    cursor = next
  }
  return result
}

// Неделя начинается с понедельника, как принято в отечественном календаре; day()
// возвращает 0 для воскресенья.
function startOf(unit: BandUnit, moment: number) {
  const value = dayjs(moment)
  if (unit === 'week') {
    const shift = (value.day() + 6) % 7
    return value.startOf('day').subtract(shift, 'day')
  }
  return value.startOf(unit)
}

const EPOCH_MONDAY = dayjs('1970-01-05')

function unitIndex(unit: BandUnit, moment: dayjs.Dayjs): number {
  if (unit === 'month') {
    return moment.year() * 12 + moment.month()
  }
  const days = moment.startOf('day').diff(EPOCH_MONDAY.startOf('day'), 'day')
  return unit === 'week' ? Math.floor(days / 7) : days
}
