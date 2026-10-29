import { useEffect, useState } from 'react'
import { VideoRuntimeService } from '../services/generated'

export type GenerationAvailability = { available: boolean; loading: boolean; message: string }

/** The trusted video worker never grants permission to older Film/Celery image or text actions. */
export function useGenerationAvailability(): GenerationAvailability {
  const [state, setState] = useState<GenerationAvailability>({ available: false, loading: true, message: '正在检查模型服务；手工编辑与保存仍可使用。' })
  useEffect(() => {
    let active = true
    let current: ReturnType<typeof VideoRuntimeService.getVideoRuntimeSetup> | undefined
    /** Refresh availability on mount or when returning from configuration in another window. */
    const refresh = () => {
      current?.cancel()
      setState(previous => ({ ...previous, available: false, loading: true, message: '正在检查模型服务；手工编辑与保存仍可使用。' }))
      const request = VideoRuntimeService.getVideoRuntimeSetup()
      current = request
      void request.then(response => {
        if (!active || current !== request) return
        const available = response.data?.legacy_actions_available === true
        setState({ available, loading: false, message: available ? '当前入口的模型执行器已就绪' : '当前入口的模型执行器尚未接通；可继续手动编辑、上传和保存。视频生成请使用受信镜头计划流程。' })
      }).catch(() => {
        if (active && current === request) setState({ available: false, loading: false, message: '暂时无法确认模型服务状态，未启用模型动作；可继续手工编辑、上传和保存。' })
      })
    }
    refresh()
    window.addEventListener('focus', refresh)
    return () => { active = false; current?.cancel(); window.removeEventListener('focus', refresh) }
  }, [])
  return state
}
