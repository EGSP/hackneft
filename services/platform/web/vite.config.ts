import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Файл сборки для модулей каждого пакета. Библиотеки вынесены в отдельные файлы: они
// меняются редко, и после правки кода интерфейса браузер перезагружает только небольшой
// файл приложения, а не весь бандл целиком.
//
// Пакет относится к файлу функцией по пути модуля, а не перечнем точек входа. Перечень
// относит к файлу только модуль точки входа, а его внутренние модули достаются тому файлу,
// чей пакет импортирует их первым: так внутренности React и react-dom оказывались в файле
// antd, и файл react импортировал файл antd. Пока обратного импорта не было, это работало.
// Когда к библиотекам добавились @ant-design/x и react-query, файл antd стал импортировать
// файл react, и файлы оказались в цикле. При цикле порядок исполнения зависит от порядка
// импортов в файле приложения: когда первым исполнялся файл react, код react-dom
// выполнялся раньше самого React, и страница оставалась пустой. Функция относит каждый
// модуль перечисленного пакета напрямую, поэтому React, react-dom и scheduler целиком лежат
// в файле react, а он сам ничего не импортирует. Зависимости, не перечисленные здесь,
// достаются файлу того пакета, который их импортирует.
const CHUNK_BY_PACKAGE: Record<string, string> = {
  react: 'react',
  'react-dom': 'react',
  scheduler: 'react',
  'react-router': 'react',
  'react-router-dom': 'react',
  '@remix-run/router': 'react',
  '@tanstack/react-query': 'react',
  '@tanstack/query-core': 'react',
  antd: 'antd',
  '@ant-design/icons': 'antd',
  '@ant-design/x': 'antd',
  echarts: 'echarts',
  zrender: 'echarts',
  'react-markdown': 'markdown',
  'remark-gfm': 'markdown',
}

/** Имя пакета по пути модуля: последний сегмент после node_modules, с областью имён. */
function packageName(id: string): string | undefined {
  const parts = id.split(/[\\/]/)
  const index = parts.lastIndexOf('node_modules')
  if (index === -1 || index + 1 >= parts.length) {
    return undefined
  }
  const first = parts[index + 1]
  return first.startsWith('@') ? `${first}/${parts[index + 2]}` : first
}

// Сборка складывается прямо в каталог статики сервиса: FastAPI отдаёт её оттуда
// (api.py:serve_frontend), отдельного веб-сервера в рабочем контуре нет.
//
// В разработке запускаются два процесса: uvicorn на порту 8000 и этот сервер на 5173.
// Запросы к /api и поток SSE перенаправляются на uvicorn, поэтому источник для браузера
// остаётся один и настраивать CORS на стороне FastAPI не требуется.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/hackneft_platform/static',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          const name = packageName(id)
          return name === undefined ? undefined : CHUNK_BY_PACKAGE[name]
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // Поток событий не должен накапливаться в буфере посредника.
        ws: false,
      },
    },
  },
})
