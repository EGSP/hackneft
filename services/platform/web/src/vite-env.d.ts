/// <reference types="vite/client" />

// Локали ECharts поставляются без объявлений типов. Модуль экспортирует объект перевода
// того же вида, что принимает echarts.registerLocale.
declare module 'echarts/i18n/langRU-obj.js' {
  const lang: Parameters<typeof import('echarts').registerLocale>[1]
  export default lang
}
