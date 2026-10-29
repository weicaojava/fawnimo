import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, Input, Pagination, Select, Space, Spin, Tag, message } from 'antd'
import { DownloadOutlined, FileImageOutlined, UploadOutlined, VideoCameraOutlined } from '@ant-design/icons'
import { StudioFilesService } from '../../../services/generated'
import type { FileRead } from '../../../services/generated'
import { resolveAssetUrl } from '../assets/utils'
import { agentError } from '../agents/agentConfigIO'
import '../assets/components/LibraryPages.css'

const PRESET_TAGS = ['首帧', '尾帧', '关键帧', '成片', '版本1', '现实主义', '科幻', '待导出']
const PAGE_SIZE = 12

/** Save the exact binary response locally without decoding it through a text representation. */
function saveBlob(blob: unknown, name: string): void {
  if (!(blob instanceof Blob)) throw new Error('Server did not return a binary file')
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name.replace(/[\\/:*?"<>|]/g, '_').slice(0, 180) || 'download'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Browse real stored files, persist tags and download actual selected media or ZIP bundles. */
export default function FileManager() {
  const uploadInput = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<FileRead[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [tagFilter, setTagFilter] = useState<string | undefined>()
  const [selected, setSelected] = useState<string[]>([])
  const [quickTag, setQuickTag] = useState('')
  const [tagging, setTagging] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)
  const [page, setPage] = useState(1)

  /** Read complete pagination so tags and search never silently omit later pages. */
  const load = async () => {
    setLoading(true); setError('')
    try {
      const all: FileRead[] = []
      let nextPage = 1
      let total = 0
      do {
        const response = await StudioFilesService.listFilesApiApiV1StudioFilesGet({ page: nextPage, pageSize: 100, order: 'created_at', isDesc: true })
        const items = response.data?.items ?? []
        total = response.data?.pagination.total ?? 0
        all.push(...items)
        if (!items.length) break
        nextPage += 1
      } while (all.length < total)
      setFiles(all)
      setSelected(previous => previous.filter(id => all.some(item => item.id === id)))
    } catch (failure) { setError(agentError(failure, '加载文件失败')) }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])
  useEffect(() => { setPage(1) }, [search, tagFilter])

  const tags = useMemo(() => [...new Set(files.flatMap(file => file.tags ?? []))].sort(), [files])
  const filtered = useMemo(() => files.filter(file => (!tagFilter || file.tags?.includes(tagFilter)) && (!search || [file.name, ...(file.tags ?? [])].some(value => value.toLowerCase().includes(search.toLowerCase())))), [files, tagFilter, search])
  const visible = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  /** Toggle a real file identity; selection never pretends to modify its metadata. */
  const toggle = (id: string) => setSelected(previous => previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id])

  /** Persist each requested tag change, then reload the server's results even after partial failure. */
  const addTag = async () => {
    const tag = quickTag.trim()
    if (!selected.length || !tag) return
    if (tag.length > 80) { message.warning('标签长度不得超过 80 个字符'); return }
    setTagging(true)
    let changed = 0
    try {
      for (const id of selected) {
        const file = files.find(item => item.id === id)
        if (!file) continue
        await StudioFilesService.updateFileMetaApiV1StudioFilesFileIdPatch({ fileId: id, requestBody: { tags: [...new Set([...(file.tags ?? []), tag])] } })
        changed += 1
      }
      setQuickTag(''); message.success(`已为 ${changed} 个文件保存标签`)
    } catch (failure) { message.error(agentError(failure, `标签未全部保存，已完成 ${changed} 个，请刷新检查`)) }
    finally { await load(); setTagging(false) }
  }

  /** Request a real file through the generated client and save only a successful binary response. */
  const download = async (file: FileRead) => {
    setDownloading(file.id)
    try { const blob = await StudioFilesService.downloadFileApiApiV1StudioFilesFileIdDownloadGet({ fileId: file.id }); saveBlob(blob, file.name) }
    catch (failure) { message.error(agentError(failure, '下载失败，请检查文件是否存在')) }
    finally { setDownloading(null) }
  }

  /** Export an atomic, bounded ZIP whose entries were all verified by the backend. */
  const exportSelected = async () => {
    if (!selected.length) return
    setExporting(true)
    try { const blob = await StudioFilesService.exportFileBundle({ fileIds: selected }); saveBlob(blob, 'jellyfish-files.zip') }
    catch (failure) { message.error(agentError(failure, '导出失败，请减少文件数量或检查素材是否可用')) }
    finally { setExporting(false) }
  }

  /** Import supported local media through the actual upload endpoint without model calls. */
  const upload = async (file?: File) => {
    if (!file) return
    if (file.size > 64 * 1024 * 1024) { message.warning('单个文件不得超过 64 MiB'); return }
    setUploading(true)
    // This generator types multipart binary fields as strings; the real File reaches FormData unchanged.
    try { await StudioFilesService.uploadFileApiApiV1StudioFilesUploadPost({ formData: { file: file as unknown as string }, name: file.name }); await load(); message.success('文件已保存') }
    catch (failure) { message.error(agentError(failure, '上传失败，请检查文件格式或本地存储配置')) }
    finally { setUploading(false); if (uploadInput.current) uploadInput.current.value = '' }
  }

  return <div className="library-page">
    <header className="library-page-header"><h1 className="library-page-title">文件列表</h1><Space wrap><input ref={uploadInput} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.gif,.mp4,.mov,.mkv,.avi,.webm" aria-label="上传本地媒体文件" onChange={event => void upload(event.target.files?.[0])} /><Button icon={<UploadOutlined />} loading={uploading} onClick={() => uploadInput.current?.click()}>上传文件</Button><Button onClick={() => void load()} loading={loading}>刷新</Button></Space></header>
    {error && <Alert type="error" message={error} showIcon action={<Button onClick={() => void load()}>重试</Button>} />}
    <div className="library-toolbar"><div className="library-toolbar-controls">
      <Input.Search placeholder="搜索文件名或标签" aria-label="搜索文件名或标签" className="library-search" value={search} onChange={event => setSearch(event.target.value)} allowClear />
      <Select placeholder="按标签筛选" aria-label="按标签筛选" value={tagFilter} onChange={setTagFilter} options={tags.map(tag => ({ label: tag, value: tag }))} allowClear style={{ minWidth: 160 }} />
      <Select mode="tags" aria-label="选择或输入标签" placeholder="选择或输入标签" value={quickTag ? [quickTag] : []} onChange={values => setQuickTag(values[values.length - 1] ?? '')} options={[...new Set([...PRESET_TAGS, ...tags])].map(tag => ({ label: tag, value: tag }))} style={{ minWidth: 180 }} />
      <Button disabled={!selected.length || !quickTag.trim()} loading={tagging} onClick={() => void addTag()}>保存标签</Button>
    </div><Space wrap><Button disabled={!visible.length} onClick={() => setSelected(previous => [...new Set([...previous, ...visible.map(file => file.id)])])}>选择本页</Button><Button disabled={!selected.length} onClick={() => setSelected([])}>清空选择</Button><Button icon={<DownloadOutlined />} loading={exporting} disabled={!selected.length || selected.length > 20} title="每次最多 20 个文件，总大小不超过 32 MiB" onClick={() => void exportSelected()}>批量导出{selected.length ? ` (${selected.length})` : ''}</Button></Space></div>
    <p className="library-muted mb-4">共 {filtered.length} 个文件。批量导出最多 20 个文件，总大小不超过 32 MiB。</p>
    <Spin spinning={loading}><div className="library-file-grid">{visible.map(file => <Card key={file.id} size="small" hoverable className="library-file-card" role="checkbox" aria-label={file.name} aria-checked={selected.includes(file.id)} tabIndex={0} onClick={() => toggle(file.id)} onKeyDown={event => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); toggle(file.id) } }} cover={<div className="library-file-stage">{file.thumbnail ? <img src={resolveAssetUrl(file.thumbnail)} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'contain' }} /> : file.type === 'video' ? <VideoCameraOutlined className="text-2xl" /> : <FileImageOutlined className="text-2xl" />}</div>}><div className="font-medium text-sm truncate" title={file.name}>{file.name}</div><Space wrap className="mt-2">{(file.tags ?? []).map(tag => <Tag key={tag}>{tag}</Tag>)}</Space><div className="mt-3"><Button type="link" icon={<DownloadOutlined />} loading={downloading === file.id} onClick={event => { event.stopPropagation(); void download(file) }}>下载</Button></div></Card>)}</div>{!loading && !filtered.length && <Empty description="暂无文件" />}</Spin>
    <Pagination className="mt-4" current={page} pageSize={PAGE_SIZE} total={filtered.length} showSizeChanger={false} onChange={setPage} hideOnSinglePage />
  </div>
}
