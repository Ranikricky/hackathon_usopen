import axios from 'axios'
import i18n from '../i18n'

const resolveApiBaseUrl = () => {
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL
  }

  if (typeof window === 'undefined') {
    return ''
  }

  const { hostname, port } = window.location

  if (hostname.endsWith('vercel.app')) {
    return 'https://horizon-xl.onrender.com'
  }

  if (hostname === '127.0.0.1' || hostname === 'localhost') {
    // Vite dev on port 3000 proxies /api. Vite preview on 4173 does not,
    // so preview needs to call the local Flask backend directly.
    return port === '4173' ? 'http://127.0.0.1:5001' : ''
  }

  return ''
}

// Shared axios instance.
const service = axios.create({
  baseURL: resolveApiBaseUrl(),
  timeout: 300000
})

// Request interceptor.
service.interceptors.request.use(
  config => {
    config.headers['Accept-Language'] = i18n.global.locale.value
    if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
      // Let the browser set multipart/form-data with the required boundary.
      // Keeping the JSON default here makes Flask see an empty request.form.
      if (typeof config.headers?.delete === 'function') {
        config.headers.delete('Content-Type')
        config.headers.delete('content-type')
      } else {
        delete config.headers['Content-Type']
        delete config.headers['content-type']
      }
    }
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// Response interceptor with retry-friendly errors.
service.interceptors.response.use(
  response => {
    const res = response.data
    
    // If the API returns a structured failure, surface it as an error.
    if (!res.success && res.success !== undefined) {
      const message = res.error || res.message || 'Error'
      const apiError = new Error(message)
      // Keep API payload on the thrown error for deterministic recovery logic.
      apiError.apiError = res
      apiError.status = response.status
      console.error('API Error:', message, res)
      return Promise.reject(apiError)
    }
    
    return res
  },
  error => {
    console.error('Response error:', error)
    
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
    }
    
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }
    
    return Promise.reject(error)
  }
)

// Request helper with exponential backoff.
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      const status = error?.response?.status
      // Client errors usually cannot be fixed by retrying, except rate limits.
      if (status && status >= 400 && status < 500 && status !== 429) {
        throw error
      }
      if (i === maxRetries - 1) throw error
      
      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
