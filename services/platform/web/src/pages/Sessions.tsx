import { Flex, theme } from 'antd'
import { Outlet } from 'react-router-dom'

import { SessionList } from '../sessions/SessionList'

/**
 * Раздел сессий агентов: журнал выбранной сессии и перечень сессий справа.
 *
 * Раскладка перенесена из xip, откуда взят и сам интерфейс сессий: слева остаётся то, что
 * не меняется от раздела к разделу, — меню платформы, — а справа содержимое раздела.
 * Раздел заполняет область под шапкой (App.tsx ограничивает её высотой окна), и
 * прокручиваются только перечень и журнал по отдельности: при прокрутке всей страницы
 * заголовок сессии уезжал бы из виду вместе с началом журнала.
 */
export function Sessions() {
  const { token } = theme.useToken()

  return (
    <Flex
      className="sessions-page"
      style={{ flex: 1, minHeight: 0, background: token.colorBgContainer }}
    >
      <Flex vertical style={{ flex: 1, minWidth: 0 }}>
        <Outlet />
      </Flex>
      <div
        style={{
          width: 300,
          flex: 'none',
          overflow: 'hidden',
          borderInlineStart: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <SessionList />
      </div>
    </Flex>
  )
}
