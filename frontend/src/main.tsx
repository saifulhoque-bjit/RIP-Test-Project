import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Provider } from 'react-redux'
import { store } from '@/store'
import PageHeaderProvider from '@/contexts/PageHeaderProvider'
import App from '@/App'
import '@/styles/globals.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Provider store={store}>
      <PageHeaderProvider>
        <App />
      </PageHeaderProvider>
    </Provider>
  </StrictMode>,
)
