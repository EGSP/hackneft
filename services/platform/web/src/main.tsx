import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider, theme } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import dayjs from 'dayjs'
import 'dayjs/locale/ru'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { App } from './App'
import './index.css'

dayjs.locale('ru')

// Кеш запросов раздела сессий (sessions/queries.ts). Журнал сессии приходит событиями через
// поток, а перечень и записи сессий опрашиваются с заданным периодом, поэтому перечитывание
// при возврате на вкладку браузера не нужно. Правило повтора задают сами запросы.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
    },
  },
})

const container = document.getElementById('root')
if (!container) {
  throw new Error('Элемент #root отсутствует в разметке страницы')
}

createRoot(container).render(
  <StrictMode>
    {/* Тема светлая: defaultAlgorithm задан явно, чтобы оформление не зависело
        от настройки тёмного режима в операционной системе. */}
    <ConfigProvider
      locale={ruRU}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: { colorPrimary: '#1677ff', borderRadius: 6 },
      }}
    >
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  </StrictMode>,
)
