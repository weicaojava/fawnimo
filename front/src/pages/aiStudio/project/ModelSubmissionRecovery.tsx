import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Space } from 'antd'
import { agentError } from '../agents/agentConfigIO'
import { chargeMoney } from './ProjectChargePicker'

export type ModelSubmissionRecoveryAction = {
  key: string
  label: string
  projectName: string
  upperMinor: number
  recover: () => Promise<void>
}

/** Retain each uncertain, frozen action in this mounted business scope without depending on a new budget check. */
export function useModelSubmissionRecovery(scope: string, setBusy: (busy: boolean) => void, setError: (error: string) => void) {
  const [items, setItems] = useState<ModelSubmissionRecoveryAction[]>([])
  const currentScope = useRef(scope)
  const active = useRef(true)
  currentScope.current = scope
  useEffect(() => { active.current = true; setItems([]); setBusy(false); return () => { active.current = false } }, [scope, setBusy])
  /** A late original response belongs only to the still-mounted original business page. */
  const isCurrent = () => active.current && currentScope.current === scope
  /** Keep the exact original transport closure; subsequent form edits cannot replace its payload or destination. */
  const remember = (action: ModelSubmissionRecoveryAction) => {
    if (isCurrent()) setItems(previous => [...previous.filter(item => item.key !== action.key), action])
  }
  /** Remove only a positively returned original action, preserving any other uncertain submissions. */
  const forget = (key: string) => setItems(previous => previous.filter(item => item.key !== key))
  /** Replay only after an explicit user click; the server resolves the original request before considering new admission. */
  const recover = async (action: ModelSubmissionRecoveryAction) => {
    setBusy(true); setError('')
    try { await action.recover(); forget(action.key) }
    catch (failure) { setError(agentError(failure, '原提交仍未确认，可继续恢复或先查看历史；原费用仍保留')) }
    finally { if (isCurrent()) setBusy(false) }
  }
  return { items, remember, forget, recover, isCurrent, atCapacity: items.length >= 8 }
}

/** Offer explicit recovery even when an accepted request has already consumed all of its project's balance. */
export default function ModelSubmissionRecovery({ items, busy, onRecover }: {
  items: ModelSubmissionRecoveryAction[]
  busy: boolean
  onRecover: (action: ModelSubmissionRecoveryAction) => Promise<void>
}) {
  if (!items.length) return null
  return <Alert type="warning" showIcon message="有待确认的原提交" description={<Space direction="vertical" className="w-full">
    <span>按上次确认的内容恢复，已接收的提交会返回原记录。原项目、模型和费用授权保持原样；刷新或离开此页前请先核对历史。</span>
    {items.map(item => <Space wrap key={item.key}><span>{item.label} · {item.projectName} · 原完整预留 {chargeMoney(item.upperMinor)}</span>
      <Button disabled={busy} onClick={() => Modal.confirm({ title: '恢复这次原提交？', content: `「${item.label}」仍归属「${item.projectName}」，使用原先确认的内容和 ${chargeMoney(item.upperMinor)} 上界。已接收时返回原记录；未接收时按原授权重新核对。`, okText: '确认恢复原提交', cancelText: '取消', onOk: () => onRecover(item) })}>恢复上次提交</Button>
    </Space>)}
  </Space>} />
}
