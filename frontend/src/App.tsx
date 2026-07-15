import { useCallback, useEffect, useMemo, useState } from 'react'
import { Sidebar, type WorkspacePage } from '@/components/layout/Sidebar'
import PredictPage from './pages/PredictPage'
import PipelinePage from './pages/PipelinePage'
import DashboardPage from './pages/DashboardPage'
import DriftPage from './pages/DriftPage'
import ChatPage from './pages/ChatPage'
import ModelsPage from './pages/ModelsPage'
import ApprovalsPage from './pages/ApprovalsPage'
import LoginPage from './pages/LoginPage'
import {
  fetchDatasets,
  fetchMe,
  listPromotionRequests,
  type AuthUser,
  type DatasetConfig,
} from '@/lib/api'
import { clearToken, getToken } from '@/lib/auth'

const DATASET_STORAGE_KEY = 'dealight.dataset'
const WORKSPACE_PAGES = new Set<WorkspacePage>([
  'dashboard', 'predict', 'pipeline', 'drift', 'chat', 'models', 'approvals',
])

export default function App() {
  const [page, setPage] = useState<WorkspacePage>(() => {
    const requested = new URLSearchParams(window.location.search).get('page') as WorkspacePage | null
    return requested && WORKSPACE_PAGES.has(requested) ? requested : 'dashboard'
  })
  const [user, setUser] = useState<AuthUser | null>(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [datasets, setDatasets] = useState<DatasetConfig[]>([])
  const [datasetName, setDatasetName] = useState(
    () => window.localStorage.getItem(DATASET_STORAGE_KEY) ?? 'hbaac_sku',
  )
  const [datasetError, setDatasetError] = useState<string | null>(null)
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    if (!getToken()) {
      setAuthChecked(true)
      return
    }
    fetchMe()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setAuthChecked(true))
  }, [])

  useEffect(() => {
    const onLogout = () => setUser(null)
    window.addEventListener('dealight:logout', onLogout)
    return () => window.removeEventListener('dealight:logout', onLogout)
  }, [])

  useEffect(() => {
    if (!user) return
    fetchDatasets()
      .then((items) => {
        setDatasets(items)
        setDatasetError(null)
        if (items.length && !items.some((item) => item.name === datasetName)) {
          setDatasetName(items[0].name)
        }
      })
      .catch((error: Error) => setDatasetError(error.message))
  }, [user, datasetName])

  useEffect(() => {
    window.localStorage.setItem(DATASET_STORAGE_KEY, datasetName)
  }, [datasetName])

  useEffect(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('page', page)
    window.history.replaceState({}, '', url)
  }, [page])

  useEffect(() => {
    if (user && user.role !== 'manager' && page === 'approvals') setPage('dashboard')
  }, [user, page])

  const refreshPending = useCallback(() => {
    if (user?.role !== 'manager') return
    listPromotionRequests('pending')
      .then(({ items }) => setPendingCount(items.length))
      .catch(() => setPendingCount(0))
  }, [user])

  useEffect(refreshPending, [refreshPending])

  const handleLogout = () => {
    clearToken()
    setUser(null)
  }

  const dataset = useMemo(
    () => datasets.find((item) => item.name === datasetName) ?? null,
    [datasets, datasetName],
  )

  if (!authChecked) return null
  if (!user) return <LoginPage onLogin={setUser} />

  return (
    <div className="flex h-screen flex-col bg-zinc-950 text-zinc-100 md:flex-row">
      <Sidebar
        current={page}
        onChange={setPage}
        datasets={datasets}
        selectedDataset={datasetName}
        onDatasetChange={setDatasetName}
        datasetError={datasetError}
        user={user}
        onLogout={handleLogout}
        pendingCount={pendingCount}
      />
      <main className="flex-1 overflow-hidden">
        {page === 'predict' && <PredictPage />}
        {page === 'pipeline' && <PipelinePage dataset={dataset} />}
        {page === 'dashboard' && <DashboardPage dataset={dataset} />}
        {page === 'drift' && <DriftPage />}
        {page === 'chat' && <ChatPage onLogout={() => setPage('dashboard')} />}
        {page === 'models' && <ModelsPage dataset={datasetName} />}
        {page === 'approvals' && user.role === 'manager' && (
          <ApprovalsPage onDecided={refreshPending} />
        )}
      </main>
    </div>
  )
}
