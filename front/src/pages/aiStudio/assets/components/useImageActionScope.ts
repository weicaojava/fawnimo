import { useEffect, useRef } from 'react'
import { Modal } from 'antd'
import { useIdentity } from '../../../../identity/IdentityGate'
import { IdentityService } from '../../../../services/generated'
import { workspaceRequestScope } from '../../../../services/workspaceContext'

type Stamp = { domain: string; epoch: number; namespace: string; identity: { userId: string; workspaceId: string } | null }
type Confirmation = Parameters<typeof Modal.confirm>[0]

/** Fence image actions by monotonic view history and re-attest managed cookies before sending or accepting results. */
export function useImageActionScope(context: string) {
  const identity = useIdentity()
  const namespace = workspaceRequestScope()
  const visible = identity ? { userId: identity.user.id, workspaceId: identity.workspace_id } : null
  const domain = JSON.stringify([namespace, visible?.userId, visible?.workspaceId, context])
  const current = useRef({ domain, namespace, identity: visible, epoch: 0, active: true })
  const modals = useRef(new Set<ReturnType<typeof Modal.confirm>>())
  if (current.current.domain !== domain) current.current.epoch += 1
  current.current.domain = domain; current.current.namespace = namespace; current.current.identity = visible

  /** Destroy only confirmations owned by this mounted business scope. */
  const closeModals = () => { for (const modal of modals.current) modal.destroy(); modals.current.clear() }
  useEffect(() => {
    current.current.active = true
    return () => { current.current.active = false; current.current.epoch += 1; closeModals() }
  }, [])
  useEffect(() => () => { closeModals() }, [domain])

  /** Capture this exact view generation; A to B to A is a different generation. */
  const capture = (): Stamp => ({ domain: current.current.domain, epoch: current.current.epoch,
    namespace: current.current.namespace, identity: current.current.identity })
  /** Test both mounted view history and the immediately bound global identity, without changing any state. */
  const isCurrent = (stamp: Stamp) => current.current.active && current.current.domain === stamp.domain
    && current.current.epoch === stamp.epoch && stamp.namespace === workspaceRequestScope()
  /** Abort a stale continuation without clearing its original request identity or authorizing a new destination. */
  const ensure = (stamp: Stamp) => {
    if (!isCurrent(stamp)) throw new Error('页面、项目或登录身份已变化，请从当前页面重新确认；未继续原操作')
  }
  /** Catch a second tab replacing the HttpOnly session even when this view has not rerendered yet. */
  const attest = async (stamp: Stamp) => {
    ensure(stamp)
    if (stamp.identity) {
      const response = await IdentityService.meApiV1IdentityMeGet()
      ensure(stamp)
      if (!response.data || response.data.user.id !== stamp.identity.userId || response.data.workspace_id !== stamp.identity.workspaceId) {
        throw new Error('登录身份或个人空间已变化，请刷新后核对原任务')
      }
    }
    ensure(stamp)
  }
  /** Require a fresh confirmation even after a same-asset history selection changes the visible intent. */
  const invalidate = () => { current.current.epoch += 1; closeModals() }
  /** Close owned dialogs on context changes and guard onOk even when an already captured callback is invoked later. */
  const confirm = (options: Confirmation, stamp = capture()) => {
    if (!isCurrent(stamp)) return
    closeModals()
    let handle: ReturnType<typeof Modal.confirm> | undefined
    handle = Modal.confirm({ ...options,
      onOk: (...args) => isCurrent(stamp) ? options.onOk?.(...args) : undefined,
      afterClose: () => { if (handle) modals.current.delete(handle); options.afterClose?.() },
    })
    if (handle && typeof handle.destroy === 'function') modals.current.add(handle)
  }
  return { key: domain, capture, isCurrent, ensure, attest, invalidate, confirm }
}
