import { DatabaseOutlined, LineChartOutlined, RobotOutlined } from '@ant-design/icons'
import { Layout, Menu, Typography } from 'antd'
import { useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { AdvisorPanel } from './advisor/AdvisorPanel'
import { Dashboard } from './pages/Dashboard'
import { Sessions } from './pages/Sessions'
import { Synonyms } from './pages/Synonyms'
import { EmptyState } from './sessions/EmptyState'
import { SessionView } from './sessions/SessionView'

const { Sider, Content, Header } = Layout

// Правая колонка советника — той же высоты, что и левая, но только на главной.
const ADVISOR_WIDTH = 340

const MENU_ITEMS = [
  { key: '/', icon: <LineChartOutlined />, label: 'Главная' },
  { key: '/synonyms', icon: <DatabaseOutlined />, label: 'Справочник синонимов' },
  { key: '/sessions', icon: <RobotOutlined />, label: 'Сессии' },
]

export function App() {
  const navigate = useNavigate()
  const location = useLocation()
  // Состояние панели отслеживается здесь, а не только внутри Sider: в свёрнутом виде
  // ширины не хватает на название, и текст заголовка заменяется сокращением.
  const [collapsed, setCollapsed] = useState(false)
  // Раздел сессий занимает ровно высоту окна и прокручивает перечень и журнал по
  // отдельности (см. pages/Sessions.tsx), поэтому для него раскладка ограничена высотой
  // окна, а у области содержимого нет полей. Остальные страницы растут вместе с содержимым.
  const inSessions = location.pathname === '/sessions' || location.pathname.startsWith('/sessions/')
  // Главная также занимает ровно высоту окна: графики делят её между собой, и
  // прокрутка страницы для их просмотра не нужна (см. pages/Dashboard.tsx).
  const onDashboard = location.pathname === '/'
  const fitsWindow = inSessions || onDashboard

  return (
    <Layout style={fitsWindow ? { height: '100dvh' } : { minHeight: '100vh' }}>
      <Sider
        theme="light"
        width={240}
        breakpoint="lg"
        collapsedWidth={64}
        collapsed={collapsed}
        onCollapse={setCollapsed}
      >
        <div
          style={{
            padding: collapsed ? '20px 0' : '20px 24px',
            textAlign: collapsed ? 'center' : 'left',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
          }}
        >
          {collapsed ? (
            <Typography.Text strong style={{ fontSize: 16 }}>
              24
            </Typography.Text>
          ) : (
            <>
              <Typography.Text strong style={{ fontSize: 16 }}>
                Платформа
              </Typography.Text>
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Установка 24-2000
              </Typography.Text>
            </>
          )}
        </div>
        <Menu
          mode="inline"
          // Адрес сессии вложен в раздел, и пункт меню раздела остаётся выделенным.
          selectedKeys={[inSessions ? '/sessions' : location.pathname]}
          items={MENU_ITEMS}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          {onDashboard ? (
            <>
              <Typography.Title level={4} style={{ margin: 0 }}>
                Содержание серы в гидроочищенном ДТ
              </Typography.Title>
              <Typography.Text type="secondary">
                Поточный анализатор (ПАК) и лабораторный анализ (ЛИМС), мг/кг
              </Typography.Text>
            </>
          ) : (
            <Typography.Title level={4} style={{ margin: 0 }}>
              {MENU_ITEMS.find((item) => item.key === (inSessions ? '/sessions' : location.pathname))?.label}
            </Typography.Title>
          )}
        </Header>
        <Content
          style={
            inSessions
              ? { display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }
              : onDashboard
                ? { display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'auto', padding: 16 }
                : { padding: 24 }
          }
        >
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/synonyms" element={<Synonyms />} />
            <Route path="/sessions" element={<Sessions />}>
              <Route index element={<EmptyState />} />
              <Route path=":sessionId" element={<SessionView />} />
            </Route>
            {/* Неизвестный адрес возвращает на главную: сервер отдаёт index.html
                на любой путь, поэтому сюда попадает и опечатка в строке браузера. */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Content>
      </Layout>
      {onDashboard && (
        <Sider theme="light" width={ADVISOR_WIDTH} className="advisor-sider" breakpoint="xl" collapsedWidth={0}>
          <AdvisorPanel />
        </Sider>
      )}
    </Layout>
  )
}
