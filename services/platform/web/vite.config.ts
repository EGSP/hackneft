import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

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
        // Библиотеки вынесены в отдельные файлы: они меняются редко, и после правки
        // кода интерфейса браузер перезагружает только небольшой файл приложения,
        // а не весь бандл целиком.
        manualChunks: {
          echarts: ['echarts'],
          antd: ['antd', '@ant-design/icons'],
          react: ['react', 'react-dom', 'react-router-dom'],
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
