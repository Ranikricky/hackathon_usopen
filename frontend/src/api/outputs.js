import service, { requestWithRetry } from './index'

/**
 * Generate a read-only output from structured simulation state.
 * @param {Object} data - { simulation_id, output_type }
 */
export const generateStructuredOutput = (data) => {
  return requestWithRetry(() => service.post('/api/outputs/generate', data), 2, 1000)
}

/**
 * Fetch a read-only output from structured simulation state.
 * @param {string} simulationId
 * @param {string} outputType
 */
export const getStructuredOutput = (simulationId, outputType) => {
  return service.get(`/api/outputs/${simulationId}/${outputType}`)
}
