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
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  </StrictMode>,
)
