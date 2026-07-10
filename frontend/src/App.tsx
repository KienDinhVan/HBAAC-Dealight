import { useState } from 'react'
import { Sidebar, type WorkspacePage } from '@/components/layout/Sidebar'
import PredictPage from './pages/PredictPage'
import PipelinePage from './pages/PipelinePage'
import DashboardPage from './pages/DashboardPage'
import DriftPage from './pages/DriftPage'
import ChatPage from './pages/ChatPage'

export default function App() {
  const [page, setPage] = useState<WorkspacePage>('dashboard')

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      <Sidebar current={page} onChange={setPage} />
      <main className="flex-1 overflow-hidden">
        {page === 'predict' && <PredictPage />}
        {page === 'pipeline' && <PipelinePage />}
        {page === 'dashboard' && <DashboardPage />}
        {page === 'drift' && <DriftPage />}
        {page === 'chat' && <ChatPage onLogout={() => setPage('dashboard')} />}
      </main>
    </div>
  )
}
