import { useEffect, useState } from 'react'
import { Alert, Button, Input, InputNumber, List, Modal, Select, Slider, Space, Tag, message } from 'antd'
import { ArrowLeftOutlined, CopyOutlined, DeleteOutlined, DownloadOutlined, HistoryOutlined, PlusOutlined, SaveOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { AgentsService } from '../../../services/generated'
import type { AgentConfigRead, AgentConfigCreate, AgentConfigRevisionRead, AgentNode } from '../../../services/generated'
import { agentError, downloadAgentConfig } from './agentConfigIO'
import AgentRunsPanel from './AgentRunsPanel'
import '../assets/components/LibraryPages.css'

const TYPES: { value: AgentConfigRead['type']; label: string }[] = [{ value: 'plot', label: '剧情提取' }, { value: 'character', label: '角色提取' }, { value: 'scene', label: '场景提取' }, { value: 'prop', label: '道具提取' }, { value: 'other', label: '其他类型' }]

/** Persist every exposed Agent setting and real workflow revision without simulated execution. */
export default function AgentEdit() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [draft, setDraft] = useState<AgentConfigRead | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [dirty, setDirty] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>('llm1')
  const [history, setHistory] = useState<AgentConfigRevisionRead[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)

  /** Load saved fields as returned, rather than replacing the prompt with a preset value. */
  const load = async () => {
    if (!id) return
    setLoading(true); setError('')
    try {
      const response = await AgentsService.getAgentConfig({ agentId: id })
      if (!response.data) throw new Error()
      setDraft(response.data); setDirty(false)
      setSelectedNodeId(response.data.workflow?.find(node => node.type === 'llm')?.id ?? null)
    } catch (failure) { setError(agentError(failure, '加载 Agent 配置失败')) }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [id])
  useEffect(() => { if (draft && window.location.hash === '#agent-execution') document.getElementById('agent-execution')?.scrollIntoView({ block: 'start' }) }, [draft?.id])

  /** Keep editable form fields together so each save persists a complete validated draft. */
  const change = (values: Partial<AgentConfigRead>) => { setDraft(previous => previous ? { ...previous, ...values } : null); setDirty(true) }
  const selectedNode = draft?.workflow?.find(node => node.id === selectedNodeId)

  /** Update a selected node without discarding sibling nodes or global Agent defaults. */
  const changeNode = (values: Partial<AgentNode>) => {
    if (!draft || !selectedNodeId) return
    change({ workflow: draft.workflow?.map(node => node.id === selectedNodeId ? { ...node, ...values } : node) })
  }

  /** Submit saved fields with the displayed version to avoid overwriting another editor. */
  const save = async (restored?: AgentConfigCreate) => {
    if (!id || !draft) return
    if (!draft.name.trim() && !restored) { message.warning('请输入名称'); return }
    setSaving(true)
    try {
      const body = restored ?? { name: draft.name.trim(), type: draft.type, description: draft.description, is_default: draft.is_default, is_favorite: draft.is_favorite, prompt: draft.prompt, temperature: draft.temperature, top_p: draft.top_p, parameters: draft.parameters, workflow: draft.workflow }
      const response = await AgentsService.updateAgentConfig({ agentId: id, requestBody: { ...body, expected_version: draft.version } })
      if (!response.data) throw new Error()
      setDraft(response.data); setDirty(false); setHistoryOpen(false); message.success('配置已保存')
    } catch (failure) { message.error(agentError(failure, '保存失败，修改仍保留在当前表单')) }
    finally { setSaving(false) }
  }

  /** Inspect actual stored revisions, leaving unsaved edits intact until an explicit restore. */
  const openHistory = async () => {
    if (!id) return
    setHistoryOpen(true); setHistoryLoading(true)
    try { const response = await AgentsService.listAgentConfigRevisions({ agentId: id }); setHistory(response.data ?? []) }
    catch (failure) { message.error(agentError(failure, '加载版本历史失败')) }
    finally { setHistoryLoading(false) }
  }

  /** Insert a real editable LLM node before the fixed ending, within the server's node limit. */
  const addNode = () => {
    if (!draft) return
    const workflow = draft.workflow ?? []
    if (workflow.length >= 32) { message.warning('最多支持 32 个节点'); return }
    const node: AgentNode = { id: crypto.randomUUID(), type: 'llm', label: `LLM 提取 ${workflow.length - 1}`, prompt: '', temperature: draft.temperature ?? .7, top_p: draft.top_p ?? .9 }
    change({ workflow: [...workflow.slice(0, -1), node, workflow[workflow.length - 1]] })
    setSelectedNodeId(node.id)
  }

  /** Reorder intermediate nodes while keeping start and end at their required positions. */
  const moveNode = (direction: number) => {
    if (!draft || !selectedNode) return
    const workflow = [...(draft.workflow ?? [])]
    const index = workflow.findIndex(node => node.id === selectedNodeId)
    const next = index + direction
    if (index <= 0 || index >= workflow.length - 1 || next <= 0 || next >= workflow.length - 1) return
    ;[workflow[index], workflow[next]] = [workflow[next], workflow[index]]
    change({ workflow })
  }

  /** Download the latest saved configuration, making unsaved-form behavior explicit. */
  const exportConfig = async () => {
    if (!id || !draft) return
    try { const response = await AgentsService.exportAgentConfig({ agentId: id }); if (!response.data) throw new Error(); downloadAgentConfig(response.data, draft.name) }
    catch (failure) { message.error(agentError(failure, '导出失败')) }
  }

  if (loading) return <div className="library-page">加载中…</div>
  if (!draft) return <div className="library-page"><Alert type="error" message={error || 'Agent 配置不存在'} action={<Button onClick={() => void load()}>重试</Button>} /><Button onClick={() => navigate('/agents')}>返回列表</Button></div>

  return <div className="library-page library-management library-agent-editor">
    <header className="library-management-header">
      <div className="flex items-center justify-between gap-4 flex-wrap"><h1 className="library-page-title">{draft.name}</h1><Space wrap><Tag>已保存版本 {draft.version}</Tag>{dirty && <Tag color="orange">有未保存修改</Tag>}</Space></div>
    </header>
    <div className="library-editor-toolbar"><Space wrap>
      <Button icon={<ArrowLeftOutlined />} onClick={() => dirty ? Modal.confirm({ title: '离开前放弃未保存修改？', onOk: () => navigate('/agents') }) : navigate('/agents')}>返回列表</Button>
      <Button icon={<HistoryOutlined />} onClick={() => void openHistory()}>版本管理</Button>
      <Button icon={<DownloadOutlined />} onClick={() => void exportConfig()} title={dirty ? '导出当前已保存版本，不包含未保存修改' : '导出已保存配置'}>导出配置</Button>
      <Button icon={<CopyOutlined />} onClick={() => Modal.confirm({ title: '复制当前已保存版本？', content: '未保存修改不会包含在副本中。', onOk: async () => { try { const response = await AgentsService.copyAgentConfig({ agentId: id! }); if (response.data) navigate(`/agents/${response.data.id}/edit`) } catch (failure) { message.error(agentError(failure, '复制失败')) } } })}>复制配置</Button>
      <Button icon={<ThunderboltOutlined />} onClick={() => document.getElementById('agent-execution')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>执行与记录</Button>
      <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>保存</Button>
    </Space></div>
    <Alert className="library-notice" type="info" showIcon message="配置编辑与真实执行分别操作" description="先保存配置，再在下方选择文本模型并确认执行。执行记录包含每个节点的真实输入输出；未配置模型时仍可保存与管理配置。" />
    <div className="library-data-region">
      <section className="library-form-group"><h2 className="library-section-title">基础配置</h2><Space direction="vertical" className="w-full" size="middle">
        <label htmlFor="agent-name">名称</label><Input id="agent-name" value={draft.name} maxLength={255} onChange={event => change({ name: event.target.value })} />
        <label htmlFor="agent-type">类型</label><Select id="agent-type" className="w-full" value={draft.type} options={TYPES} onChange={type => change({ type })} />
        <label htmlFor="agent-description">描述</label><Input.TextArea id="agent-description" value={draft.description} maxLength={8000} rows={2} onChange={event => change({ description: event.target.value })} />
        <label htmlFor="agent-prompt">Agent 提示词</label><Input.TextArea id="agent-prompt" aria-label="Agent 提示词" value={draft.prompt} maxLength={64000} rows={5} onChange={event => change({ prompt: event.target.value })} />
        <label>新增节点默认温度 {draft.temperature}</label><Slider aria-label="默认温度" min={0} max={2} step={.1} value={draft.temperature} onChange={temperature => change({ temperature })} />
        <label>新增节点默认 Top-P {draft.top_p}</label><Slider aria-label="默认 Top-P" min={0} max={1} step={.05} value={draft.top_p} onChange={top_p => change({ top_p })} />
        {draft.type === 'plot' && <label>主线保留阈值 <InputNumber aria-label="主线保留阈值" min={0} max={1} step={.1} value={draft.parameters?.plot_threshold ?? .5} onChange={value => value !== null && change({ parameters: { ...draft.parameters, plot_threshold: value } })} /></label>}
        {draft.type === 'character' && <label>角色数量上限 <InputNumber aria-label="角色数量上限" min={1} max={100} value={draft.parameters?.character_limit ?? 20} onChange={value => value !== null && change({ parameters: { ...draft.parameters, character_limit: value } })} /></label>}
      </Space></section>
      <section className="library-form-group"><div className="flex items-center justify-between gap-4 flex-wrap"><h2 className="library-section-title">工作流配置</h2><Button icon={<PlusOutlined />} onClick={addNode}>添加节点</Button></div>
        <Space direction="vertical" className="w-full" size="middle">
          <Space wrap>{draft.workflow?.map((node, index) => <Button key={node.id} className="library-workflow-node" aria-pressed={selectedNodeId === node.id} onClick={() => setSelectedNodeId(node.id)}>{index + 1}. {node.label}</Button>)}</Space>
          {selectedNode && <Space direction="vertical" className="w-full" size="middle"><label htmlFor="agent-node-label">节点名称</label><Input id="agent-node-label" maxLength={80} value={selectedNode.label} onChange={event => changeNode({ label: event.target.value })} />
            {selectedNode.type === 'llm' && <><label htmlFor="agent-node-prompt">节点提示词</label><Input.TextArea id="agent-node-prompt" aria-label="节点提示词" rows={5} maxLength={32000} value={selectedNode.prompt} onChange={event => changeNode({ prompt: event.target.value })} /><label>节点温度 {selectedNode.temperature}</label><Slider aria-label="节点温度" min={0} max={2} step={.1} value={selectedNode.temperature} onChange={temperature => changeNode({ temperature })} /><label>节点 Top-P {selectedNode.top_p}</label><Slider aria-label="节点 Top-P" min={0} max={1} step={.05} value={selectedNode.top_p} onChange={top_p => changeNode({ top_p })} /><Space><Button onClick={() => moveNode(-1)}>上移</Button><Button onClick={() => moveNode(1)}>下移</Button><Button danger icon={<DeleteOutlined />} onClick={() => { change({ workflow: draft.workflow?.filter(node => node.id !== selectedNode.id) }); setSelectedNodeId(null) }}>删除节点</Button></Space></>}
          </Space>}
        </Space>
      </section>
      <AgentRunsPanel key={draft.id} agent={draft} dirty={dirty} />
    </div>
    <Modal open={historyOpen} title="已保存版本" onCancel={() => setHistoryOpen(false)} footer={null}><List loading={historyLoading} dataSource={history} renderItem={revision => <List.Item actions={[<Button key="restore" disabled={revision.version === draft.version || saving} onClick={() => Modal.confirm({ title: `恢复版本 ${revision.version} 的配置？`, content: '将创建新的保存版本，既有历史保留；当前未保存修改会被替换。', onOk: () => save(revision.document) })}>恢复此版</Button>]}><List.Item.Meta title={`版本 ${revision.version}`} description={new Date(revision.saved_at).toLocaleString()} /></List.Item>} /></Modal>
  </div>
}
