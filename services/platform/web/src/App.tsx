import { DatabaseOutlined, LineChartOutlined, RobotOutlined } from '@ant-design/icons'
import { Layout, Menu, Typography } from 'antd'
import { useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { Dashboard } from './pages/Dashboard'
import { Sessions } from './pages/Sessions'
import { Synonyms } from './pages/Synonyms'
import { EmptyState } from './sessions/EmptyState'
import { SessionView } from './sessions/SessionView'

const { Sider, Content, Header } = Layout

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

  return (
    <Layout style={inSessions ? { height: '100dvh' } : { minHeight: '100vh' }}>
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
        <Header style={{ background: '#fff', borderBottom: '1px solid #f0f0f0', paddingInline: 24 }}>
          <Typography.Text type="secondary">Контроль качества дизельного топлива</Typography.Text>
        </Header>
        <Content
          style={
            inSessions
              ? { display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }
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
    </Layout>
  )
}
