import { useEffect, useMemo, useState } from 'react'
import { Sidebar, type WorkspacePage } from '@/components/layout/Sidebar'
import PredictPage from './pages/PredictPage'
import PipelinePage from './pages/PipelinePage'
import DashboardPage from './pages/DashboardPage'
import DriftPage from './pages/DriftPage'
import ChatPage from './pages/ChatPage'
import { fetchDatasets, type DatasetConfig } from '@/lib/api'

const DATASET_STORAGE_KEY = 'dealight.dataset'
const WORKSPACE_PAGES = new Set<WorkspacePage>(['dashboard', 'predict', 'pipeline', 'drift', 'chat'])

export default function App() {
  const [page, setPage] = useState<WorkspacePage>(() => {
    const requested = new URLSearchParams(window.location.search).get('page') as WorkspacePage | null
    return requested && WORKSPACE_PAGES.has(requested) ? requested : 'dashboard'
  })
  const [datasets, setDatasets] = useState<DatasetConfig[]>([])
  const [datasetName, setDatasetName] = useState(
    () => window.localStorage.getItem(DATASET_STORAGE_KEY) ?? 'hbaac_sku',
  )
  const [datasetError, setDatasetError] = useState<string | null>(null)

  useEffect(() => {
    fetchDatasets()
      .then((items) => {
        setDatasets(items)
        setDatasetError(null)
        if (items.length && !items.some((item) => item.name === datasetName)) {
          setDatasetName(items[0].name)
        }
      })
      .catch((error: Error) => setDatasetError(error.message))
  }, [datasetName])

  useEffect(() => {
    window.localStorage.setItem(DATASET_STORAGE_KEY, datasetName)
  }, [datasetName])

  useEffect(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('page', page)
    window.history.replaceState({}, '', url)
  }, [page])

  const dataset = useMemo(
    () => datasets.find((item) => item.name === datasetName) ?? null,
    [datasets, datasetName],
  )

  return (
    <div className="flex h-screen flex-col bg-zinc-950 text-zinc-100 md:flex-row">
      <Sidebar
        current={page}
        onChange={setPage}
        datasets={datasets}
        selectedDataset={datasetName}
        onDatasetChange={setDatasetName}
        datasetError={datasetError}
      />
      <main className="flex-1 overflow-hidden">
        {page === 'predict' && <PredictPage />}
        {page === 'pipeline' && <PipelinePage dataset={dataset} />}
        {page === 'dashboard' && <DashboardPage dataset={dataset} />}
        {page === 'drift' && <DriftPage />}
        {page === 'chat' && <ChatPage onLogout={() => setPage('dashboard')} />}
      </main>
    </div>
  )
}
