import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Empty, Form, Input, List, Modal, Popconfirm, Select, Space, Table, Tag, message } from 'antd'
import { useParams } from 'react-router-dom'
import { OpenAPI, ProductionService as P, StudioChaptersService, StudioShotsService } from '../../../../../services/generated'
import type { AssetWrite, TakeWrite, VersionWrite } from '../../../../../services/generated'
import { ExecutionJobsPanel } from './ExecutionJobsPanel'

type Version = { id: string; revision: number; payload: { description: string; media_ids: string[] }; content_hash: string }
type Asset = { asset: { id: string; name: string; kind: string; archived: boolean; entity_id: string | null }; versions: Version[]; affected_shot_ids: string[] }
type Media = { id: string; name: string; mime: string; size: number }
type Take = { id: string; shot_id: string; media_id: string; provenance: string; selected: boolean; versions: Version[] }
const kinds = [{ value: 'character', label: '角色' }, { value: 'appearance', label: '角色服装' }, { value: 'scene', label: '场景' }, { value: 'prop', label: '道具' }, { value: 'voice', label: '角色声线' }]

/** Real project library: versions and imported takes stay distinct from model generation. */
export function ProductionTab() {
  const { projectId = '' } = useParams()
  const [assets, setAssets] = useState<Asset[]>([])
  const [media, setMedia] = useState<Media[]>([])
  const [takes, setTakes] = useState<Take[]>([])
  const [shots, setShots] = useState<{ value: string; label: string }[]>([])
  const [selected, setSelected] = useState<Asset | null>(null)
  const [mode, setMode] = useState<'create' | 'revise' | 'take' | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [form] = Form.useForm()
  const kind = Form.useWatch('kind', form)

  /** Refresh all visible data from generated clients; a failure remains visible. */
  const load = useCallback(async () => {
    setLoading(true); setLoadError('')
    try {
      const [a, m, t, c] = await Promise.all([
        P.listAssetsApiV1StudioProductionProjectsProjectIdAssetsGet({ projectId }),
        P.listMediaApiV1StudioProductionProjectsProjectIdMediaGet({ projectId }),
        P.listTakesApiV1StudioProductionProjectsProjectIdTakesGet({ projectId }),
        StudioChaptersService.listChaptersApiV1StudioChaptersGet({ projectId, pageSize: 100 }),
      ])
      const next = (a.data ?? []) as Asset[]
      setAssets(next); setMedia((m.data ?? []) as Media[]); setTakes((t.data ?? []) as Take[])
      setSelected(old => next.find(item => item.asset.id === old?.asset.id) ?? next[0] ?? null)
      const lists = await Promise.all((c.data?.items ?? []).map(ch => StudioShotsService.listShotsApiV1StudioShotsGet({ chapterId: ch.id, pageSize: 100 })))
      setShots(lists.flatMap(list => (list.data?.items ?? []).map(s => ({ value: s.id, label: s.title }))))
    } catch { setLoadError('读取工程资产失败，请重试。历史记录未被修改。') }
    finally { setLoading(false) }
  }, [projectId])
  useEffect(() => { setSelected(null); void load() }, [load])

  /** Run one mutation at a time and surface server validation messages. */
  async function mutate(action: () => Promise<unknown>) {
    setBusy(true)
    try { await action(); setMode(null); form.resetFields(); await load(); message.success('已保存') }
    catch (e) { const error = e as { body?: { message?: string } }; message.error(error.body?.message ?? '保存失败，请刷新后重试') }
    finally { setBusy(false) }
  }

  /** Publish an append-only revision or record an explicitly imported candidate. */
  function submit(values: AssetWrite & TakeWrite & VersionWrite) {
    if (mode === 'create') void mutate(() => P.postAssetApiV1StudioProductionProjectsProjectIdAssetsPost({ projectId, requestBody: { ...values, expected_revision: 0, media_ids: values.media_ids ?? [] } }))
    if (mode === 'revise' && selected) void mutate(() => P.postVersionApiV1StudioProductionProjectsProjectIdAssetsAssetIdVersionsPost({ projectId, assetId: selected.asset.id, requestBody: { description: values.description ?? '', media_ids: values.media_ids ?? [], expected_revision: selected.versions[0].revision } }))
    if (mode === 'take') void mutate(() => P.postTakeApiV1StudioProductionProjectsProjectIdTakesPost({ projectId, requestBody: values }))
  }

  /** Upload media or a validated bundle; no upload initiates model generation. */
  function upload(file: File | undefined, bundle: boolean) {
    if (!file) return
    void mutate(() => bundle
      // The generator types multipart binary as string; retain the actual File object for FormData.
      ? P.postBundleApiV1StudioProductionProjectsProjectIdMediaBundlePost({ projectId, formData: { file: file as unknown as string } })
      : P.postMediaApiV1StudioProductionProjectsProjectIdMediaPost({ projectId, formData: { file: file as unknown as string } }))
  }

  const mediaOptions = media.map(m => ({ value: m.id, label: m.name }))
  return <div className="space-y-4 pb-6">
    <ExecutionJobsPanel key={projectId} projectId={projectId} />
    <Alert type="info" showIcon message="工程资产与版本" description="修订会新增版本，已采用片段保留原版本。这里导入的文件属于用户素材，不计作模型生成或成片验收。" />
    {loadError && <Alert type="error" message={loadError} action={<Button onClick={() => void load()}>重试</Button>} />}
    <Card title="资产库" loading={loading} extra={<Button type="primary" onClick={() => { form.resetFields(); setMode('create') }}>新建版本资产</Button>}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 20 }}>
        <List dataSource={assets} locale={{ emptyText: <Empty description="尚未创建版本资产" /> }} renderItem={item => <List.Item>
          <Button type={selected?.asset.id === item.asset.id ? 'primary' : 'text'} onClick={() => setSelected(item)}>{item.asset.name}</Button>
          <Space><Tag>{kinds.find(k => k.value === item.asset.kind)?.label}</Tag><Tag>{item.asset.archived ? '已归档' : `v${item.versions[0]?.revision}`}</Tag></Space>
        </List.Item>} />
        <div>{selected ? <>
          <h3 className="text-base font-semibold mb-3">{selected.asset.name} · 版本历史</h3>
          <Space wrap className="mb-3">
            <Button disabled={selected.asset.archived} onClick={() => { form.resetFields(); form.setFieldsValue(selected.versions[0]?.payload); setMode('revise') }}>修订为新版本</Button>
            <Popconfirm title={selected.asset.archived ? '恢复此资产？' : '归档此资产？'} description={`关联 ${selected.affected_shot_ids.length} 个镜头，历史采用记录会保留。`} onConfirm={() => mutate(() => P.archiveAssetApiV1StudioProductionProjectsProjectIdAssetsAssetIdArchivePatch({ projectId, assetId: selected.asset.id, requestBody: { archived: !selected.asset.archived } }))}>
              <Button disabled={busy}>{selected.asset.archived ? '恢复资产' : '归档资产'}</Button>
            </Popconfirm>
          </Space>
          <p>受影响镜头：{selected.affected_shot_ids.length ? selected.affected_shot_ids.join('、') : '暂无采用'}</p>
          <Table size="small" rowKey="id" pagination={false} dataSource={selected.versions} columns={[{ title: '版本', dataIndex: 'revision', render: value => `v${value}` }, { title: '描述', render: (_, v) => <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{v.payload.description}</span> }, { title: '参考素材', render: (_, v) => v.payload.media_ids.length }]} />
        </> : <Empty description="选择资产查看历史" />}</div>
      </div>
    </Card>
    <Card title="本工程媒体">
      <Space wrap className="mb-3"><label>导入媒体 <input aria-label="导入媒体" type="file" accept=".png,.jpg,.jpeg,.webp,.mp4,.wav" disabled={busy} onChange={e => { upload(e.target.files?.[0], false); e.target.value = '' }} /></label><label>导入媒体包 <input aria-label="导入媒体包" type="file" accept=".zip" disabled={busy} onChange={e => { upload(e.target.files?.[0], true); e.target.value = '' }} /></label></Space>
      <p className="text-gray-500 mb-3">媒体包只导入清单内的素材。文件上限 64 MB；不接受脚本、链接或未经声明的归档内容。</p>
      <List dataSource={media} locale={{ emptyText: '暂无媒体' }} renderItem={m => <List.Item><a href={`${OpenAPI.BASE}/api/v1/studio/production/projects/${encodeURIComponent(projectId)}/media/${encodeURIComponent(m.id)}/content`} target="_blank" rel="noreferrer">{m.name}</a><span>{Math.ceil(m.size / 1024)} KB · 用户导入</span></List.Item>} />
    </Card>
    <Card title="采用记录" extra={<Button disabled={!shots.length || !media.some(m => m.mime.startsWith('video/')) || !assets.length} onClick={() => { form.resetFields(); setMode('take') }}>采用导入片段</Button>}>
      <List dataSource={takes} locale={{ emptyText: '暂无采用记录' }} renderItem={t => <List.Item><div>{shots.find(s => s.value === t.shot_id)?.label ?? t.shot_id}<p><a href={`${OpenAPI.BASE}/api/v1/studio/production/projects/${encodeURIComponent(projectId)}/media/${encodeURIComponent(t.media_id)}/content`} target="_blank" rel="noreferrer">{media.find(m => m.id === t.media_id)?.name ?? '媒体待核验'} · 查看片段</a></p><p className="text-gray-500">用户导入 · {t.versions.map(v => `v${v.revision}`).join(' / ')} · 历史版本已固定</p></div>{t.selected ? <Tag color="green">当前采用</Tag> : <Button disabled={busy} onClick={() => void mutate(() => P.postSelectionApiV1StudioProductionProjectsProjectIdTakesTakeIdSelectPost({ projectId, takeId: t.id }))}>切换采用</Button>}</List.Item>} />
    </Card>
    <Modal title={mode === 'create' ? '新建版本资产' : mode === 'revise' ? '发布新版本' : '采用导入片段'} open={mode !== null} onCancel={() => setMode(null)} footer={null} destroyOnClose>
      <Form form={form} layout="vertical" onFinish={submit} initialValues={{ kind: 'scene', description: '', media_ids: [] }} preserve={false}>
        {mode === 'create' && <><Form.Item name="kind" label="资产类别" rules={[{ required: true }]}><Select options={kinds} /></Form.Item><Form.Item name="name" label="资产名称" rules={[{ required: true }]}><Input maxLength={120} /></Form.Item>{['appearance', 'voice'].includes(kind) && <Form.Item name="subject_id" label="所属角色" rules={[{ required: true }]}><Select options={assets.filter(a => a.asset.kind === 'character' && !a.asset.archived).map(a => ({ value: a.asset.entity_id, label: a.asset.name }))} /></Form.Item>}</>}
        {mode !== 'take' ? <><Form.Item name="description" label="描述"><Input.TextArea rows={4} maxLength={16000} /></Form.Item><Form.Item name="media_ids" label="参考媒体"><Select mode="multiple" options={mediaOptions} /></Form.Item></> : <><Form.Item name="shot_id" label="镜头" rules={[{ required: true }]}><Select options={shots} /></Form.Item><Form.Item name="media_id" label="视频素材" rules={[{ required: true }]}><Select options={media.filter(m => m.mime.startsWith('video/')).map(m => ({ value: m.id, label: m.name }))} /></Form.Item><Form.Item name="version_ids" label="锁定版本" rules={[{ required: true }]}><Select mode="multiple" options={assets.filter(a => !a.asset.archived).flatMap(a => a.versions.map(v => ({ value: v.id, label: `${a.asset.name} · v${v.revision}` })))} /></Form.Item></>}
        <Button type="primary" htmlType="submit" loading={busy}>保存</Button>
      </Form>
    </Modal>
  </div>
}
