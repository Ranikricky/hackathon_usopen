/**
 * 临时存储待处理输入（可仅提示词，也可附带文件）
 * 用于首页点击启动引擎后立即跳转，在Process页面再进行API调用
 */
import { reactive } from 'vue'

const STORAGE_KEY = 'horizonxl.pendingPrompt'
const LEGACY_STORAGE_KEY = 'horizonxl.lastPrompt'

export function getLastPrompt() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY) || localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const restored = JSON.parse(raw)
      if (restored?.simulationRequirement) {
        return String(restored.simulationRequirement || '').trim()
      }
    }
    return String(localStorage.getItem(LEGACY_STORAGE_KEY) || '').trim()
  } catch (err) {
    return ''
  }
}

const state = reactive({
  files: [],
  simulationRequirement: '',
  isPending: false
})

export function setPendingUpload(files, requirement) {
  const normalizedRequirement = String(requirement || '').trim()
  state.files = files
  state.simulationRequirement = normalizedRequirement
  state.isPending = normalizedRequirement.length > 0
  try {
    const payload = JSON.stringify({
      simulationRequirement: normalizedRequirement,
      isPending: normalizedRequirement.length > 0,
    })
    sessionStorage.setItem(STORAGE_KEY, payload)
    localStorage.setItem(STORAGE_KEY, payload)
    localStorage.setItem(LEGACY_STORAGE_KEY, normalizedRequirement)
  } catch (err) {
    // File objects cannot be persisted; the prompt is enough for prompt-only runs.
  }
}

export function getPendingUpload() {
  if (!state.isPending) {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY) || localStorage.getItem(STORAGE_KEY)
      if (raw) {
        const restored = JSON.parse(raw)
        if (restored?.isPending && restored.simulationRequirement) {
          state.files = []
          state.simulationRequirement = String(restored.simulationRequirement || '').trim()
          state.isPending = true
        }
      }
      if (!state.isPending) {
        const legacyPrompt = String(localStorage.getItem(LEGACY_STORAGE_KEY) || '').trim()
        if (legacyPrompt) {
          state.files = []
          state.simulationRequirement = legacyPrompt
          state.isPending = true
        }
      }
    } catch (err) {
      // Ignore invalid session state and fall back to the in-memory object.
    }
  }
  return {
    files: state.files,
    simulationRequirement: state.simulationRequirement,
    isPending: state.isPending
  }
}

export function clearPendingUpload() {
  state.files = []
  state.simulationRequirement = ''
  state.isPending = false
  try {
    sessionStorage.removeItem(STORAGE_KEY)
    localStorage.removeItem(STORAGE_KEY)
  } catch (err) {
    // Ignore storage cleanup failures.
  }
}

export default state
