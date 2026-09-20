import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Form,
  Input,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createSensorName,
  deleteSensorName,
  fetchSensorNames,
  type SensorNameItem,
} from '../api'

interface FormValues {
  sensor_code: string
  name: string
}

/**
 * Справочник синонимов: перечень имён датчиков из таблицы sensor_names.
 *
 * Имя, совпадающее с кодом, обозначает сам датчик и удалению не подлежит — сервер
 * такое обращение отклоняет, поэтому кнопка удаления для таких строк не показывается.
 */
export function Synonyms() {
  const [items, setItems] = useState<SensorNameItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [form] = Form.useForm<FormValues>()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await fetchSensorNames())
      setError(null)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const codes = useMemo(
    () => items.filter((item) => item.is_code).map((item) => ({ value: item.sensor_code })),
    [items],
  )

  const handleCreate = async (values: FormValues) => {
    try {
      await createSensorName(values.sensor_code, values.name)
      message.success(`Синоним «${values.name}» добавлен датчику ${values.sensor_code}`)
      form.resetFields()
      await load()
    } catch (reason) {
      message.error((reason as Error).message)
    }
  }

  const handleDelete = async (item: SensorNameItem) => {
    try {
      await deleteSensorName(item.sensor_code, item.name)
      message.success('Синоним удалён')
      await load()
    } catch (reason) {
      message.error((reason as Error).message)
    }
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Справочник синонимов
        </Typography.Title>
        <Typography.Text type="secondary">
          Имена, под которыми датчик известен в разных системах. Выборка данных по любому
          из них возвращает одни и те же измерения.
        </Typography.Text>
      </div>

      {error && <Alert type="error" showIcon message={error} />}

      <Card title="Добавить синоним">
        <Form form={form} layout="inline" onFinish={handleCreate}>
          <Form.Item
            name="sensor_code"
            label="Код датчика"
            rules={[{ required: true, message: 'Укажите код' }]}
          >
            <AutoComplete
              options={codes}
              style={{ width: 200 }}
              placeholder="Q21"
              filterOption={(input, option) =>
                (option?.value ?? '').toLowerCase().includes(input.toLowerCase())
              }
            />
          </Form.Item>
          <Form.Item
            name="name"
            label="Синоним"
            rules={[{ required: true, message: 'Укажите синоним' }]}
          >
            <Input style={{ width: 280 }} placeholder="Сера ПАК" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit">
              Добавить
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Table<SensorNameItem>
        rowKey={(item) => `${item.sensor_code}::${item.name}`}
        dataSource={items}
        loading={loading}
        size="middle"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        columns={[
          {
            title: 'Код датчика',
            dataIndex: 'sensor_code',
            width: 180,
            sorter: (a, b) => a.sensor_code.localeCompare(b.sensor_code),
            filters: codes.map((code) => ({ text: code.value, value: code.value })),
            onFilter: (value, item) => item.sensor_code === value,
          },
          {
            title: 'Имя',
            dataIndex: 'name',
            render: (name: string, item) =>
              item.is_code ? (
                <Space>
                  <Typography.Text strong>{name}</Typography.Text>
                  <Tag>код датчика</Tag>
                </Space>
              ) : (
                name
              ),
          },
          {
            title: '',
            width: 120,
            render: (_, item) =>
              item.is_code ? null : (
                <Popconfirm
                  title="Удалить синоним?"
                  okText="Удалить"
                  cancelText="Отмена"
                  onConfirm={() => handleDelete(item)}
                >
                  <Button type="link" danger>
                    Удалить
                  </Button>
                </Popconfirm>
              ),
          },
        ]}
      />
    </Space>
  )
}
