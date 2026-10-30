import { useEffect, useState, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Alert,
  Layout,
  Input,
  Button,
  Table,
  Tag,
  Space,
  Tree,
  Card,
  Dropdown,
  Drawer,
  Modal,
  Form,
  Select,
  Switch,
  message,
  Tooltip,
  Empty,
  Grid,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  CopyOutlined,
  MenuOutlined,
  AppstoreOutlined,
  UnorderedListOutlined,
  DownOutlined,
  RightOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { LlmService } from '../../../services/generated/services/LlmService'
import ModelConnectionTestModal from './ModelConnectionTestModal'
import type {
  ModelRead,
  ModelCategoryKey,
  ProviderRead,
  ProviderSupportedRead,
} from '../../../services/generated'
import {
  MODEL_CATEGORIES,
  TABLE_ACTION_BTN_EDIT_CLASS,
  TABLE_ACTION_BTN_MORE_CLASS,
  TABLE_ACTION_BTN_TEST_CLASS,
  categoryLabelMap,
  categoryColorMap,
  SORT_OPTIONS,
  initialManagementView,
} from './constants'

/** Browse model configuration while keeping generation qualification explicitly unverified. */
export default function ModelsTab() {
  const [searchParams] = useSearchParams()
  const requestedModel = searchParams.get('connectionModel')
  const requestedRun = searchParams.get('connectionRun')
  const [providers, setProviders] = useState<ProviderRead[]>([])
  const [supportedProviders, setSupportedProviders] = useState<ProviderSupportedRead[]>([])
  const [models, setModels] = useState<ModelRead[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState<'updated' | 'name' | 'category'>('updated')
  const [viewMode, setViewMode] = useState<'table' | 'card'>(initialManagementView)
  const [selectedModel, setSelectedModel] = useState<ModelRead | null>(null)
  const [detailPanelOpen, setDetailPanelOpen] = useState(false)
  const [treeCollapsed, setTreeCollapsed] = useState(false)
  const [categoryFilter, setCategoryFilter] = useState<ModelCategoryKey | null>(null)
  const [modelModalOpen, setModelModalOpen] = useState(false)
  const [modelEditing, setModelEditing] = useState<ModelRead | null>(null)
  const [testModel, setTestModel] = useState<ModelRead | null>(null)
  useEffect(() => {
    if (!requestedModel) return
    let active = true
    void LlmService.getModelApiV1LlmModelsModelIdGet({ modelId: requestedModel }).then(response => {
      if (!active) return
      if (!response.data || response.data.category !== 'text') { message.warning('此回跳入口仅查看文本模型连接测试，不会发起图片或视频生成'); return }
      setTestModel(response.data)
    }).catch(() => { if (active) message.warning('未找到回跳指定的模型；没有发起连接测试') })
    return () => { active = false }
  }, [requestedModel])
  const [providerOptionsLoading, setProviderOptionsLoading] = useState(true)
  const [form] = Form.useForm()
  const selectedFormCategory = Form.useWatch<ModelCategoryKey | undefined>('category', form)
  const { lg } = Grid.useBreakpoint()
  const isLargeScreen = lg ?? false

  const load = async () => {
    setLoading(true)
    try {
      const [provRes, modelsRes, supportedRes] = await Promise.all([
        LlmService.listProvidersApiV1LlmProvidersGet({ page: 1, pageSize: 100 }),
        LlmService.listModelsApiV1LlmModelsGet({
          q: search.trim() || undefined,
          order: sortBy === 'name' ? 'name' : sortBy === 'category' ? 'category' : 'updated_at',
          isDesc: true,
          page: 1,
          pageSize: 100,
        }),
        LlmService.listSupportedProvidersApiV1LlmProvidersSupportedGet({}),
      ])
      setProviders(provRes.data?.items ?? [])
      setModels(modelsRes.data?.items ?? [])
      setSupportedProviders(supportedRes.data ?? [])
    } catch {
      message.error('加载失败')
    } finally {
      setLoading(false)
      setProviderOptionsLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [search, sortBy])

  const modelList = useMemo(() => {
    let list = models
    if (categoryFilter) list = list.filter((m) => m.category === categoryFilter)
    return list
  }, [models, categoryFilter])

  const categoryCounts = useMemo(() => {
    const c: Record<string, number> = {}
    MODEL_CATEGORIES.forEach((cat) => {
      c[cat.key] = models.filter((m) => m.category === cat.key).length
    })
    return c
  }, [models])

  const treeData = useMemo(
    () =>
      MODEL_CATEGORIES.map((c) => ({
        key: c.key,
        title: `${c.label} (${categoryCounts[c.key] ?? 0})`,
        isLeaf: true,
      })),
    [categoryCounts]
  )

  const getProviderName = (id: string) => providers.find((p) => p.id === id)?.name ?? id

  const resolveProviderSpec = (providerName: string) =>
    supportedProviders.find(
      (spec) => spec.display_name === providerName || (spec.aliases?.length && spec.aliases.includes(providerName)),
    )

  const filteredProviderOptions = useMemo(() => {
    if (!selectedFormCategory) return providers.map((p) => ({ label: p.name, value: p.id }))
    return providers
      .filter((provider) => {
        const spec = resolveProviderSpec(provider.name)
        return !!spec?.supported_categories?.includes(selectedFormCategory)
      })
      .map((p) => ({ label: p.name, value: p.id }))
  }, [providers, selectedFormCategory, supportedProviders])

  const editingUnsupportedProviderOption = useMemo(() => {
    if (!modelEditing) return null
    const provider = providers.find((p) => p.id === modelEditing.provider_id)
    if (!provider) return null
    const alreadyInOptions = filteredProviderOptions.some((opt) => opt.value === provider.id)
    if (alreadyInOptions) return null
    return {
      label: `${provider.name}（历史/当前不支持所选类别）`,
      value: provider.id,
    }
  }, [filteredProviderOptions, modelEditing, providers])

  const providerSelectOptions = useMemo(
    () =>
      editingUnsupportedProviderOption
        ? [editingUnsupportedProviderOption, ...filteredProviderOptions]
        : filteredProviderOptions,
    [editingUnsupportedProviderOption, filteredProviderOptions],
  )
  const unsupportedProviderWarning = useMemo(() => {
    if (!editingUnsupportedProviderOption || !selectedFormCategory) return null
    const provider = providers.find((p) => p.id === editingUnsupportedProviderOption.value)
    if (!provider) return null
    const categoryLabel = categoryLabelMap[selectedFormCategory]
    return `当前模型绑定供应商「${provider.name}」不支持「${categoryLabel}」类别，保存前建议切换到支持该类别的供应商。`
  }, [editingUnsupportedProviderOption, providers, selectedFormCategory])

  useEffect(() => {
    if (!selectedFormCategory) return
    const providerId = form.getFieldValue('provider_id') as string | undefined
    if (!providerId) return
    const stillSupported = filteredProviderOptions.some((opt) => opt.value === providerId)
    const keepHistoricalEditingProvider = modelEditing?.provider_id === providerId
    if (!stillSupported && !keepHistoricalEditingProvider) {
      form.setFieldsValue({ provider_id: undefined })
      message.warning('当前供应商不支持所选模型类别，请重新选择')
    }
  }, [filteredProviderOptions, form, modelEditing, selectedFormCategory])

  const handleSaveModel = async () => {
    try {
      const values = await form.validateFields()
      let params: Record<string, unknown> = {}
      try {
        if (values.params && String(values.params).trim())
          params = JSON.parse(String(values.params))
        if (!params || typeof params !== 'object' || Array.isArray(params)) throw new Error('Object required')
      } catch {
        message.error('参数格式需为合法 JSON 对象，原参数尚未更改')
        return
      }
      if (values.category === 'image' && values.local_image_generation) {
        if (providers.find(provider => provider.id === values.provider_id)?.name.trim().toLowerCase() !== 'openai') {
          message.error('本机单张图片生成需要配置 OpenAI-compatible 供应商；当前供应商协议不受支持')
          return
        }
        params.asset_image_protocol = 'openai_b64_v1'
      } else delete params.asset_image_protocol
      if (values.category === 'text' && values.local_vision_understanding) params.understanding_vision_protocol = 'openai_chat_image_v1'
      else delete params.understanding_vision_protocol
      if (values.category === 'audio' && values.local_audio_transcription) params.understanding_asr_protocol = 'openai_verbose_segments_v1'
      else delete params.understanding_asr_protocol
      if (values.category === 'video' && values.local_native_video) {
        if (values.name !== 'doubao-seedance-2-5-260628') { message.error('原生视频当前支持的模型名称为 doubao-seedance-2-5-260628，请核对后保存'); return }
        params.native_video_protocol = 'seedance25_text_v1'
      } else delete params.native_video_protocol
      if (modelEditing) {
        await LlmService.updateModelApiV1LlmModelsModelIdPatch({
          modelId: modelEditing.id,
          requestBody: {
            name: values.name,
            category: values.category,
            provider_id: values.provider_id,
            description: values.description ?? null,
            params,
          },
        })
        message.success('模型已更新')
      } else {
        if (!values.provider_id) {
          message.warning('请先添加供应商后再添加模型')
          return
        }
        const modelId =
          typeof crypto !== 'undefined' && crypto.randomUUID
            ? crypto.randomUUID()
            : `model_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`
        await LlmService.createModelApiV1LlmModelsPost({
          requestBody: {
            id: modelId,
            name: values.name,
            category: values.category,
            provider_id: values.provider_id,
            description: values.description,
            params,
          },
        })
        message.success('模型已添加')
      }
      setModelModalOpen(false)
      setModelEditing(null)
      form.resetFields()
      void load()
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in e) return
      message.error('保存失败')
    }
  }

  const handleDeleteModel = (m: ModelRead) => {
    Modal.confirm({
      title: '删除模型',
      content: `确定删除「${m.name}」？`,
      okText: '删除',
      okType: 'danger',
      onOk: async () => {
        await LlmService.deleteModelApiV1LlmModelsModelIdDelete({ modelId: m.id })
        message.success('已删除')
        if (selectedModel?.id === m.id) setSelectedModel(null)
        void load()
      },
    })
  }

  const openModelModal = (m?: ModelRead) => {
    setModelEditing(m ?? null)
    if (m) {
      form.setFieldsValue({
        name: m.name,
        category: m.category,
        provider_id: m.provider_id,
        description: m.description,
        params: JSON.stringify(m.params ?? {}, null, 2),
        local_image_generation: m.params?.asset_image_protocol === 'openai_b64_v1',
        local_vision_understanding: m.params?.understanding_vision_protocol === 'openai_chat_image_v1',
        local_audio_transcription: m.params?.understanding_asr_protocol === 'openai_verbose_segments_v1',
        local_native_video: m.params?.native_video_protocol === 'seedance25_text_v1',
      })
    } else {
      form.resetFields()
      form.setFieldsValue({ category: 'text', local_image_generation: false })
    }
    setModelModalOpen(true)
  }

  /** 基于已有模型打开「添加模型」浮窗，预填字段，名称追加「-复制」。 */
  const openCopyModelModal = (source: ModelRead) => {
    setModelEditing(null)
    form.resetFields()
    form.setFieldsValue({
      name: `${source.name}-复制`,
      category: source.category,
      provider_id: source.provider_id,
      description: source.description ?? '',
      params: JSON.stringify(source.params ?? {}, null, 2),
      local_image_generation: source.params?.asset_image_protocol === 'openai_b64_v1',
      local_vision_understanding: source.params?.understanding_vision_protocol === 'openai_chat_image_v1',
      local_audio_transcription: source.params?.understanding_asr_protocol === 'openai_verbose_segments_v1',
      local_native_video: source.params?.native_video_protocol === 'seedance25_text_v1',
    })
    setModelModalOpen(true)
  }

  const modelColumns: TableColumnsType<ModelRead> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      fixed: 'left',
      ellipsis: true,
      render: (n) => <Space>{n}</Space>,
    },
    {
      title: '类别',
      dataIndex: 'category',
      key: 'category',
      width: 100,
      render: (c: ModelCategoryKey) => (
        <Tag className="library-category-tag" color={categoryColorMap[c]}>{categoryLabelMap[c]}</Tag>
      ),
    },
    {
      title: '关联供应商',
      dataIndex: 'provider_id',
      key: 'provider_id',
      width: 120,
      render: (id: string) => getProviderName(id),
    },
    {
      title: '参数',
      dataIndex: 'params',
      key: 'params',
      ellipsis: true,
      render: (p: Record<string, unknown>) => (
        <Tooltip title={JSON.stringify(p)}>
          <span>
            {p && Object.keys(p).length ? JSON.stringify(p).slice(0, 30) + '…' : '—'}
          </span>
        </Tooltip>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (d: string) => <Tooltip title={d}>{d || '—'}</Tooltip>,
    },
    {
      title: '创建人',
      dataIndex: 'created_by',
      key: 'created_by',
      width: 100,
      render: (c: string) => c || '—',
    },
    {
      title: '操作',
      key: 'action',
      width: 144,
      fixed: 'right',
      align: 'center',
      render: (_, record) => (
        <Space size={4} className="flex-nowrap justify-center">
          <Tooltip title="编辑">
            <Button
              type="text"
              size="small"
              className={TABLE_ACTION_BTN_EDIT_CLASS}
              aria-label="编辑模型"
              icon={<EditOutlined />}
              onClick={(e) => {
                e.stopPropagation()
                openModelModal(record)
              }}
            />
          </Tooltip>
          <Tooltip title={record.category === 'text' ? '查看历史或确认一次真实短文本连接测试' : '图片与视频生成资格需在对应生成流程验收'}>
            <span><Button
              type="text"
              size="small"
              disabled={record.category !== 'text'}
              aria-label={record.category === 'text' ? `连接测试 ${record.name}` : '模型生成资格尚未验证'}
              className={TABLE_ACTION_BTN_TEST_CLASS}
              icon={<ThunderboltOutlined />}
              onClick={event => { event.stopPropagation(); setTestModel(record) }}
            /></span>
          </Tooltip>
          <Dropdown
            menu={{
              items: [
                {
                  key: 'copy',
                  label: '复制',
                  icon: <CopyOutlined />,
                  onClick: ({ domEvent }) => {
                    domEvent.stopPropagation()
                    openCopyModelModal(record)
                  },
                },
                {
                  key: 'delete',
                  label: '删除',
                  danger: true,
                  icon: <DeleteOutlined />,
                  onClick: ({ domEvent }) => {
                    domEvent.stopPropagation()
                    handleDeleteModel(record)
                  },
                },
              ],
            }}
            trigger={['click']}
          >
            <Tooltip title="更多">
              <Button
                type="text"
                size="small"
                className={TABLE_ACTION_BTN_MORE_CLASS}
                aria-label="更多模型操作"
                icon={<MenuOutlined />}
                onClick={(e) => e.stopPropagation()}
              />
            </Tooltip>
          </Dropdown>
        </Space>
      ),
    },
  ]

  return (
    <>
      {/* Model records and defaults must not imply a successful provider test. */}
      <Alert type="info" showIcon message="模型配置与文本连接测试" description="文本模型可确认发送一次最多 8 tokens 的真实连接测试，并保留结果。连接成功只证明文本 HTTP 响应；模型质量、图片与视频生成资格需分别验收。" className="library-notice flex-shrink-0" />
      <div className="library-management-toolbar">
        <div className="flex items-center gap-2">
          <span className="library-secondary text-sm">共 {models.length} 个模型</span>
        </div>
        <Space wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openModelModal()}>
            添加模型
          </Button>
          <Input
            placeholder="搜索名称/类型"
            aria-label="搜索模型名称或类型"
            allowClear
            className="w-48"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Dropdown
            menu={{
              items: SORT_OPTIONS.map((o) => ({
                key: o.value,
                label: o.label,
                onClick: () => setSortBy(o.value as 'updated' | 'name' | 'category'),
              })),
            }}
          >
            <Button icon={<DownOutlined />}>
              排序：{SORT_OPTIONS.find((s) => s.value === sortBy)?.label}
            </Button>
          </Dropdown>
        </Space>
      </div>

      <Layout className="library-split flex-1 min-h-0 flex-row overflow-hidden">
        <div
          className="library-filter-panel"
          style={{ width: treeCollapsed ? 48 : 200 }}
        >
          {treeCollapsed ? (
            <Button
              type="text"
              icon={<RightOutlined />}
              aria-label="展开模型筛选"
              onClick={() => setTreeCollapsed(false)}
              className="w-full rounded-none"
            />
          ) : (
            <>
              <div className="flex items-center justify-between px-3 py-2 border-b library-rule">
                <span className="text-sm font-medium library-secondary">筛选</span>
                <Button
                  type="text"
                  size="small"
                  icon={<RightOutlined rotate={180} />}
                  aria-label="收起模型筛选"
                  onClick={() => setTreeCollapsed(true)}
                />
              </div>
              <Tree
                selectedKeys={categoryFilter ? [categoryFilter] : []}
                treeData={treeData}
                showLine
                blockNode
                onSelect={([key]) => setCategoryFilter(key ? (key as ModelCategoryKey) : null)}
                className="py-2"
              />
            </>
          )}
        </div>

        <div className="library-data-region">
          <div className="library-view-switch" role="group" aria-label="模型显示方式">
            <Button
              type={viewMode === 'table' ? 'primary' : 'default'}
              size="small"
              icon={<UnorderedListOutlined />}
              aria-label="列表视图"
              aria-pressed={viewMode === 'table'}
              onClick={() => setViewMode('table')}
            />
            <Button
              type={viewMode === 'card' ? 'primary' : 'default'}
              size="small"
              icon={<AppstoreOutlined />}
              aria-label="卡片视图"
              aria-pressed={viewMode === 'card'}
              onClick={() => setViewMode('card')}
            />
          </div>

          {modelList.length === 0 ? (
            <div className="library-empty">
              <Empty
                description={
                  models.length === 0 ? '暂无模型，请先添加供应商再添加模型' : '无匹配结果'
                }
              >
                {providers.length > 0 && models.length === 0 && (
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => openModelModal()}>
                    添加第一个模型
                  </Button>
                )}
              </Empty>
            </div>
          ) : viewMode === 'table' ? (
            <div className="library-table-wrap">
              <Table<ModelRead>
                rowKey="id"
                loading={loading}
                columns={modelColumns}
                dataSource={modelList}
                scroll={{ x: 1024 }}
                pagination={{ pageSize: 20 }}
                onRow={(record) => ({
                  tabIndex: 0,
                  'aria-label': `查看模型${record.name}详情`,
                  onClick: () => {
                    setSelectedModel(record)
                    setDetailPanelOpen(true)
                  },
                  onKeyDown: (event) => {
                    // Keep the existing pointer action available from the keyboard.
                    if (event.target === event.currentTarget && event.key === 'Enter') {
                      setSelectedModel(record)
                      setDetailPanelOpen(true)
                    }
                  },
                  style: { cursor: 'pointer' },
                })}
                size="small"
              />
            </div>
          ) : (
            <div className="library-card-grid">
              {modelList.map((m) => (
                <Card
                  key={m.id}
                  hoverable
                  className="library-record-card cursor-pointer"
                  role="button"
                  tabIndex={0}
                  aria-label={`查看模型${m.name}详情`}
                  style={{ minHeight: 220 }}
                  onClick={() => {
                    setSelectedModel(m)
                    setDetailPanelOpen(true)
                  }}
                  onKeyDown={(event) => {
                    // Keep the existing pointer action available from the keyboard.
                    if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
                      event.preventDefault()
                      setSelectedModel(m)
                      setDetailPanelOpen(true)
                    }
                  }}
                  actions={[
                    <Button
                      key="edit"
                      type="text"
                      size="small"
                      icon={<EditOutlined />}
                      onClick={(e) => {
                        e.stopPropagation()
                        openModelModal(m)
                      }}
                    >
                      编辑
                    </Button>,
                    <Button key="test" type="text" size="small" disabled={m.category !== 'text'} icon={<ThunderboltOutlined />} title={m.category === 'text' ? '查看历史或发送一次短文本连接测试' : '图片与视频生成资格需分别验收'} onClick={event => { event.stopPropagation(); setTestModel(m) }}>
                      {m.category === 'text' ? '连接测试' : '生成未验证'}
                    </Button>,
                    <Dropdown
                      key="more"
                      menu={{
                        items: [
                          {
                            key: 'copy',
                            label: '复制',
                            icon: <CopyOutlined />,
                            onClick: ({ domEvent }) => {
                              domEvent.stopPropagation()
                              openCopyModelModal(m)
                            },
                          },
                          {
                            key: 'delete',
                            label: '删除',
                            danger: true,
                            icon: <DeleteOutlined />,
                            onClick: ({ domEvent }) => {
                              domEvent.stopPropagation()
                              handleDeleteModel(m)
                            },
                          },
                        ],
                      }}
                      trigger={['click']}
                    >
                      <Button type="text" size="small" onClick={(e) => e.stopPropagation()}>
                        更多
                      </Button>
                    </Dropdown>,
                  ]}
                >
                  <div className="flex items-center gap-2 mb-2">
                    <Tag className="library-category-tag" color={categoryColorMap[m.category]}>{categoryLabelMap[m.category]}</Tag>
                  </div>
                  <div className="font-medium mb-1">{m.name}</div>
                  <div className="library-muted text-sm mb-1">
                    供应商：{getProviderName(m.provider_id)}
                  </div>
                  <div className="library-muted text-sm line-clamp-2 mb-2">{m.description || '—'}</div>
                  {m.created_by && (
                    <span className="text-xs library-muted">创建：{m.created_by}</span>
                  )}
                </Card>
              ))}
            </div>
          )}
        </div>

        {selectedModel && isLargeScreen && (
          <div
            className="library-detail-panel"
            style={{ width: '36%', minWidth: 320 }}
          >
            <div className="p-4 border-b library-rule flex items-center justify-between">
              <span className="font-medium">详情</span>
              <Button
                type="link"
                size="small"
                onClick={() => {
                  setDetailPanelOpen(false)
                  setSelectedModel(null)
                }}
              >
                收起
              </Button>
            </div>
            <div className="p-4 space-y-4">
              <div>
                <div className="text-sm library-muted mb-1">名称</div>
                <div className="font-medium">{selectedModel.name}</div>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">类别</div>
                <Tag className="library-category-tag" color={categoryColorMap[selectedModel.category]}>
                  {categoryLabelMap[selectedModel.category]}
                </Tag>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">关联供应商</div>
                <div>{getProviderName(selectedModel.provider_id)}</div>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">描述</div>
                <div className="library-secondary text-sm">{selectedModel.description || '—'}</div>
              </div>
              <Space>
                <Button
                  type="primary"
                  icon={<EditOutlined />}
                  onClick={() => openModelModal(selectedModel)}
                >
                  编辑
                </Button>
                <Button disabled={selectedModel.category !== 'text'} icon={<ThunderboltOutlined />} onClick={() => setTestModel(selectedModel)} title={selectedModel.category !== 'text' ? '图片与视频生成资格需分别验收' : undefined}>{selectedModel.category === 'text' ? '连接测试' : '生成资格未验证'}</Button>
              </Space>
            </div>
          </div>
        )}

        {selectedModel && !isLargeScreen && (
          <Drawer
            className="library-dialog"
            title="详情"
            placement="right"
            open={detailPanelOpen}
            onClose={() => setDetailPanelOpen(false)}
            width="min(100%, 400px)"
          >
            <div className="space-y-4">
              <div>
                <div className="text-sm library-muted mb-1">名称</div>
                <div className="font-medium">{selectedModel.name}</div>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">类别</div>
                <Tag className="library-category-tag" color={categoryColorMap[selectedModel.category]}>
                  {categoryLabelMap[selectedModel.category]}
                </Tag>
              </div>
              <Space>
                <Button
                  type="primary"
                  icon={<EditOutlined />}
                  onClick={() => openModelModal(selectedModel)}
                >
                  编辑
                </Button>
                <Button disabled={selectedModel.category !== 'text'} icon={<ThunderboltOutlined />} onClick={() => setTestModel(selectedModel)} title={selectedModel.category !== 'text' ? '图片与视频生成资格需分别验收' : undefined}>{selectedModel.category === 'text' ? '连接测试' : '生成资格未验证'}</Button>
              </Space>
            </div>
          </Drawer>
        )}
      </Layout>

      <ModelConnectionTestModal key={testModel?.id ?? 'no-test'} model={testModel} requestedRun={testModel?.id === requestedModel ? requestedRun : undefined} onClose={() => setTestModel(null)} />
      <Modal
        className="library-dialog"
        title={modelEditing ? '编辑模型' : '添加模型'}
        open={modelModalOpen}
        onCancel={() => {
          setModelModalOpen(false)
          setModelEditing(null)
          form.resetFields()
        }}
        onOk={() => void handleSaveModel()}
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="pt-2">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="例如：GPT-4" />
          </Form.Item>
          <Form.Item name="category" label="类别" rules={[{ required: true }]}>
            <Select options={MODEL_CATEGORIES.map((c) => ({ label: c.label, value: c.key }))} />
          </Form.Item>
          <Form.Item
            name="provider_id"
            label="关联供应商"
            rules={[{ required: true, message: '请选择供应商' }]}
          >
            <Select
              loading={providerOptionsLoading}
              placeholder={
                selectedFormCategory
                  ? `选择支持${categoryLabelMap[selectedFormCategory]}的供应商`
                  : '选择供应商（请先添加供应商）'
              }
              options={providerSelectOptions}
              notFoundContent={
                providerOptionsLoading
                  ? '加载中…'
                  : selectedFormCategory
                    ? '暂无支持该类别的供应商'
                    : '暂无供应商'
              }
            />
          </Form.Item>
          {unsupportedProviderWarning && (
            <Alert
              type="warning"
              showIcon
              className="mb-4"
              message={unsupportedProviderWarning}
            />
          )}
          {selectedFormCategory === 'image' && <><Form.Item name="local_image_generation" label="用于本机单张图片生成" valuePropName="checked"><Switch aria-label="用于本机单张图片生成" /></Form.Item><Alert className="mb-4" type="info" showIcon message="单张文本生图，返回图片内容" description="仅兼容支持此协议的 OpenAI-compatible 图片供应商。不接受只返回图片网址的响应，不发送人物参考图；打开开关与保存配置不会测试或调用模型。" /></>}
          {selectedFormCategory === 'text' && <><Form.Item name="local_vision_understanding" label="用于原片画面理解" valuePropName="checked"><Switch aria-label="用于原片画面理解" /></Form.Item><Alert className="mb-4" type="info" showIcon message="读取原帧图片，形成拆解候选" description="需要模型接受图片输入并返回结构化内容。保存配置不会调用模型；实际识别能力与候选内容仍需核对。" /></>}
          {selectedFormCategory === 'audio' && <><Form.Item name="local_audio_transcription" label="用于原片语音转写" valuePropName="checked"><Switch aria-label="用于原片语音转写" /></Form.Item><Alert className="mb-4" type="info" showIcon message="语音转写，需要逐段时间" description="只用于返回带逐段时标的语音转写候选。保存不会调用模型；转写结果须人工核对，声音生成另行配置。" /></>}
          {selectedFormCategory === 'video' && <><Form.Item name="local_native_video" label="用于原生视频生成" valuePropName="checked"><Switch aria-label="用于原生视频生成" /></Form.Item><Alert className="mb-4" type="info" showIcon message="Seedance 2.5 文字生成视频" description="当前支持火山 Ark 的 doubao-seedance-2-5-260628，4–30整数秒、竖屏480p、无生成音频并保留水印。需另行登记项目费用与执行服务；保存不会生成视频。参考图片、首尾帧和模型质量仍需单独验收。" /></>}
          <Form.Item name="params" label="参数（JSON）">
            <Input.TextArea rows={3} placeholder='{"max_tokens": 4096, "temperature": 0.7}' />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
