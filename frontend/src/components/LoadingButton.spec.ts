// @vitest-environment jsdom
import { expect, it } from 'vitest'
import { baseParse, compile, NodeTypes, type ElementNode, type RootNode, type TemplateChildNode } from '@vue/compiler-dom'
import { parse } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { computed, defineComponent, type RenderFunction } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { Button } from 'ant-design-vue'
import * as Icons from '@ant-design/icons-vue'

const sources = import.meta.glob('../views/**/*.vue', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

function walk(node: RootNode | TemplateChildNode, visit: (element: ElementNode) => void) {
  if (node.type === NodeTypes.ELEMENT) visit(node)
  if (node.type === NodeTypes.ROOT || node.type === NodeTypes.ELEMENT) node.children.forEach(child => walk(child, visit))
}

const buttons = Object.entries(sources).flatMap(([filename, source]) => {
  const template = parse(source).descriptor.template
  if (!template) return []
  const cases: { name: string; content: string; icons: string[] }[] = []
  walk(baseParse(template.content), node => {
    if (node.tag !== 'a-button' || !node.props.some(prop => prop.type === NodeTypes.DIRECTIVE && prop.name === 'bind' && prop.arg?.type === NodeTypes.SIMPLE_EXPRESSION && prop.arg.content === 'loading')) return
    const icons: string[] = []
    walk(node, child => { if (/(Outlined|Filled|TwoTone)$/.test(child.tag)) icons.push(child.tag) })
    if (!icons.length) return
    const line = source.slice(0, template.loc.start.offset + node.loc.start.offset).split('\n').length
    cases.push({ name: `${filename}:${line}`, content: node.children.map(child => child.loc.source).join('').replace(/{{[\s\S]*?}}/g, '记录'), icons })
  })
  return cases
})

// 重放真实按钮的子模板；仅移除页面业务条件，交由真实 Ant Button 切换加载图标。
it.each(buttons)('加载按钮 $name 在加载时仅显示spinner，完成后恢复原图标', async ({ content, icons }) => {
  const { code } = compile(`<a-button :loading="loading">${content}</a-button>`, { mode: 'function', prefixIdentifiers: true })
  const wrapper = mount(defineComponent({
    props: { loading: Boolean },
    components: { AButton: Button, ...Object.fromEntries(icons.map(name => [name, Icons[name as keyof typeof Icons]])) },
    setup: props => ({ exporting: computed(() => props.loading) }),
    render: new Function('Vue', code)(Vue) as RenderFunction,
  }), { props: { loading: false } })
  try {
    const original = wrapper.findAll('.anticon').map(icon => icon.attributes('aria-label'))
    expect(original).toHaveLength(icons.length)
    expect(wrapper.find('.anticon-loading').exists()).toBe(false)
    await wrapper.setProps({ loading: true })
    await flushPromises()
    expect(wrapper.findAll('.anticon-loading')).toHaveLength(1)
    expect(wrapper.findAll('.anticon')).toHaveLength(1)
    await wrapper.setProps({ loading: false })
    await flushPromises()
    expect(wrapper.find('.anticon-loading').exists()).toBe(false)
    expect(wrapper.findAll('.anticon').map(icon => icon.attributes('aria-label'))).toEqual(original)
  } finally {
    wrapper.unmount()
  }
})
