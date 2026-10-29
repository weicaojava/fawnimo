import React, { useEffect, useMemo, useState } from 'react'
import { Layout, Menu, Dropdown, Avatar, Select, Breadcrumb, Drawer, Grid } from 'antd'
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SettingOutlined,
  UserOutlined,
  FolderOutlined,
  PictureOutlined,
  FileTextOutlined,
  ApiOutlined,
} from '@ant-design/icons'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAppStore } from '../store/useAppStore'
import { useTranslation } from 'react-i18next'
import { TaskCenter } from '../pages/aiStudio/components/TaskCenter'
import { TaskRuntimeProvider } from '../pages/aiStudio/components/TaskRuntimeProvider'
import { ThemeSwitch } from '../theme/ThemeSwitch'
import { useIdentity } from '../identity/IdentityGate'
import { IdentityService } from '../services/generated'

const { Header, Sider, Content } = Layout

/** Keep production tools in one responsive shell with a shared appearance preference. */
const MainLayout: React.FC = () => {
  const { t, i18n } = useTranslation('layout')
  const location = useLocation()
  const navigate = useNavigate()
  const identity = useIdentity()
  const screens = Grid.useBreakpoint()
  const isMobile = screens.md === false
  const [mobileOpen, setMobileOpen] = useState(false)
  useEffect(() => { setMobileOpen(false) }, [location.pathname])

  const collapsed = useAppStore((state) => state.siderCollapsed)
  const toggleCollapsed = useAppStore((state) => state.toggleSider)
  const user = useAppStore((state) => state.user)
  const language = useAppStore((state) => state.language)
  const setLanguage = useAppStore((state) => state.setLanguage)

  const selectedKeys = useMemo(() => {
    if (location.pathname === '/projects' || location.pathname.startsWith('/projects/')) return ['projects']
    if (location.pathname.startsWith('/assets')) return ['assets']
    if (location.pathname.startsWith('/prompts')) return ['prompts']
    if (location.pathname.startsWith('/files')) return ['files']
    if (location.pathname.startsWith('/agents')) return ['agents']
    if (location.pathname.startsWith('/models')) return ['models']
    if (location.pathname.startsWith('/settings')) return ['settings']
    return []
  }, [location.pathname])

  const breadcrumbItems = useMemo(() => {
    const path = location.pathname.replace(/^\/+/, '').split('/').filter(Boolean)
    if (path.length === 0) return [{ title: t('title') }]
    const items: { title: React.ReactNode; key: string }[] = []
    const pathLabels: Record<string, string> = {
      projects: '项目列表',
      assets: '资产管理',
      prompts: '提示词模板',
      files: '文件管理',
      agents: 'Agent管理',
      models: '模型管理',
      settings: t('menu.settings'),
      chapters: '章节管理',
      studio: '分镜工作室',
      prep: '章节编辑',
      shots: '分镜',
      editor: '视频剪辑',
      edit: '编辑',
    }
    path.forEach((segment, i) => {
      // 特殊：/projects/:projectId/chapters/:chapterId/* 中的 chapterId 段不展示（避免出现“章节”这一层）
      if (path[0] === 'projects' && path[2] === 'chapters' && i === 3) {
        return
      }

      // 默认：按原始路径逐段拼接
      let href = path.slice(0, i + 1).join('/')
      href = `/${href}`

      // 特殊：章节相关的中间路径段在路由里不存在，需映射到有效地址
      // /projects/:projectId/chapters/:chapterId/*
      if (path[0] === 'projects' && path[2] === 'chapters') {
        const projectId = path[1]
        const chapterId = path[3]
        if (segment === 'chapters' && i === 2) {
          // “章节管理”实际在项目工作台页
          href = `/projects/${projectId}?tab=chapters`
        } else if (i === 3) {
          // 章节 ID 段没有对应独立页面，跳到分镜页（存在路由）
          href = `/projects/${projectId}/chapters/${chapterId}/shots`
        }
      }

      const isLast = i === path.length - 1
      let label = pathLabels[segment]
      if (label === undefined) {
        if (path[0] === 'projects' && i === 1) label = '项目工作台'
        else if (path[2] === 'chapters' && i === 3) label = '章节'
        else label = segment
      }
      items.push({
        key: href,
        title: isLast ? label : <Link to={href}>{label}</Link>,
      })
    })
    return items
  }, [location.pathname, t])

  const menuItems = [
    {
      key: 'projects',
      icon: <FolderOutlined />,
      label: <Link to="/projects">项目列表</Link>,
    },
    {
      key: 'assets',
      icon: <PictureOutlined />,
      label: <Link to="/assets">资产管理</Link>,
    },
    {
      key: 'prompts',
      icon: <FileTextOutlined />,
      label: <Link to="/prompts">提示词模板</Link>,
    },
    {
      key: 'models',
      icon: <ApiOutlined />,
      label: <Link to="/models">模型管理</Link>,
    },
    {
      key: 'files',
      icon: <FolderOutlined />,
      label: <Link to="/files">文件管理</Link>,
    },
    {
      key: 'agents',
      icon: <ApiOutlined />,
      label: <Link to="/agents">Agent 管理</Link>,
    },
    {
      key: 'settings',
      icon: <SettingOutlined />,
      label: <Link to="/settings">{t('menu.settings')}</Link>,
    },
  ]

  const userMenuItems = [
    {
      key: 'profile',
      label: t('user.profile'),
      onClick: () => navigate('/settings'),
    },
    {
      type: 'divider' as const,
    },
    {
      key: 'logout',
      label: t('user.logout'),
      onClick: () => {
        if (identity) void IdentityService.logoutApiV1IdentityLogoutPost().then(() => window.location.replace('/projects'))
      },
    },
  ]

  /** Render the same real navigation in the desktop rail and the mobile drawer. */
  const navigation = (compact: boolean) => (
    <>
      <Link to="/projects" className="studio-brand" aria-label="Fawnimo 鹿帧 项目列表">
        <img src="/fawnimo.svg" alt="" width={32} height={32} />
        {!compact && <span className="studio-brand-copy"><strong>{t('title')}</strong><span>{t('subtitle')}</span></span>}
      </Link>
      <Menu mode="inline" selectedKeys={selectedKeys} items={menuItems} className="studio-navigation" />
    </>
  )

  return (
    <Layout className="studio-shell">
      {!isMobile && (
        <Sider trigger={null} collapsible collapsed={collapsed} width={216} collapsedWidth={76} className="studio-sidebar" aria-label="主要导航">
          {navigation(collapsed)}
        </Sider>
      )}
      <Drawer open={isMobile && mobileOpen} onClose={() => setMobileOpen(false)} placement="left" width={248} className="studio-mobile-navigation" title="工作台导航" bodyStyle={{ padding: 0 }}>
        {navigation(false)}
      </Drawer>

      <Layout className="studio-body">
        <Header className="studio-topbar">
          <div className="studio-location">
            <button type="button" className="studio-icon-button" aria-label={isMobile ? '打开导航' : collapsed ? '展开导航' : '收起导航'} aria-expanded={isMobile ? mobileOpen : !collapsed} onClick={() => isMobile ? setMobileOpen(true) : toggleCollapsed()}>
              {collapsed || isMobile ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            </button>
            <Breadcrumb items={breadcrumbItems} className="studio-breadcrumb" />
          </div>

          <div className="studio-header-actions">
            {identity && identity.workspaces.length > 1 && <Select aria-label="工作空间" value={identity.workspace_id}
              options={identity.workspaces.map(item => ({ value: item.id, label: item.name }))}
              onChange={(workspace_id) => { void IdentityService.switchApiV1IdentitySwitchPost({ requestBody: { workspace_id } }).then(() => window.location.replace('/projects')) }} />}
            <ThemeSwitch />
            <Select
              aria-label="界面语言"
              className="studio-language"
              value={language}
              onChange={(value) => {
                setLanguage(value)
                void i18n.changeLanguage(value)
                try { window.localStorage.setItem('jellyfish_language', value) } catch { /* Keep the language usable without storage. */ }
                document.documentElement.lang = value === 'en-US' ? 'en' : 'zh-CN'
              }}
              options={[
                { label: t('lang.zh'), value: 'zh-CN' },
                { label: t('lang.en'), value: 'en-US' },
              ]}
            />

            <Dropdown
              menu={{
                items: userMenuItems,
              }}
              placement="bottomRight"
            >
              <button type="button" className="studio-user-menu" aria-label={t('user.profile')}>
                <Avatar size={32} icon={<UserOutlined />} />
                <span className="studio-user-copy"><strong>{user.name}</strong><span>{user.role}</span></span>
              </button>
            </Dropdown>
          </div>
        </Header>

        <TaskRuntimeProvider>
          <Content className="studio-content">
            <div className="studio-page-frame">
              <Outlet />
            </div>
          </Content>
          <TaskCenter />
        </TaskRuntimeProvider>
      </Layout>
    </Layout>
  )
}

export default MainLayout
