import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Layout, Tabs } from 'antd'
import ProvidersTab from './ProvidersTab'
import ModelsTab from './ModelsTab'
import SettingsTab from './SettingsTab'
import '../assets/components/LibraryPages.css'

/** Keep provider, model and default settings together in one themed configuration workspace. */
export default function ModelManagement() {
  const [searchParams] = useSearchParams()
  const connectionModel = searchParams.get('connectionModel')
  const [activeTab, setActiveTab] = useState<string>(() => connectionModel ? 'models' : 'providers')
  useEffect(() => { if (connectionModel) setActiveTab('models') }, [connectionModel])

  return (
    <Layout className="library-page library-management" style={{ minHeight: 0 }}>
      <div className="library-management-header">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="library-page-title">模型管理</h1>
        </div>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          size="small"
          items={[
            { key: 'providers', label: '供应商' },
            { key: 'models', label: '模型' },
            { key: 'settings', label: '设置' },
          ]}
        />
      </div>

      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {activeTab === 'providers' && <ProvidersTab />}
        {activeTab === 'models' && <ModelsTab />}
        {activeTab === 'settings' && <SettingsTab />}
      </div>
    </Layout>
  )
}
