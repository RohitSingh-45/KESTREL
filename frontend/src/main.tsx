import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import { AuthProvider } from './auth/AuthContext'
import './styles.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
})

// #region agent log
fetch('http://127.0.0.1:7732/ingest/2d88ebec-ca80-4b2f-90b4-63275b860989',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'41078f'},body:JSON.stringify({sessionId:'41078f',hypothesisId:'F',location:'main.tsx:mount',message:'spa_mount',data:{href:window.location.href,apiBase:import.meta.env.VITE_API_BASE ?? '/api/v1'},timestamp:Date.now()})}).catch(()=>{});
window.addEventListener('error', (event) => {
  fetch('http://127.0.0.1:7732/ingest/2d88ebec-ca80-4b2f-90b4-63275b860989',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'41078f'},body:JSON.stringify({sessionId:'41078f',hypothesisId:'F',location:'main.tsx:error',message:'window_error',data:{error:String(event.error ?? event.message)},timestamp:Date.now()})}).catch(()=>{});
})
window.addEventListener('unhandledrejection', (event) => {
  fetch('http://127.0.0.1:7732/ingest/2d88ebec-ca80-4b2f-90b4-63275b860989',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'41078f'},body:JSON.stringify({sessionId:'41078f',hypothesisId:'F',location:'main.tsx:unhandledrejection',message:'unhandled_rejection',data:{error:String(event.reason)},timestamp:Date.now()})}).catch(()=>{});
})
// #endregion

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
