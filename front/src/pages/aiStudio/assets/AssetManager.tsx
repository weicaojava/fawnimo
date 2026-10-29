import { useEffect, useState } from 'react'
import { Tabs } from 'antd'
import { useSearchParams } from 'react-router-dom'
import { ActorsTab } from './tabs/ActorsTab'
import { ScenesTab } from './tabs/ScenesTab'
import { PropsTab } from './tabs/PropsTab'
import { CostumesTab } from './tabs/CostumesTab'
import './components/LibraryPages.css'

const TAB_PARAM = 'tab'
type AssetTabKey = 'actor' | 'scene' | 'prop' | 'costume'

function isValidTab(tab: string | null): tab is AssetTabKey {
  return tab === 'actor' || tab === 'scene' || tab === 'prop' || tab === 'costume'
}

/** Present the shared asset archive while retaining its URL-backed type selection. */
const AssetManager = () => {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabFromUrl = searchParams.get(TAB_PARAM)

  const [activeTab, setActiveTab] = useState<AssetTabKey>(() => (isValidTab(tabFromUrl) ? tabFromUrl : 'actor'))

  useEffect(() => {
    if (isValidTab(tabFromUrl)) {
      setActiveTab(tabFromUrl)
    } else if (tabFromUrl === null || tabFromUrl === '') {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set(TAB_PARAM, 'actor')
          return next
        },
        { replace: true },
      )
    }
  }, [tabFromUrl, setSearchParams])

  const setTabInUrl = (tab: AssetTabKey) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.set(TAB_PARAM, tab)
        return next
      },
      { replace: true },
    )
  }

  return (
    <div className="library-page library-asset-library">
      <header className="library-page-header">
        <h1 className="library-page-title">资产管理</h1>
      </header>
        <Tabs
          className="library-asset-tabs"
          activeKey={activeTab}
          onChange={(k) => {
            if (isValidTab(k)) setTabInUrl(k)
          }}
          items={[
            { key: 'actor', label: '演员', children: <ActorsTab /> },
            { key: 'scene', label: '场景', children: <ScenesTab /> },
            { key: 'prop', label: '道具', children: <PropsTab /> },
            { key: 'costume', label: '服装', children: <CostumesTab /> },
          ]}
        />
    </div>
  )
}

export default AssetManager
