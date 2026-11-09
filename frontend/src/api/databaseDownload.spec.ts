// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Blob as NativeBlob } from 'node:buffer'
import axios, { AxiosError, AxiosHeaders, type AxiosAdapter } from 'axios'
import { message } from 'ant-design-vue'
import http from './http'
import { downloadInstanceImportTemplate, exportInstances } from './database'

const session = vi.hoisted(() => ({ access: 'access-1', refresh: 'refresh-1', clear: vi.fn() }))
vi.mock('./token', () => ({
  getAccessToken: () => session.access,
  getRefreshToken: () => session.refresh,
  getSessionId: () => session.access ? 'download-session' : '',
  expireIfNeeded: () => false,
  waitForTokenChange: async () => false,
  setTokens: (access: string, refresh: string) => { session.access = access; session.refresh = refresh },
  clearTokens: (broadcast: boolean) => { session.clear(broadcast); session.access = ''; session.refresh = '' },
}))
vi.mock('ant-design-vue', () => ({ message: { error: vi.fn() } }))

const originalAdapter = http.defaults.adapter
const originalCreateObjectURL = URL.createObjectURL
const originalRevokeObjectURL = URL.revokeObjectURL

beforeEach(() => {
  vi.clearAllMocks()
  session.access = 'access-1'
  session.refresh = 'refresh-1'
  // jsdom 未实现 Blob.text；使用浏览器同等 API 的原生 Blob，保留真实 Axios 拦截链。
  vi.stubGlobal('Blob', NativeBlob)
  vi.stubGlobal('window', { location: { pathname: '/database/instances', href: '/database/instances' } })
  URL.createObjectURL = vi.fn(() => 'blob:download')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => {
  http.defaults.adapter = originalAdapter
  URL.createObjectURL = originalCreateObjectURL
  URL.revokeObjectURL = originalRevokeObjectURL
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function rejectBlob(code: number, status = 401, content = JSON.stringify({ code, message: '下载被拒绝', data: { reason: 'permission' } }), type = 'application/json') {
  const adapter: AxiosAdapter = async config => {
    throw new AxiosError('HTTP request failed', 'ERR_BAD_REQUEST', config, undefined, {
      status, statusText: 'Rejected', config, headers: new AxiosHeaders({ 'content-type': type }), data: new Blob([content], { type }),
    })
  }
  return adapter
}

it.each(['template', 'export'])('真实Axios收到%s下载JSON Blob 40102后刷新并只重放一次，成功才保存xlsx', async kind => {
  const binary = new Blob(['PK-xlsx'], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
  const refresh = vi.spyOn(axios, 'post').mockResolvedValue({ data: { code: 0, data: { access_token: 'access-2', refresh_token: 'refresh-2' } } })
  const adapter = vi.fn<AxiosAdapter>().mockImplementationOnce(rejectBlob(40102)).mockImplementation(async config => ({
    status: 200, statusText: 'OK', headers: new AxiosHeaders(), config, data: binary,
  }))
  http.defaults.adapter = adapter
  const download = kind === 'template' ? downloadInstanceImportTemplate() : exportInstances({ scope: 'management', keyword: 'mysql', enabled: false, sort_by: 'name', sort_order: 'desc' })
  await download
  expect(refresh).toHaveBeenCalledOnce()
  expect(adapter).toHaveBeenCalledTimes(2)
  const retried = adapter.mock.calls[1]![0]
  expect(retried.headers.Authorization).toBe('Bearer access-2')
  expect(retried.responseType).toBe('blob')
  if (kind === 'export') expect(retried.params).toMatchObject({ keyword: 'mysql', enabled: false, sort_by: 'name', sort_order: 'desc' })
  expect(URL.createObjectURL).toHaveBeenCalledOnce()
  expect(URL.createObjectURL).toHaveBeenCalledWith(binary)
  expect(message.error).not.toHaveBeenCalled()
  expect(session.clear).not.toHaveBeenCalled()
})

it.each([40101, 40301])('JSON Blob %s保留业务错误与提示，未授权才清理会话，不保存错误文件', async code => {
  const refresh = vi.spyOn(axios, 'post')
  http.defaults.adapter = rejectBlob(code, code === 40101 ? 401 : 403)
  await expect(exportInstances({ scope: 'management' })).rejects.toMatchObject({ code, message: '下载被拒绝', data: { reason: 'permission' } })
  expect(message.error).toHaveBeenCalledOnce()
  expect(message.error).toHaveBeenCalledWith('下载被拒绝')
  expect(refresh).not.toHaveBeenCalled()
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  expect(session.clear.mock.calls).toEqual(code === 40101 ? [[false]] : [])
  expect(window.location.href).toBe(code === 40101 ? '/login' : '/database/instances')
})

it('刷新后下载仍返回Blob 40102时不循环重放，清理会话且不下载', async () => {
  const refresh = vi.spyOn(axios, 'post').mockResolvedValue({ data: { code: 0, data: { access_token: 'access-2', refresh_token: 'refresh-2' } } })
  const adapter = vi.fn<AxiosAdapter>().mockImplementation(rejectBlob(40102))
  http.defaults.adapter = adapter
  await expect(downloadInstanceImportTemplate()).rejects.toMatchObject({ code: 40102 })
  expect(refresh).toHaveBeenCalledOnce()
  expect(adapter).toHaveBeenCalledTimes(2)
  expect(session.clear).toHaveBeenCalledOnce()
  expect(session.clear).toHaveBeenCalledWith(false)
  expect(window.location.href).toBe('/login')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

it.each([
  { content: 'invalid-json', type: 'application/json' },
  { content: '<html>Bad Gateway</html>', type: 'text/html' },
])('无法解析的下载错误 $type 保留传输错误，不触发认证流程', async ({ content, type }) => {
  const refresh = vi.spyOn(axios, 'post')
  http.defaults.adapter = rejectBlob(0, 502, content, type)
  await expect(downloadInstanceImportTemplate()).rejects.toMatchObject({ message: 'HTTP request failed' })
  expect(message.error).toHaveBeenCalledOnce()
  expect(message.error).toHaveBeenCalledWith('HTTP request failed')
  expect(refresh).not.toHaveBeenCalled()
  expect(session.clear).not.toHaveBeenCalled()
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
