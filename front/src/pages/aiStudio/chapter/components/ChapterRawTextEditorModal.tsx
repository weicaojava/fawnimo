import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Card, Collapse, Empty, Input, List, Modal, Pagination, Space, Spin, Tag, message } from 'antd'
import {
  CloseCircleOutlined,
  DiffOutlined,
  FileTextOutlined,
  HistoryOutlined,
  ReloadOutlined,
  SaveOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { ScriptProcessingService, StudioChaptersService } from '../../../../services/generated'
import type { ScriptConsistencyCheckResult } from '../../../../services/generated'
import {
  CONSISTENCY_CHECK_RELATION_TYPE,
  SCRIPT_OPTIMIZATION_RELATION_TYPE,
  SCRIPT_SIMPLIFICATION_RELATION_TYPE,
  useCancelableRelationTask,
} from '../../project/ProjectWorkbench/chapterDivisionTasks'
import { executeAsyncTaskCreate, executeTaskCancel, notifyExistingTask } from '../../components/taskActionHelpers'
import { handleTaskResultSafely } from '../../components/taskResultHelpers'
import { useRelationTaskNotification } from '../../components/taskNotificationHelpers'
import { useTaskPageContext } from '../../components/taskPageContext'
import { TASK_COPY } from '../../components/taskCopy'
import { useGenerationAvailability } from '../../../../hooks/useGenerationAvailability'
import { ScriptCandidatePanel } from './ScriptCandidatePanel'

type EditorMode = 'raw' | 'condensed' | 'compare'

type HistoryItem = {
  id: string
  revision: number
  created_at: string
  raw_text: string
  condensed_text: string
  origin: string
  restored_from_revision?: number | null
}

/** Keep revision conflicts actionable while retaining the user's unsaved editor text. */
function historyError(error: unknown, fallback: string): string {
  const body = (error as { body?: { message?: unknown } })?.body
  return typeof body?.message === 'string' ? body.message : fallback
}

/** Edit real chapter text and expose only server-persisted history and restores. */
export function ChapterRawTextEditorModal({
  open,
  onClose,
  chapterId,
  projectId,
  initialCandidateOperation,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  chapterId: string | undefined
  projectId?: string
  initialCandidateOperation?: 'optimize' | 'simplify' | 'divide'
  onSaved?: (next: { rawText?: string; condensedText?: string }) => void
}) {
  const generation = useGenerationAvailability()
  const consistencyTaskCopy = TASK_COPY.consistencyCheck
  const simplifyTaskCopy = TASK_COPY.scriptSimplify
  const optimizeTaskCopy = TASK_COPY.scriptOptimize
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [checkingConsistency, setCheckingConsistency] = useState(false)
  const [optimizingScript, setOptimizingScript] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [candidateBusy, setCandidateBusy] = useState(false)
  const [candidateSections, setCandidateSections] = useState<string[]>([])
  const [candidateOperation, setCandidateOperation] = useState<'optimize' | 'simplify' | 'divide'>('optimize')
  useEffect(() => {
    if (open && initialCandidateOperation) { setCandidateOperation(initialCandidateOperation); setCandidateSections(['ai']) }
  }, [open, chapterId, initialCandidateOperation])
  const [historyItems, setHistoryItems] = useState<HistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyFailure, setHistoryFailure] = useState('')
  const [historyPage, setHistoryPage] = useState(1)
  const [historyTotal, setHistoryTotal] = useState(0)
  const [textRevision, setTextRevision] = useState(0)
  const [latestHistoryRevision, setLatestHistoryRevision] = useState(0)
  const [loadFailure, setLoadFailure] = useState('')
  const [reloadVersion, setReloadVersion] = useState(0)
  const editorSession = useRef(0)
  const historyRequest = useRef(0)

  const [mode, setMode] = useState<EditorMode>('raw')
  const [rawText, setRawText] = useState('')
  const [condensedText, setCondensedText] = useState('')
  const [savedRawText, setSavedRawText] = useState('')
  const [savedCondensedText, setSavedCondensedText] = useState('')
  const [editorText, setEditorText] = useState('')
  const [compareRaw, setCompareRaw] = useState('')
  const [compareCondensed, setCompareCondensed] = useState('')
  const [consistencyResult, setConsistencyResult] = useState<ScriptConsistencyCheckResult | null>(null)

  const applyConsistencyTaskResult = useCallback(async (taskId: string) => {
    await handleTaskResultSafely(taskId, {
      readErrorMessage: '读取一致性检查结果失败',
      failedFallbackMessage: '一致性检查失败',
      onSucceeded: (resultValue) => {
        setConsistencyResult(resultValue as ScriptConsistencyCheckResult)
        const result = resultValue as Record<string, any>
        if (result?.has_issues) {
          message.warning(`发现 ${Array.isArray(result.issues) ? result.issues.length : 0} 个角色混淆问题`)
        } else {
          message.success('未发现角色混淆问题')
        }
      },
      onFailed: (errorMessage) => {
        message.error(errorMessage)
      },
      onReadError: () => {
        message.error('读取一致性检查结果失败')
      },
    })
  }, [])

  const applySimplifyResult = useCallback(async (taskId: string) => {
    await handleTaskResultSafely(taskId, {
      readErrorMessage: '读取智能精简结果失败',
      failedFallbackMessage: '智能精简失败',
      onSucceeded: (resultValue) => {
        const result = resultValue as Record<string, any>
        const simplified = String(result?.simplified_script_text ?? '').trim()
        if (!simplified) {
          message.error('智能精简失败：未返回有效内容')
          return
        }
        setCondensedText(simplified)
        if (mode === 'compare') {
          setCompareCondensed(simplified)
        } else {
          setMode('condensed')
          setEditorText(simplified)
        }
        message.success('智能精简完成')
      },
      onFailed: (errorMessage) => {
        message.error(errorMessage)
      },
      onReadError: () => {
        message.error('读取智能精简结果失败')
      },
    })
  }, [mode])

  const applyOptimizeResult = useCallback(async (taskId: string) => {
    await handleTaskResultSafely(taskId, {
      readErrorMessage: '读取一键优化结果失败',
      failedFallbackMessage: '一键优化失败',
      onSucceeded: (resultValue) => {
        const result = resultValue as Record<string, any>
        const optimized = String(result?.optimized_script_text ?? '').trim()
        if (!optimized) {
          message.error('一键优化失败：未返回有效内容')
          return
        }
        setRawText(optimized)
        setEditorText(optimized)
        setMode('raw')
        setCompareRaw(optimized)
        message.success('一键优化完成')
      },
      onFailed: (errorMessage) => {
        message.error(errorMessage)
      },
      onReadError: () => {
        message.error('读取一键优化结果失败')
      },
    })
  }, [])

  const { task: consistencyTask, settledTask: consistencySettledTask, trackTaskData: trackConsistencyTaskData, applyCancelData: applyConsistencyCancelData } = useCancelableRelationTask({
    enabled: open && !!chapterId,
    relationType: CONSISTENCY_CHECK_RELATION_TYPE,
    relationEntityId: chapterId,
    onTaskSettled: applyConsistencyTaskResult,
  })

  const { task: simplifyTask, settledTask: simplifySettledTask, trackTaskData: trackSimplifyTaskData, applyCancelData: applySimplifyCancelData } = useCancelableRelationTask({
    enabled: open && !!chapterId,
    relationType: SCRIPT_SIMPLIFICATION_RELATION_TYPE,
    relationEntityId: chapterId,
    onTaskSettled: applySimplifyResult,
  })

  const { task: optimizeTask, settledTask: optimizeSettledTask, trackTaskData: trackOptimizeTaskData, applyCancelData: applyOptimizeCancelData } = useCancelableRelationTask({
    enabled: open && !!chapterId,
    relationType: SCRIPT_OPTIMIZATION_RELATION_TYPE,
    relationEntityId: chapterId,
    onTaskSettled: applyOptimizeResult,
  })
  useTaskPageContext(
    open && chapterId
      ? [
          {
            relationType: CONSISTENCY_CHECK_RELATION_TYPE,
            relationEntityId: chapterId,
          },
          {
            relationType: SCRIPT_SIMPLIFICATION_RELATION_TYPE,
            relationEntityId: chapterId,
          },
          {
            relationType: SCRIPT_OPTIMIZATION_RELATION_TYPE,
            relationEntityId: chapterId,
          },
        ]
      : [],
  )

  const plainWordCount = useMemo(() => editorText.trim().length, [editorText])
  const paragraphCount = useMemo(() => editorText.split(/\n\s*\n/).filter((p) => p.trim()).length, [editorText])
  const actionsLoading =
    candidateBusy || extracting || checkingConsistency || optimizingScript || !!simplifyTask || !!optimizeTask || !!consistencyTask
  const consistencyIssues = useMemo(() => {
    const list = (consistencyResult?.issues as any[] | undefined) ?? []
    return Array.isArray(list) ? list : []
  }, [consistencyResult])

  useEffect(() => {
    const token = ++editorSession.current
    if (!open || !chapterId) return
    setHistoryOpen(false)
    setHistoryItems([])
    setHistoryFailure('')
    setHistoryLoading(false)
    setHistoryPage(1)
    setHistoryTotal(0)
    setSaving(false)
    setLoadFailure('')
    setLoading(true)
    StudioChaptersService.listChapterTextHistoryApiV1StudioChaptersChapterIdTextHistoryGet({ chapterId })
      .then((res) => {
        if (token !== editorSession.current) return
        const data = res.data
        if (!data) throw new Error('章节文本未返回')
        const nextRaw = data?.raw_text ?? ''
        const nextCondensed = data?.condensed_text ?? ''
        setTextRevision(data.current_revision)
        setLatestHistoryRevision(data.current_revision)
        setHistoryItems(data.items)
        setHistoryTotal(data.total)
        setRawText(nextRaw)
        setCondensedText(nextCondensed)
        setSavedRawText(nextRaw)
        setSavedCondensedText(nextCondensed)
        setMode('raw')
        setEditorText(nextRaw)
        setCompareRaw(nextRaw)
        setCompareCondensed(nextCondensed)
        setConsistencyResult(null)
      })
      .catch((reason) => {
        if (token !== editorSession.current) return
        const explanation = historyError(reason, '加载章节文本与历史失败，请重试')
        setLoadFailure(explanation)
        message.error(explanation)
      })
      .finally(() => { if (token === editorSession.current) setLoading(false) })
    return () => { ++editorSession.current; ++historyRequest.current }
  }, [open, chapterId, reloadVersion])

  /** Refresh real history without upgrading the revision underlying unsaved editor text. */
  const loadHistory = async (page = 1) => {
    if (!chapterId) return
    const token = editorSession.current
    const operation = ++historyRequest.current
    setHistoryLoading(true)
    setHistoryFailure('')
    try {
      const response = await StudioChaptersService.listChapterTextHistoryApiV1StudioChaptersChapterIdTextHistoryGet({ chapterId, page, pageSize: 20 })
      if (token !== editorSession.current || operation !== historyRequest.current) return
      if (!response.data) throw new Error('历史记录未返回')
      setHistoryItems(response.data.items)
      setHistoryPage(response.data.page)
      setHistoryTotal(response.data.total)
      setLatestHistoryRevision(response.data.current_revision)
    } catch (reason) {
      if (token === editorSession.current && operation === historyRequest.current) setHistoryFailure(historyError(reason, '读取历史失败，当前编辑内容未改动'))
    } finally {
      if (token === editorSession.current && operation === historyRequest.current) setHistoryLoading(false)
    }
  }

  const handleSmartSimplify = async () => {
    if (!generation.available) { message.warning(generation.message); return }
    if (!rawText.trim()) {
      message.warning('请先输入原文')
      return
    }
    if (notifyExistingTask(simplifyTask, {
      cancellingMessage: simplifyTaskCopy.cancellingMessage,
      runningMessage: simplifyTaskCopy.runningMessage,
    })) {
      return
    }
    setExtracting(true)
    try {
      await executeAsyncTaskCreate({
        request: () =>
          ScriptProcessingService.simplifyScriptAsyncApiV1ScriptProcessingSimplifyScriptAsyncPost({
            requestBody: {
              chapter_id: chapterId ?? null,
              script_text: rawText,
            },
          }),
        trackTaskData: trackSimplifyTaskData,
        startedMessage: simplifyTaskCopy.startedMessage,
        reusedMessage: simplifyTaskCopy.reusedMessage,
        fallbackErrorMessage: '智能精简失败',
      })
    } catch {
      // executeAsyncTaskCreate 已统一处理错误提示
    } finally {
      setExtracting(false)
    }
  }

  const handleCheckConsistency = async () => {
    if (!generation.available) { message.warning(generation.message); return }
    const scriptText = rawText.trim()
    if (!scriptText) {
      message.warning('请先输入原文')
      return
    }
    if (notifyExistingTask(consistencyTask, {
      cancellingMessage: consistencyTaskCopy.cancellingMessage,
      runningMessage: consistencyTaskCopy.runningMessage,
    })) {
      return
    }
    setCheckingConsistency(true)
    try {
      await executeAsyncTaskCreate({
        request: () =>
          ScriptProcessingService.checkConsistencyAsyncApiV1ScriptProcessingCheckConsistencyAsyncPost({
            requestBody: { script_text: scriptText, chapter_id: chapterId ?? null },
          }),
        trackTaskData: trackConsistencyTaskData,
        startedMessage: consistencyTaskCopy.startedMessage,
        reusedMessage: consistencyTaskCopy.reusedMessage,
        fallbackErrorMessage: '一致性检查失败',
      })
    } catch {
      // executeAsyncTaskCreate 已统一处理错误提示
    } finally {
      setCheckingConsistency(false)
    }
  }

  const handleCancelConsistencyTask = async () => {
    if (!consistencyTask?.taskId) return
    try {
      await executeTaskCancel({
        taskId: consistencyTask.taskId,
        reason: '用户在原文编辑弹窗取消一致性检查任务',
        applyCancelData: applyConsistencyCancelData,
        cancelledImmediatelyMessage: consistencyTaskCopy.cancelledImmediatelyMessage,
        cancelRequestedMessage: consistencyTaskCopy.cancelRequestedMessage,
        fallbackErrorMessage: '取消一致性检查任务失败',
      })
    } catch {
      // executeTaskCancel 已统一处理错误提示
    }
  }

  const handleOneClickOptimize = async () => {
    if (!generation.available) { message.warning(generation.message); return }
    const scriptText = rawText.trim()
    if (!scriptText) {
      message.warning('请先输入原文')
      return
    }
    if (!consistencyResult) {
      message.info('请先进行角色混淆检查')
      return
    }
    if (notifyExistingTask(optimizeTask, {
      cancellingMessage: optimizeTaskCopy.cancellingMessage,
      runningMessage: optimizeTaskCopy.runningMessage,
    })) {
      return
    }
    setOptimizingScript(true)
    try {
      await executeAsyncTaskCreate({
        request: () =>
          ScriptProcessingService.optimizeScriptAsyncApiV1ScriptProcessingOptimizeScriptAsyncPost({
            requestBody: {
              chapter_id: chapterId ?? null,
              script_text: scriptText,
              consistency: consistencyResult as any,
            },
          }),
        trackTaskData: trackOptimizeTaskData,
        startedMessage: optimizeTaskCopy.startedMessage,
        reusedMessage: optimizeTaskCopy.reusedMessage,
        fallbackErrorMessage: '一键优化失败',
      })
    } catch {
      // executeAsyncTaskCreate 已统一处理错误提示
    } finally {
      setOptimizingScript(false)
    }
  }

  const handleCancelSimplifyTask = async () => {
    if (!simplifyTask?.taskId) return
    try {
      await executeTaskCancel({
        taskId: simplifyTask.taskId,
        reason: '用户在原文编辑弹窗取消智能精简任务',
        applyCancelData: applySimplifyCancelData,
        cancelledImmediatelyMessage: simplifyTaskCopy.cancelledImmediatelyMessage,
        cancelRequestedMessage: simplifyTaskCopy.cancelRequestedMessage,
        fallbackErrorMessage: '取消智能精简任务失败',
      })
    } catch {
      // executeTaskCancel 已统一处理错误提示
    }
  }

  const handleCancelOptimizeTask = async () => {
    if (!optimizeTask?.taskId) return
    try {
      await executeTaskCancel({
        taskId: optimizeTask.taskId,
        reason: '用户在原文编辑弹窗取消一键优化任务',
        applyCancelData: applyOptimizeCancelData,
        cancelledImmediatelyMessage: optimizeTaskCopy.cancelledImmediatelyMessage,
        cancelRequestedMessage: optimizeTaskCopy.cancelRequestedMessage,
        fallbackErrorMessage: '取消一键优化任务失败',
      })
    } catch {
      // executeTaskCancel 已统一处理错误提示
    }
  }

  useRelationTaskNotification({
    task: consistencyTask,
    settledTask: consistencySettledTask,
    title: consistencyTaskCopy.title,
    sourceLabel: '章节原文编辑',
    runningDescription: consistencyTaskCopy.runningDescription,
    cancellingDescription: consistencyTaskCopy.cancellingDescription,
    successDescription: consistencyTaskCopy.successDescription,
    cancelledDescription: consistencyTaskCopy.cancelledDescription,
    failedDescription: consistencyTaskCopy.failedDescription,
    onCancel: consistencyTask ? () => void handleCancelConsistencyTask() : null,
  })

  useRelationTaskNotification({
    task: simplifyTask,
    settledTask: simplifySettledTask,
    title: simplifyTaskCopy.title,
    sourceLabel: '章节原文编辑',
    runningDescription: simplifyTaskCopy.runningDescription,
    cancellingDescription: simplifyTaskCopy.cancellingDescription,
    successDescription: simplifyTaskCopy.successDescription,
    cancelledDescription: simplifyTaskCopy.cancelledDescription,
    failedDescription: simplifyTaskCopy.failedDescription,
    onCancel: simplifyTask ? () => void handleCancelSimplifyTask() : null,
  })

  useRelationTaskNotification({
    task: optimizeTask,
    settledTask: optimizeSettledTask,
    title: optimizeTaskCopy.title,
    sourceLabel: '章节原文编辑',
    runningDescription: optimizeTaskCopy.runningDescription,
    cancellingDescription: optimizeTaskCopy.cancellingDescription,
    successDescription: optimizeTaskCopy.successDescription,
    cancelledDescription: optimizeTaskCopy.cancelledDescription,
    failedDescription: optimizeTaskCopy.failedDescription,
    onCancel: optimizeTask ? () => void handleCancelOptimizeTask() : null,
  })

  const handleBackToRaw = () => {
    setMode('raw')
    setEditorText(rawText)
  }

  const handleViewCondensed = () => {
    const nextCondensed = mode === 'compare' ? compareCondensed : condensedText
    if (!nextCondensed.trim()) {
      message.info('暂无精简内容')
      return
    }
    setMode('condensed')
    setEditorText(nextCondensed)
  }

  const handleSave = async (): Promise<boolean> => {
    if (!chapterId || saving || loading || loadFailure) return false
    const token = editorSession.current
    setSaving(true)
    try {
      // Both buffers remain editable across modes; save them together before closing the editor.
      const requestBody = { expected_revision: textRevision,
        raw_text: mode === 'compare' ? compareRaw : rawText,
        condensed_text: mode === 'compare' ? compareCondensed : condensedText }
      const response = await StudioChaptersService.saveChapterTextRevisionApiV1StudioChaptersChapterIdTextHistoryPost({ chapterId, requestBody })
      if (token !== editorSession.current) return false
      const data = response.data
      if (!data) throw new Error('保存未返回修订')
      setTextRevision(data.current_revision)
      setLatestHistoryRevision(data.current_revision)
      setRawText(data.raw_text); setSavedRawText(data.raw_text); setCompareRaw(data.raw_text)
      setCondensedText(data.condensed_text); setSavedCondensedText(data.condensed_text); setCompareCondensed(data.condensed_text)
      onSaved?.({ rawText: data.raw_text, condensedText: data.condensed_text })
      message.success('章节文本已保存')
      return true
    } catch (reason) {
      if (token === editorSession.current) message.error(historyError(reason, '保存失败；当前编辑内容仍保留'))
      return false
    } finally {
      if (token === editorSession.current) setSaving(false)
    }
  }

  const hasUnsavedChanges = useMemo(() => {
    const activeRaw = mode === 'compare' ? compareRaw : rawText
    const activeCondensed = mode === 'compare' ? compareCondensed : condensedText
    return activeRaw !== savedRawText || activeCondensed !== savedCondensedText
  }, [compareCondensed, compareRaw, condensedText, mode, rawText, savedCondensedText, savedRawText])

  const handleRequestClose = () => {
    if (actionsLoading || saving) return
    if (!hasUnsavedChanges) {
      onClose()
      return
    }
    Modal.confirm({
      title: '检测到未保存变更',
      content: '文本输入区有未保存修改，关闭前请选择操作。',
      okText: '保存',
      cancelText: '忽略',
      onOk: async () => {
        const ok = await handleSave()
        if (ok) onClose()
      },
      onCancel: () => {
        onClose()
      },
    })
  }

  /** Restore only after explicit acknowledgement; history remains append-only and CAS protected. */
  const restoreHistory = (item: HistoryItem) => {
    if (!chapterId || saving || historyLoading) return
    const token = editorSession.current
    Modal.confirm({
      title: `恢复修订 ${item.revision} 为新修订？`,
      content: hasUnsavedChanges ? '当前未保存的编辑将被替换，请先确认已经保存或复制。已有历史记录会完整保留。' : '恢复会另存一个新修订，已有历史记录会完整保留。',
      okText: '恢复为新修订', cancelText: '取消',
      onOk: async () => {
        if (token !== editorSession.current) return
        setSaving(true)
        setHistoryFailure('')
        try {
          const response = await StudioChaptersService.restoreChapterTextRevisionApiV1StudioChaptersChapterIdTextHistoryRevisionRestorePost({ chapterId, revision: item.revision, requestBody: { expected_revision: textRevision } })
          if (token !== editorSession.current) return
          const data = response.data
          if (!data) throw new Error('恢复未返回新修订')
          setRawText(data.raw_text); setCondensedText(data.condensed_text)
          setSavedRawText(data.raw_text); setSavedCondensedText(data.condensed_text)
          setCompareRaw(data.raw_text); setCompareCondensed(data.condensed_text)
          setEditorText(mode === 'condensed' ? data.condensed_text : data.raw_text)
          setTextRevision(data.current_revision); setLatestHistoryRevision(data.current_revision)
          onSaved?.({ rawText: data.raw_text, condensedText: data.condensed_text })
          message.success(`已恢复为修订 ${data.current_revision}`)
          await loadHistory()
        } catch (reason) {
          if (token === editorSession.current) setHistoryFailure(historyError(reason, '恢复失败；当前编辑内容未改动'))
        } finally {
          if (token === editorSession.current) setSaving(false)
        }
      },
    })
  }

  return (
    <>
      <Modal
        title={
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-2">
              <FileTextOutlined />{' '}
              {mode === 'raw' ? '原文编辑区' : mode === 'condensed' ? '精简内容编辑区' : '对比模式'}
              <Tag color="blue">{plainWordCount} 字</Tag>
              <Tag color="default">{paragraphCount} 段</Tag>
            </div>
            <Space size="small">
              <Button size="small" type="primary" icon={<SaveOutlined />} loading={actionsLoading || saving} disabled={actionsLoading || loading || !!loadFailure} onClick={() => void handleSave()}>
                保存
              </Button>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                loading={checkingConsistency || !!consistencyTask}
                disabled={actionsLoading || saving || loading || !!loadFailure || !!consistencyTask || !generation.available}
                title={!generation.available ? generation.message : undefined}
                onClick={() => void handleCheckConsistency()}
              >
                {consistencyTask ? '检查中' : '角色混淆检查'}
              </Button>
              {consistencyTask ? (
                <Button
                  size="small"
                  danger
                  icon={<CloseCircleOutlined />}
                  disabled={consistencyTask.cancelRequested}
                  onClick={() => void handleCancelConsistencyTask()}
                >
                  {consistencyTask.cancelRequested ? '正在取消' : '取消检查'}
                </Button>
              ) : null}
              <Button
                size="small"
                icon={<ThunderboltOutlined />}
                loading={extracting || !!simplifyTask}
                disabled={actionsLoading || saving || loading || !!loadFailure || !!simplifyTask || (!projectId && !generation.available)}
                title={projectId ? '查看按项目费用生成的精简候选' : !generation.available ? generation.message : undefined}
                onClick={() => { if (projectId) { setCandidateOperation('simplify'); setCandidateSections(['ai']) } else void handleSmartSimplify() }}
              >
                {simplifyTask ? '精简中' : '智能精简'}
              </Button>
              {simplifyTask ? (
                <Button
                  size="small"
                  danger
                  icon={<CloseCircleOutlined />}
                  disabled={simplifyTask.cancelRequested}
                  onClick={() => void handleCancelSimplifyTask()}
                >
                  {simplifyTask.cancelRequested ? '正在取消' : '取消精简'}
                </Button>
              ) : null}
              {mode === 'condensed' ? (
                <Button size="small" icon={<ReloadOutlined />} loading={actionsLoading} disabled={actionsLoading || saving || loading || !!loadFailure} onClick={handleBackToRaw}>
                  回到原文
                </Button>
              ) : (
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  loading={actionsLoading}
                  disabled={actionsLoading || saving || loading || !!loadFailure || !(mode === 'compare' ? compareCondensed : condensedText).trim()}
                  onClick={handleViewCondensed}
                >
                  查看精简
                </Button>
              )}
              <Button
                size="small"
                icon={<DiffOutlined />}
                loading={actionsLoading}
                disabled={actionsLoading || saving || loading || !!loadFailure}
                type={mode === 'compare' ? 'primary' : 'default'}
                onClick={() => {
                  if (mode === 'compare') {
                    setMode('raw')
                    setEditorText(compareRaw)
                    return
                  }
                  setCompareRaw(rawText)
                  setCompareCondensed(condensedText)
                  setMode('compare')
                }}
              >
                对比模式
              </Button>
              <Button size="small" icon={<HistoryOutlined />} loading={actionsLoading} disabled={actionsLoading || loading || saving || !!loadFailure} onClick={() => { setHistoryOpen(true); void loadHistory() }}>
                版本历史
              </Button>
            </Space>
          </div>
        }
        open={open}
        onCancel={() => {
          handleRequestClose()
        }}
        width={900}
        footer={
          <Button type="primary" loading={actionsLoading || saving} disabled={actionsLoading || saving} onClick={handleRequestClose}>
            关闭
          </Button>
        }
        styles={{
          header: { paddingRight: 48 },
          body: { maxHeight: '70vh', overflow: 'auto', paddingTop: 12 },
        }}
      >
        {!projectId && !generation.available && <Alert className="mb-3" type="info" showIcon message={generation.message} description="原文编辑、保存和真实版本历史可继续使用。" />}
        {projectId && chapterId && !loading && !loadFailure && <Collapse activeKey={candidateSections} onChange={value => setCandidateSections(typeof value === 'string' ? [value] : value)} className="mb-3" destroyInactivePanel items={[{
          key: 'ai', label: 'AI 剧本候选 · 优化、精简与分镜提取', children: <ScriptCandidatePanel projectId={projectId} chapterId={chapterId} preferredOperation={candidateOperation}
            hasUnsavedChanges={hasUnsavedChanges || saving} onBusyChange={setCandidateBusy} onAdopted={row => {
              if (row.result && 'text' in row.result) onSaved?.(row.operation === 'optimize' ? { rawText: row.result.text } : { condensedText: row.result.text })
              else onSaved?.({})
              setReloadVersion(value => value + 1)
            }} />,
        }]} />}
        {loading ? (
          <div className="flex justify-center items-center py-10">
            <Spin />
          </div>
        ) : loadFailure ? <Alert type="error" showIcon message={loadFailure} action={<Button onClick={() => setReloadVersion(value => value + 1)}>重新加载</Button>} /> : mode === 'compare' ? (
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col">
              <div className="text-xs text-gray-500 mb-2">原文</div>
              <Input.TextArea
                value={compareRaw}
                onChange={(e) => { setCompareRaw(e.target.value); setRawText(e.target.value) }}
                rows={14}
                disabled={actionsLoading || saving}
                style={{ resize: 'none', fontFamily: 'var(--font-mono)' }}
              />
            </div>
            <div className="flex flex-col">
              <div className="text-xs text-gray-500 mb-2">精简内容</div>
              <Input.TextArea
                value={compareCondensed}
                onChange={(e) => { setCompareCondensed(e.target.value); setCondensedText(e.target.value) }}
                rows={14}
                disabled={actionsLoading || saving}
                style={{ resize: 'none', fontFamily: 'var(--font-mono)' }}
              />
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <Input.TextArea
              value={editorText}
              onChange={(e) => {
                const v = e.target.value
                setEditorText(v)
                if (mode === 'raw') setRawText(v)
                if (mode === 'condensed') setCondensedText(v)
              }}
              disabled={actionsLoading || saving}
              placeholder={mode === 'raw' ? '编辑章节原文…' : '编辑精简内容…'}
              rows={16}
              style={{ resize: 'none', background: 'var(--color-paper)' }}
            />
            {consistencyResult ? (
              <Card
                size="small"
                title="角色混淆检查结果"
                extra={
                  <Space wrap>
                    <Tag color={consistencyResult.has_issues ? 'red' : 'green'}>
                      {consistencyResult.has_issues ? '发现问题' : '无问题'}
                    </Tag>
                    <Tag>issues：{consistencyIssues.length}</Tag>
                    <Button
                      size="small"
                      type="primary"
                      icon={<ThunderboltOutlined />}
                      loading={optimizingScript || !!optimizeTask}
                      disabled={actionsLoading || saving || loading || !!loadFailure || !!optimizeTask || (!projectId && !generation.available)}
                      title={projectId ? '查看按项目费用生成的优化候选' : !generation.available ? generation.message : undefined}
                      onClick={() => { if (projectId) { setCandidateOperation('optimize'); setCandidateSections(['ai']) } else void handleOneClickOptimize() }}
                    >
                      {optimizeTask ? '优化中' : '一键优化'}
                    </Button>
                    {optimizeTask ? (
                      <Button
                        size="small"
                        danger
                        icon={<CloseCircleOutlined />}
                        disabled={optimizeTask.cancelRequested}
                        onClick={() => void handleCancelOptimizeTask()}
                      >
                        {optimizeTask.cancelRequested ? '正在取消' : '取消优化'}
                      </Button>
                    ) : null}
                  </Space>
                }
              >
                {consistencyIssues.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未发现角色混淆问题" />
                ) : (
                  <List
                    size="small"
                    dataSource={consistencyIssues}
                    renderItem={(it: any, idx) => (
                      <List.Item>
                        <div className="min-w-0">
                          <div className="font-medium">
                            {it?.issue_type ? `[${it.issue_type}] ` : ''}
                            Issue {idx + 1}
                          </div>
                          <div className="text-sm">{it?.description}</div>
                          {it?.character_candidates?.length ? (
                            <div className="text-xs text-gray-500 mt-1">候选角色：{it.character_candidates.join('、')}</div>
                          ) : null}
                          {it?.suggestion ? (
                            <div className="text-xs text-gray-500 mt-1">建议：{it.suggestion}</div>
                          ) : null}
                          {it?.affected_lines ? (
                            <div className="text-xs text-gray-400 mt-1">
                              影响范围：{it.affected_lines.start_line ?? '-'}–{it.affected_lines.end_line ?? '-'}
                            </div>
                          ) : null}
                        </div>
                      </List.Item>
                    )}
                  />
                )}
                {consistencyResult.summary ? (
                  <div className="text-xs text-gray-500 mt-2">{String(consistencyResult.summary)}</div>
                ) : null}
              </Card>
            ) : null}
          </div>
        )}
      </Modal>

      <Modal
        title={
          <div className="flex items-center gap-2">
            <HistoryOutlined /> 历史版本
          </div>
        }
        open={historyOpen}
        onCancel={() => setHistoryOpen(false)}
        width={920}
        footer={
          <Button type="primary" onClick={() => setHistoryOpen(false)}>
            关闭
          </Button>
        }
        styles={{ body: { maxHeight: '70vh', overflow: 'auto' } }}
      >
        <div className="text-xs text-gray-500 mb-3">仅显示实际保存的修订。恢复会创建新修订，保留后续历史。</div>
        {historyFailure && <Alert className="mb-3" type="error" showIcon message={historyFailure} action={<Button disabled={historyLoading || saving} onClick={() => void loadHistory(historyPage)}>重试读取</Button>} />}
        {latestHistoryRevision !== textRevision && <Alert className="mb-3" type="warning" showIcon message={`服务器已有修订 ${latestHistoryRevision}，当前编辑基于修订 ${textRevision}；未覆盖你的编辑。请先保留编辑内容，再重新打开章节。`} />}
        <Spin spinning={historyLoading}>
        {!historyLoading && !historyItems.length && !historyFailure && <Empty description="暂无保存历史；首次保存会保留当前文本作为初始修订。" />}
        <div className="space-y-3">
          {historyItems.map((h) => (
            <div key={h.id} className="border border-gray-200 rounded-lg p-3 bg-white">
              <div className="flex items-center justify-between gap-3 flex-wrap mb-2"><Space wrap><strong>修订 {h.revision}</strong><span>{new Date(h.created_at).toLocaleString()}</span><Tag>{h.origin === 'restore' ? `恢复自修订 ${h.restored_from_revision}` : h.origin === 'initial' ? '初始记录' : '编辑保存'}</Tag>{h.revision === latestHistoryRevision && <Tag color="blue">当前版本</Tag>}</Space><Button disabled={saving || historyLoading || h.revision === latestHistoryRevision} onClick={() => restoreHistory(h)}>恢复为新修订</Button></div>
              <Collapse
                items={[
                  {
                    key: 'content',
                    label: '展开查看内容',
                    children: (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                          <div className="text-xs text-gray-500 mb-2">原文内容</div>
                          <Input.TextArea
                            value={h.raw_text}
                            readOnly
                            rows={8}
                            style={{ resize: 'none', fontFamily: 'var(--font-mono)' }}
                          />
                        </div>
                        <div>
                          <div className="text-xs text-gray-500 mb-2">精简内容</div>
                          <Input.TextArea
                            value={h.condensed_text}
                            readOnly
                            rows={8}
                            style={{ resize: 'none', fontFamily: 'var(--font-mono)' }}
                          />
                        </div>
                      </div>
                    ),
                  },
                ]}
              />
            </div>
          ))}
        </div>
        </Spin>
        {historyTotal > 20 && <Pagination className="mt-4" current={historyPage} pageSize={20} total={historyTotal} showSizeChanger={false} onChange={page => void loadHistory(page)} />}
      </Modal>
    </>
  )
}
