import { handleStockApi } from '../utils/stock-api'
export default defineEventHandler((event) => handleStockApi(event, 'scanner'))
