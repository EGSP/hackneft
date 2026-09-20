import { DatabaseOutlined, LineChartOutlined } from '@ant-design/icons'
import { Layout, Menu, Typography } from 'antd'
import { useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { Dashboard } from './pages/Dashboard'
import { Synonyms } from './pages/Synonyms'

const { Sider, Content, Header } = Layout

const MENU_ITEMS = [
  { key: '/', icon: <LineChartOutlined />, label: 'Главная' },
  { key: '/synonyms', icon: <DatabaseOutlined />, label: 'Справочник синонимов' },
]

export function App() {
  const navigate = useNavigate()
  const location = useLocation()
  // Состояние панели отслеживается здесь, а не только внутри Sider: в свёрнутом виде
  // ширины не хватает на название, и текст заголовка заменяется сокращением.
  const [collapsed, setCollapsed] = useState(false)

  return (
    <Layout style={{ minHeight: '100vh' }}>
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
          selectedKeys={[location.pathname]}
          items={MENU_ITEMS}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', borderBottom: '1px solid #f0f0f0', paddingInline: 24 }}>
          <Typography.Text type="secondary">Контроль качества дизельного топлива</Typography.Text>
        </Header>
        <Content style={{ padding: 24 }}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/synonyms" element={<Synonyms />} />
            {/* Неизвестный адрес возвращает на главную: сервер отдаёт index.html
                на любой путь, поэтому сюда попадает и опечатка в строке браузера. */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}
