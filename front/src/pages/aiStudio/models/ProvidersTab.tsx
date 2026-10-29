import { useEffect, useState, useMemo } from 'react'
import {
  Alert,
  Layout,
  Input,
  Button,
  Table,
  Tag,
  Space,
  Card,
  Dropdown,
  Drawer,
  Modal,
  Form,
  Select,
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
  ExportOutlined,
  MenuOutlined,
  AppstoreOutlined,
  UnorderedListOutlined,
  DownOutlined,
  RightOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { LlmService } from '../../../services/generated/services/LlmService'
import type { ProviderRead, ProviderStatus, ProviderSupportedRead } from '../../../services/generated'
import {
  PROVIDER_STATUS_MAP,
  SORT_OPTIONS,
  TABLE_ACTION_BTN_EDIT_CLASS,
  TABLE_ACTION_BTN_MORE_CLASS,
  TABLE_ACTION_BTN_TEST_CLASS,
  maskUrl,
  initialManagementView,
} from './constants'

/** Maintain provider configuration without suggesting that stored credentials have been verified. */
export default function ProvidersTab() {
  const [providers, setProviders] = useState<ProviderRead[]>([])
  const [supportedSpecs, setSupportedSpecs] = useState<ProviderSupportedRead[]>([])
  const [supportedLoading, setSupportedLoading] = useState(true)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState<'updated' | 'name'>('updated')
  const [viewMode, setViewMode] = useState<'table' | 'card'>(initialManagementView)
  const [selectedProvider, setSelectedProvider] = useState<ProviderRead | null>(null)
  const [detailPanelOpen, setDetailPanelOpen] = useState(false)
  const [treeCollapsed, setTreeCollapsed] = useState(false)
  const [providerModalOpen, setProviderModalOpen] = useState(false)
  const [providerEditing, setProviderEditing] = useState<ProviderRead | null>(null)
  const [form] = Form.useForm()
  const { lg } = Grid.useBreakpoint()
  const isLargeScreen = lg ?? false

  const load = async () => {
    setLoading(true)
    try {
      const order = sortBy === 'name' ? 'name' : 'updated_at'
      const res = await LlmService.listProvidersApiV1LlmProvidersGet({
        q: search.trim() || undefined,
        order,
        isDesc: true,
        page: 1,
        pageSize: 100,
      })
      setProviders(res.data?.items ?? [])
    } catch {
      message.error('加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [search, sortBy])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setSupportedLoading(true)
      try {
        const res = await LlmService.listSupportedProvidersApiV1LlmProvidersSupportedGet({})
        if (!cancelled) setSupportedSpecs(res.data ?? [])
      } catch {
        if (!cancelled) {
          message.error('加载系统支持的供应商列表失败')
          setSupportedSpecs([])
        }
      } finally {
        if (!cancelled) setSupportedLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const providerNameOptions = useMemo(() => {
    const fromApi = supportedSpecs.map((s) => ({
      label: s.is_experimental ? `${s.display_name}（实验）` : s.display_name,
      value: s.display_name,
    }))
    const name = providerEditing?.name?.trim()
    if (!name) return fromApi
    const known = supportedSpecs.some(
      (s) => s.display_name === name || (s.aliases?.length && s.aliases.includes(name)),
    )
    if (known) return fromApi
    return [{ label: `${name}（历史/未在清单内）`, value: name }, ...fromApi]
  }, [supportedSpecs, providerEditing])

  const applyDefaultBaseUrlForDisplayName = (displayName: string) => {
    const spec = supportedSpecs.find(
      (s) => s.display_name === displayName || (s.aliases?.length && s.aliases.includes(displayName)),
    )
    const def = spec?.default_base_url?.trim()
    if (!def) return
    const current = (form.getFieldValue('base_url') as string | undefined)?.trim()
    if (!providerEditing || !current) {
      form.setFieldsValue({ base_url: def })
    }
  }

  const providerList = useMemo(() => {
    let list = providers
    if (sortBy === 'name') list = [...list].sort((a, b) => a.name.localeCompare(b.name))
    return list
  }, [providers, sortBy])

  const handleSaveProvider = async () => {
    try {
      const values = await form.validateFields()
      if (providerEditing) {
        const requestBody: Parameters<typeof LlmService.updateProviderApiV1LlmProvidersProviderIdPatch>[0]['requestBody'] = {
          name: values.name,
          base_url: values.base_url,
          image_base_url: values.image_base_url ?? null,
          video_base_url: values.video_base_url ?? null,
          description: values.description ?? null,
          status: values.status ?? null,
        }
        if (values.api_key && values.api_key !== '********') requestBody.api_key = values.api_key
        if (values.api_secret && values.api_secret !== '********') requestBody.api_secret = values.api_secret
        await LlmService.updateProviderApiV1LlmProvidersProviderIdPatch({
          providerId: providerEditing.id,
          requestBody,
        })
        message.success('供应商已更新')
      } else {
        const id =
          typeof crypto !== 'undefined' && crypto.randomUUID
            ? crypto.randomUUID()
            : `prov_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`
        await LlmService.createProviderApiV1LlmProvidersPost({
          requestBody: {
            id,
            name: values.name,
            base_url: values.base_url,
            image_base_url: values.image_base_url ?? null,
            video_base_url: values.video_base_url ?? null,
            description: values.description,
            status: values.status,
            api_key: values.api_key,
            api_secret: values.api_secret,
          },
        })
        message.success('供应商已添加')
      }
      setProviderModalOpen(false)
      setProviderEditing(null)
      form.resetFields()
      void load()
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in e) return
      message.error('保存失败')
    }
  }

  const handleDeleteProvider = async (p: ProviderRead) => {
    let linked = 0
    try {
      const countRes = await LlmService.listModelsApiV1LlmModelsGet({
        providerId: p.id,
        pageSize: 1,
      })
      linked = countRes.data?.pagination?.total ?? 0
    } catch {
      // ignore
    }
    Modal.confirm({
      title: '删除供应商',
      content:
        linked > 0
          ? `该供应商下还有 ${linked} 个模型，删除后关联模型将失效。确定删除？`
          : '确定删除该供应商？',
      okText: '删除',
      okType: 'danger',
      onOk: async () => {
        await LlmService.deleteProviderApiV1LlmProvidersProviderIdDelete({ providerId: p.id })
        message.success('已删除')
        if (selectedProvider?.id === p.id) setSelectedProvider(null)
        void load()
      },
    })
  }

  const openProviderModal = (p?: ProviderRead) => {
    setProviderEditing(p ?? null)
    if (p) {
      form.setFieldsValue({
        name: p.name,
        base_url: p.base_url,
        image_base_url: p.image_base_url ?? null,
        video_base_url: p.video_base_url ?? null,
        api_key: '********',
        api_secret: '********',
        description: p.description,
        status: p.status ?? 'active',
      })
    } else {
      form.resetFields()
    }
    setProviderModalOpen(true)
  }

  const providerColumns: TableColumnsType<ProviderRead> = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 160, fixed: 'left', ellipsis: true, render: (n) => <Space>{n}</Space> },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      key: 'base_url',
      ellipsis: true,
      render: (url: string) => <Tooltip title={url}>{maskUrl(url)}</Tooltip>,
    },
    {
      title: 'AK/SK',
      key: 'aksk',
      render: () => (
        <span>
          <WarningOutlined className="text-amber-500 mr-1" />
          ******** / ********
        </span>
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
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (s: ProviderStatus) => (
        <Tag color={PROVIDER_STATUS_MAP[s]?.color}>{PROVIDER_STATUS_MAP[s]?.text}</Tag>
      ),
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
              aria-label="编辑供应商"
              icon={<EditOutlined />}
              onClick={(e) => {
                e.stopPropagation()
                openProviderModal(record)
              }}
            />
          </Tooltip>
          <Tooltip title="连接测试尚未接入，此配置尚未验证">
            <span><Button
              type="text"
              size="small"
              disabled
              aria-label="供应商连接尚未验证"
              className={TABLE_ACTION_BTN_TEST_CLASS}
              icon={<ThunderboltOutlined />}
            /></span>
          </Tooltip>
          <Dropdown
            menu={{
              items: [
                { key: 'copy', label: '复制', icon: <CopyOutlined /> },
                { key: 'export', label: '导出配置', icon: <ExportOutlined /> },
                { type: 'divider' },
                {
                  key: 'delete',
                  label: '删除',
                  danger: true,
                  icon: <DeleteOutlined />,
                  onClick: ({ domEvent }) => {
                    domEvent.stopPropagation()
                    handleDeleteProvider(record)
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
                aria-label="更多供应商操作"
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
      {/* Stored configuration is not evidence of authentication or model qualification. */}
      <Alert type="info" showIcon message="供应商连接尚未验证" description="本页仅维护配置，连接测试尚未接入。保存配置或修改状态不代表认证成功，也不代表模型具备生成资格。" className="library-notice flex-shrink-0" />
      <div className="library-management-toolbar">
        <div className="flex items-center gap-2">
          <span className="library-secondary text-sm">共 {providers.length} 个供应商</span>
        </div>
        <Space wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openProviderModal()}>
            添加供应商
          </Button>
          <Input
            placeholder="搜索名称/描述"
            aria-label="搜索供应商名称或描述"
            allowClear
            className="w-48"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Dropdown
            menu={{
              items: SORT_OPTIONS.filter((o) => o.value !== 'category').map((o) => ({
                key: o.value,
                label: o.label,
                onClick: () => setSortBy(o.value as 'updated' | 'name'),
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
              aria-label="展开筛选面板"
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
                  aria-label="收起筛选面板"
                  onClick={() => setTreeCollapsed(true)}
                />
              </div>
              <div className="p-3 text-sm library-muted">点击列表项查看详情</div>
            </>
          )}
        </div>

        <div className="library-data-region">
          <div className="library-view-switch" role="group" aria-label="供应商显示方式">
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

          {providerList.length === 0 ? (
            <div className="library-empty">
              <Empty
                description={
                  providers.length === 0 ? '暂无供应商，点击「添加供应商」开始' : '无匹配结果'
                }
              >
                {providers.length === 0 && (
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => openProviderModal()}>
                    添加第一个供应商
                  </Button>
                )}
              </Empty>
            </div>
          ) : viewMode === 'table' ? (
            <div className="library-table-wrap">
              <Table<ProviderRead>
                rowKey="id"
                loading={loading}
                columns={providerColumns}
                dataSource={providerList}
                scroll={{ x: 1180 }}
                pagination={{ pageSize: 20 }}
                onRow={(record) => ({
                  tabIndex: 0,
                  'aria-label': `查看供应商${record.name}详情`,
                  onClick: () => {
                    setSelectedProvider(record)
                    setDetailPanelOpen(true)
                  },
                  onKeyDown: (event) => {
                    // Keep the existing pointer action available from the keyboard.
                    if (event.target === event.currentTarget && event.key === 'Enter') {
                      setSelectedProvider(record)
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
              {providerList.map((p) => (
                <Card
                  key={p.id}
                  hoverable
                  className="library-record-card cursor-pointer"
                  role="button"
                  tabIndex={0}
                  aria-label={`查看供应商${p.name}详情`}
                  style={{ minHeight: 220 }}
                  onClick={() => {
                    setSelectedProvider(p)
                    setDetailPanelOpen(true)
                  }}
                  onKeyDown={(event) => {
                    // Keep the existing pointer action available from the keyboard.
                    if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
                      event.preventDefault()
                      setSelectedProvider(p)
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
                        openProviderModal(p)
                      }}
                    >
                      编辑
                    </Button>,
                    <Button
                      key="test"
                      type="text"
                      size="small"
                      disabled
                      icon={<ThunderboltOutlined />}
                      title="连接测试尚未接入，此配置尚未验证"
                    >
                      未验证
                    </Button>,
                    <Dropdown
                      key="more"
                      menu={{
                        items: [
                          {
                            key: 'delete',
                            label: '删除',
                            danger: true,
                            onClick: () => handleDeleteProvider(p),
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
                  <div className="font-medium mb-1">{p.name}</div>
                  <div className="library-muted text-sm mb-1 truncate" title={p.base_url}>
                    Base URL：{maskUrl(p.base_url)}
                  </div>
                  <div className="library-muted text-sm mb-1">AK/SK：******** / ********</div>
                  <div className="library-muted text-sm line-clamp-2 mb-2">{p.description || '—'}</div>
                  <Tag color={PROVIDER_STATUS_MAP[p.status ?? 'active']?.color}>
                    {PROVIDER_STATUS_MAP[p.status ?? 'active']?.text}
                  </Tag>
                  {p.created_by && (
                    <span className="text-xs library-muted ml-2">创建：{p.created_by}</span>
                  )}
                </Card>
              ))}
            </div>
          )}
        </div>

        {selectedProvider && isLargeScreen && (
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
                  setSelectedProvider(null)
                }}
              >
                收起
              </Button>
            </div>
            <div className="p-4 space-y-4">
              <div>
                <div className="text-sm library-muted mb-1">名称</div>
                <div className="font-medium">{selectedProvider.name}</div>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">文本/通用 Base URL</div>
                <Tooltip title={selectedProvider.base_url}>
                  <span className="text-sm">{maskUrl(selectedProvider.base_url)}</span>
                </Tooltip>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">图片 Base URL（可选覆盖）</div>
                <span className="text-sm">{selectedProvider.image_base_url ? maskUrl(selectedProvider.image_base_url) : '回退到文本/通用'}</span>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">视频 Base URL（可选覆盖）</div>
                <span className="text-sm">{selectedProvider.video_base_url ? maskUrl(selectedProvider.video_base_url) : '回退到文本/通用'}</span>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">描述</div>
                <div className="library-secondary text-sm">{selectedProvider.description || '—'}</div>
              </div>
              <Space>
                <Button
                  type="primary"
                  icon={<EditOutlined />}
                  onClick={() => openProviderModal(selectedProvider)}
                >
                  编辑
                </Button>
                <Button
                  icon={<ThunderboltOutlined />}
                  disabled
                  title="连接测试尚未接入，此配置尚未验证"
                >
                  连接尚未验证
                </Button>
              </Space>
            </div>
          </div>
        )}

        {selectedProvider && !isLargeScreen && (
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
                <div className="font-medium">{selectedProvider.name}</div>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">文本/通用 Base URL</div>
                <span className="text-sm">{maskUrl(selectedProvider.base_url)}</span>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">图片 Base URL（可选覆盖）</div>
                <span className="text-sm">{selectedProvider.image_base_url ? maskUrl(selectedProvider.image_base_url) : '回退到文本/通用'}</span>
              </div>
              <div>
                <div className="text-sm library-muted mb-1">视频 Base URL（可选覆盖）</div>
                <span className="text-sm">{selectedProvider.video_base_url ? maskUrl(selectedProvider.video_base_url) : '回退到文本/通用'}</span>
              </div>
              <Space>
                <Button
                  type="primary"
                  icon={<EditOutlined />}
                  onClick={() => openProviderModal(selectedProvider)}
                >
                  编辑
                </Button>
                <Button
                  icon={<ThunderboltOutlined />}
                  disabled
                  title="连接测试尚未接入，此配置尚未验证"
                >
                  连接尚未验证
                </Button>
              </Space>
            </div>
          </Drawer>
        )}
      </Layout>

      <Modal
        className="library-dialog"
        title={providerEditing ? '编辑供应商' : '添加供应商'}
        open={providerModalOpen}
        onCancel={() => {
          setProviderModalOpen(false)
          setProviderEditing(null)
          form.resetFields()
        }}
        onOk={() => void handleSaveProvider()}
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="pt-2">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请选择供应商' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              loading={supportedLoading}
              placeholder={supportedLoading ? '加载供应商清单…' : '选择供应商'}
              options={providerNameOptions}
              notFoundContent={supportedLoading ? '加载中…' : '暂无数据'}
              onChange={(v) => applyDefaultBaseUrlForDisplayName(String(v))}
            />
          </Form.Item>
          <Form.Item
            name="base_url"
            label="文本/通用 Base URL"
            rules={[{ required: true }, { type: 'url', message: '请输入有效 URL' }]}
          >
            <Input placeholder="https://api.openai.com/v1" />
          </Form.Item>
          <Form.Item name="image_base_url" label="图片 Base URL（可选覆盖）" rules={[{ type: 'url', message: '请输入有效 URL' }]}>
            <Input placeholder="留空则回退到文本/通用 Base URL" />
          </Form.Item>
          <Form.Item name="video_base_url" label="视频 Base URL（可选覆盖）" rules={[{ type: 'url', message: '请输入有效 URL' }]}>
            <Input placeholder="留空则回退到文本/通用 Base URL" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key" help={providerEditing ? '留空则不修改' : '请勿分享密钥'}>
            <Input.Password placeholder="AK" />
          </Form.Item>
          <Form.Item name="api_secret" label="API Secret" help={providerEditing ? '留空则不修改' : undefined}>
            <Input.Password placeholder="SK" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="支持 GPT 系列模型" />
          </Form.Item>
          <Form.Item name="status" label="状态" initialValue="active">
            <Select
              options={[
                { label: '活跃', value: 'active' },
                { label: '测试中', value: 'testing' },
                { label: '禁用', value: 'disabled' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
