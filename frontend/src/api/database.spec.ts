// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { downloadInstanceImportTemplate, exportInstances, importInstances } from './database'

const transport = vi.hoisted(() => ({ request: vi.fn(), get: vi.fn() }))
vi.mock('./http', () => ({ request: transport.request, default: { get: transport.get } }))
let click: ReturnType<typeof vi.spyOn>
beforeEach(() => {
  vi.clearAllMocks()
  transport.get.mockResolvedValue({ data: new Blob(['xlsx']) })
  transport.request.mockResolvedValue({ success_count: 1, created_count: 1, updated_count: 0, failed_rows: [] })
  URL.createObjectURL = vi.fn(() => 'blob:instance-xlsx')
  URL.revokeObjectURL = vi.fn()
  click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => { click.mockRestore() })

it('模板与全量导出下载xlsx，筛选排序保持原值并排除分页', async () => {
  await downloadInstanceImportTemplate()
  expect(transport.get).toHaveBeenLastCalledWith('/database/instances/import-template', { responseType: 'blob', params: undefined })
  expect((click.mock.instances[0] as HTMLAnchorElement).download).toBe('数据库实例导入模板.xlsx')
  const query = { scope: 'management' as const, engine: 'mysql' as const, instance_id: 7, enabled: false, sort_by: 'version' as const, sort_order: 'desc' as const }
  await exportInstances(query)
  expect(transport.get).toHaveBeenLastCalledWith('/database/instances/export', { responseType: 'blob', params: query })
  expect((click.mock.instances[1] as HTMLAnchorElement).download).toBe('数据库实例列表.xlsx')
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2)
})

it('导入使用multipart file与upsert参数，完整保留新增更新和失败行统计', async () => {
  const file = new File(['xlsx'], '实例.xlsx')
  const value = await importInstances(file, true)
  const request = transport.request.mock.calls[0]![0]
  expect(request).toMatchObject({ url: '/database/instances/import', method: 'post', params: { upsert: true } })
  expect(request.data).toBeInstanceOf(FormData)
  expect(request.data.get('file')).toBe(file)
  expect(value).toEqual({ success_count: 1, created_count: 1, updated_count: 0, failed_rows: [] })
})
