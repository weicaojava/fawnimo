import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Card, Descriptions, Drawer, Dropdown, Empty, Input, Modal, Select, Space, Table, Tabs, Tag, message } from 'antd'
import { AppstoreOutlined, CopyOutlined, DeleteOutlined, EditOutlined, ExportOutlined, HeartOutlined, ImportOutlined, PlusOutlined, StarOutlined, UnorderedListOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { AgentsService } from '../../../services/generated'
import type { AgentConfigRead as Agent, AgentConfigExport_Input as AgentConfigExport } from '../../../services/generated'
import { agentError, downloadAgentConfig } from './agentConfigIO'
import '../assets/components/LibraryPages.css'

type AgentType = Agent['type']
const TYPES: { value: AgentType; label: string }[] = [{ value: 'plot', label: '剧情提取' }, { value: 'character', label: '角色提取' }, { value: 'scene', label: '场景提取' }, { value: 'prop', label: '道具提取' }, { value: 'other', label: '其他类型' }]
/** Reuse the same category labels in filters, records and details. */
const typeName = (type: AgentType) => TYPES.find(item => item.value === type)?.label ?? type

/** Manage real saved Agent configurations while keeping unavailable model execution explicit. */
export default function AgentManagement() {
  const navigate = useNavigate()
  const importInput = useRef<HTMLInputElement>(null)
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [sort, setSort] = useState('updated')
  const [view, setView] = useState<'table' | 'card'>(() => window.matchMedia('(max-width:640px)').matches ? 'card' : 'table')
  const [selected, setSelected] = useState<React.Key[]>([])
  const [detail, setDetail] = useState<Agent | null>(null)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [importing, setImporting] = useState(false)
  const [name, setName] = useState('')
  const [type, setType] = useState<AgentType>('plot')
  const [description, setDescription] = useState('')

  /** Read all configuration pages so local filters and totals refer to actual stored records. */
  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const all: Agent[] = []
      let page = 1
      let total = 0
      do {
        const response = await AgentsService.listAgentConfigs({ page, pageSize: 100 })
        const items = response.data?.items ?? []
        total = response.data?.pagination.total ?? 0
        all.push(...items)
        if (!items.length) break
        page += 1
      } while (all.length < total)
      setAgents(all)
      setDetail(previous => previous ? all.find(item => item.id === previous.id) ?? null : null)
      setSelected(previous => previous.filter(id => all.some(item => item.id === id)))
    } catch (failure) { setError(agentError(failure, '加载 Agent 配置失败')) }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])

  const filtered = useMemo(() => agents.filter(agent => (filter === 'all' || (filter === 'favorites' ? agent.is_favorite : agent.type === filter)) && (!search || [agent.name, agent.description, typeName(agent.type), agent.created_by].some(value => (value ?? '').toLowerCase().includes(search.toLowerCase())))).sort((a, b) => sort === 'name' ? a.name.localeCompare(b.name) : b.updated_at.localeCompare(a.updated_at)), [agents, search, filter, sort])

  /** Create a real stored draft and navigate only when the server returns its identity. */
  const create = async () => {
    if (!name.trim()) { message.warning('请输入 Agent 名称'); return }
    setSaving(true)
    try {
      const response = await AgentsService.createAgentConfig({ requestBody: { name: name.trim(), type, description: description.trim() } })
      if (!response.data) throw new Error('Missing created configuration')
      setCreating(false); setName(''); setDescription('')
      navigate(`/agents/${response.data.id}/edit`)
    } catch (failure) { message.error(agentError(failure, '创建失败')) }
    finally { setSaving(false) }
  }

  /** Persist favorites and one default per type, guarded by the displayed saved version. */
  const updateFlag = async (agent: Agent, field: 'is_default' | 'is_favorite', value: boolean) => {
    try { await AgentsService.updateAgentConfig({ agentId: agent.id, requestBody: { [field]: value, expected_version: agent.version } }); await load() }
    catch (failure) { message.error(agentError(failure, '修改失败，请刷新重试')) }
  }

  /** Confirm destructive changes and report partial batch failures without false success. */
  const remove = (ids: string[]) => Modal.confirm({ title: ids.length === 1 ? '删除 Agent 配置？' : `删除 ${ids.length} 个 Agent 配置？`, content: '删除后该配置及其版本历史将移除，其他配置不会受影响。', okText: '删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: async () => {
    try { for (const id of ids) await AgentsService.deleteAgentConfig({ agentId: id }); await load(); message.success('配置已删除') }
    catch (failure) { await load(); message.error(agentError(failure, '删除未全部完成，请检查剩余配置')) }
  } })

  /** Copy exact saved settings into an independently editable non-default configuration. */
  const copy = async (agent: Agent) => {
    try { await AgentsService.copyAgentConfig({ agentId: agent.id }); await load(); message.success('已创建副本') }
    catch (failure) { message.error(agentError(failure, '复制失败')) }
  }

  /** Download only the portable payload returned after successful backend validation. */
  const exportConfig = async (agent: Agent) => {
    try { const response = await AgentsService.exportAgentConfig({ agentId: agent.id }); if (!response.data) throw new Error(); downloadAgentConfig(response.data, agent.name) }
    catch (failure) { message.error(agentError(failure, '导出失败')) }
  }

  /** Validate imports through the typed API and keep existing defaults unchanged. */
  const importConfig = async (file?: File) => {
    if (!file) return
    setImporting(true)
    try {
      if (file.size > 524288) throw new Error('Import exceeds limit')
      await AgentsService.importAgentConfig({ requestBody: JSON.parse(await file.text()) as AgentConfigExport })
      await load(); message.success('配置已导入')
    } catch (failure) { message.error(agentError(failure, '导入失败，请选择不超过 512 KiB 的有效 Agent 配置 JSON')) }
    finally { setImporting(false); if (importInput.current) importInput.current.value = '' }
  }

  /** Offer the same persistent actions in table rows, cards and the detail drawer. */
  const actions = (agent: Agent) => <Space wrap size="small">
    <Button icon={<EditOutlined />} onClick={() => navigate(`/agents/${agent.id}/edit`)}>编辑</Button>
    <Button onClick={() => navigate(`/agents/${agent.id}/edit#agent-execution`)}>执行与记录</Button>
    <Dropdown trigger={['click']} menu={{ items: [
      { key: 'copy', label: '复制', icon: <CopyOutlined />, onClick: () => void copy(agent) },
      { key: 'export', label: '导出配置', icon: <ExportOutlined />, onClick: () => void exportConfig(agent) },
      { key: 'favorite', label: agent.is_favorite ? '取消收藏' : '收藏', icon: <HeartOutlined />, onClick: () => void updateFlag(agent, 'is_favorite', !agent.is_favorite) },
      { key: 'default', label: agent.is_default ? '已为本类型默认' : '设为本类型默认', disabled: agent.is_default, icon: <StarOutlined />, onClick: () => Modal.confirm({ title: '替换该类型的默认 Agent？', content: '只修改配置选择，不会执行模型。', onOk: () => updateFlag(agent, 'is_default', true) }) },
      { key: 'delete', label: '删除', danger: true, icon: <DeleteOutlined />, onClick: () => remove([agent.id]) },
    ] }}><Button aria-label={`更多 ${agent.name} 操作`}>更多</Button></Dropdown>
  </Space>

  return <div className="library-page library-management">
    <header className="library-management-header">
      <div className="flex items-center justify-between gap-4 flex-wrap"><h1 className="library-page-title">Agent 管理</h1><Space wrap>
        <input hidden ref={importInput} type="file" accept=".json,application/json" aria-label="导入 Agent 配置文件" onChange={event => void importConfig(event.target.files?.[0])} />
        <Button icon={<ImportOutlined />} loading={importing} onClick={() => importInput.current?.click()}>导入配置</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>创建新 Agent</Button>
      </Space></div>
      <Tabs activeKey={filter} onChange={setFilter} items={[{ key: 'all', label: `全部 ${agents.length}` }, { key: 'favorites', label: '收藏' }, ...TYPES.map(item => ({ key: item.value, label: item.label }))]} />
    </header>
    <Alert className="library-notice" type="info" showIcon message="Agent 配置与真实执行记录" description="编辑、复制、收藏、导入导出和版本历史均写入本地数据库。进入“执行与记录”后选择文本模型并确认调用；未配置模型时会明确提示原因。" />
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => void load()}>重试</Button>} />}
    <div className="library-management-toolbar"><Space wrap>
      <Input.Search value={search} onChange={event => setSearch(event.target.value)} placeholder="按名称、类型或创建者搜索" aria-label="搜索 Agent" allowClear />
      <Select aria-label="Agent 排序" value={sort} onChange={setSort} options={[{ value: 'updated', label: '最近更新' }, { value: 'name', label: '名称' }]} />
      <Button icon={<UnorderedListOutlined />} aria-label="列表视图" aria-pressed={view === 'table'} onClick={() => setView('table')} />
      <Button icon={<AppstoreOutlined />} aria-label="卡片视图" aria-pressed={view === 'card'} onClick={() => setView('card')} />
      <Button danger icon={<DeleteOutlined />} disabled={!selected.length} onClick={() => remove(selected.map(String))}>批量删除</Button>
      <Button onClick={() => void load()} loading={loading}>刷新</Button>
    </Space></div>
    <div className="library-data-region">
      {view === 'table' ? <Table<Agent> rowKey="id" loading={loading} dataSource={filtered} scroll={{ x: 1100 }} rowSelection={{ selectedRowKeys: selected, onChange: setSelected }} onRow={agent => ({ tabIndex: 0, onClick: () => setDetail(agent), onKeyDown: event => { if (event.target === event.currentTarget && event.key === 'Enter') setDetail(agent) } })} columns={[
        { title: '名称', dataIndex: 'name', key: 'name', width: 220, render: (value, agent) => <Space wrap><span>{value}</span>{agent.is_default && <Tag>默认</Tag>}{agent.is_favorite && <Tag>已收藏</Tag>}</Space> },
        { title: '类型', dataIndex: 'type', key: 'type', width: 130, render: typeName },
        { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
        { title: '版本', dataIndex: 'version', key: 'version', width: 80 },
        { title: '更新时间', dataIndex: 'updated_at', key: 'updated', width: 190, render: value => new Date(value).toLocaleString() },
        { title: '操作', key: 'actions', width: 280, render: (_, agent) => <div onClick={event => event.stopPropagation()}>{actions(agent)}</div> },
      ]} /> : <div className="library-card-grid">{filtered.map(agent => <Card key={agent.id} className="library-record-card" title={<Button type="link" onClick={() => setDetail(agent)}>{agent.name}</Button>} extra={agent.is_favorite && <Tag>已收藏</Tag>}><Space direction="vertical"><div><Tag>{typeName(agent.type)}</Tag>{agent.is_default && <Tag>默认</Tag>}</div><p>{agent.description || '暂无描述'}</p><p className="library-muted">版本 {agent.version}，{new Date(agent.updated_at).toLocaleString()}</p>{actions(agent)}</Space></Card>)}</div>}
      {!loading && !filtered.length && <Empty description="暂无 Agent 配置" />}
    </div>
    <Drawer open={!!detail} title={detail?.name} onClose={() => setDetail(null)} width={480}>{detail && <Space direction="vertical" className="w-full" size="large"><Descriptions column={1} items={[{ key: 'type', label: '类型', children: typeName(detail.type) }, { key: 'description', label: '描述', children: detail.description || '暂无描述' }, { key: 'version', label: '版本', children: detail.version }, { key: 'creator', label: '创建者', children: detail.created_by }, { key: 'updated', label: '更新时间', children: new Date(detail.updated_at).toLocaleString() }]} />{actions(detail)}</Space>}</Drawer>
    <Modal open={creating} title="创建新 Agent" onCancel={() => setCreating(false)} onOk={() => void create()} confirmLoading={saving} okText="创建" cancelText="取消"><Space direction="vertical" className="w-full" size="middle"><label htmlFor="agent-create-name">名称</label><Input id="agent-create-name" value={name} onChange={event => setName(event.target.value)} maxLength={255} /><label htmlFor="agent-create-type">类型</label><Select id="agent-create-type" className="w-full" value={type} onChange={setType} options={TYPES} /><label htmlFor="agent-create-description">描述</label><Input.TextArea id="agent-create-description" value={description} onChange={event => setDescription(event.target.value)} maxLength={8000} rows={3} /></Space></Modal>
  </div>
}
