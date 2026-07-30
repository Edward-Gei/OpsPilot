<script setup lang="ts">
// 模板规则只读视图：步骤编排/执行策略/审批规则/通知规则/权限范围（详情抽屉与版本快照回看共用）
import { computed } from 'vue'
import type { TemplateParam, TemplateSnapshot } from '@/api/job'
import CodeEditor from '@/components/CodeEditor.vue'
import {
  approveModeText,
  channelText,
  editorLang,
  eventText,
  receiverLabel,
  scriptTypeOptions,
} from './meta'

const props = defineProps<{
  rule: TemplateSnapshot
  /** 角色 id → 角色名（审批节点/收件人/权限范围展示） */
  roleMap: Record<number, string>
  /** 凭据 id → 凭据名 */
  credMap: Record<number, string>
  /** 目标应用名（父组件按 app_id 解析） */
  appName: string
}>()

const scriptTypeText = Object.fromEntries(scriptTypeOptions.map((o) => [o.value, o.label]))

// 参数定义展示列（含 fixed 固定值语义）
const yesNo = (v: { text: boolean }) => (v.text ? '是' : '否')
const dash = (v: { text: string | null }) => v.text || '—'
const paramCols = [
  { title: '参数名', dataIndex: 'name', width: 120 },
  { title: '显示名', dataIndex: 'label', width: 100, customRender: dash },
  { title: '默认值', dataIndex: 'default', width: 90, customRender: dash },
  { title: '必填', dataIndex: 'required', width: 55, customRender: yesNo },
  { title: '固定值', dataIndex: 'fixed', width: 65, customRender: yesNo },
  { title: '说明', dataIndex: 'description', customRender: dash },
]

// 参数定义汇总：全局参数模式下各步骤同一份，去重后即模板级参数；
// 历史快照（步骤级参数）按提交端 TPL-03 同名合并规则展示，与实际提交表单一致
const mergedParams = computed(() => {
  const merged = new Map<string, TemplateParam>()
  for (const s of props.rule.steps) {
    for (const p of s.params_schema || []) {
      const exist = merged.get(p.name)
      if (exist) {
        if (p.required) exist.required = true
        if (p.fixed) exist.fixed = true
      } else {
        merged.set(p.name, { ...p })
      }
    }
  }
  return [...merged.values()]
})

// 执行策略展示行（快照可能缺省字段，出 —）
const strategyRows = computed(() => {
  const s = props.rule.exec_strategy || {}
  const b = (v: boolean | undefined) => (v === undefined ? '—' : v ? '是' : '否')
  return [
    { label: '并发数', value: s.concurrency ?? '—' },
    { label: '分批大小', value: s.batch_size ? s.batch_size : '不分批' },
    { label: '批间暂停', value: b(s.batch_pause) },
    { label: '超时（秒）', value: s.timeout ?? '—' },
    { label: '失败即停', value: b(s.fail_fast) },
    { label: '终止杀进程', value: b(s.kill_on_stop) },
  ]
})
</script>

<template>
  <a-descriptions :column="2" bordered size="small">
    <a-descriptions-item label="目标应用">{{ appName || '—' }}</a-descriptions-item>
    <a-descriptions-item label="可见角色">
      <template v-if="rule.visible_role_ids.length">
        <a-tag v-for="rid in rule.visible_role_ids" :key="rid" color="blue">{{ roleMap[rid] || rid }}</a-tag>
      </template>
      <span v-else>全部角色</span>
    </a-descriptions-item>
    <a-descriptions-item label="说明" :span="2">{{ rule.description || '—' }}</a-descriptions-item>
  </a-descriptions>

  <!-- 参数定义：对所有步骤生效（历史快照的步骤级参数按同名合并展示） -->
  <template v-if="mergedParams.length">
    <div class="section-title">参数定义（{{ mergedParams.length }}，对所有步骤生效）</div>
    <a-table bordered size="small" :pagination="false" row-key="name"
             :columns="paramCols" :data-source="mergedParams" />
  </template>

  <!-- 步骤编排：脚本内嵌，顺序即执行顺序 -->
  <div class="section-title">步骤编排（{{ rule.steps.length }}）</div>
  <a-collapse>
    <a-collapse-panel v-for="s in rule.steps" :key="s.step_order">
      <template #header>
        <a-tag color="cyan">步骤 {{ s.step_order }}</a-tag>{{ s.name }}
        <a-tag class="step-type">{{ scriptTypeText[s.script_type] || s.script_type }}</a-tag>
      </template>
      <a-descriptions :column="2" bordered size="small">
        <a-descriptions-item label="执行凭据">{{ credMap[s.credential_id] || `#${s.credential_id}` }}</a-descriptions-item>
        <a-descriptions-item label="超时（秒）">{{ s.timeout }}</a-descriptions-item>
      </a-descriptions>
      <div class="sub-title">脚本内容</div>
      <CodeEditor :model-value="s.content" :lang="editorLang(s.script_type)" readonly height="220px" />
    </a-collapse-panel>
  </a-collapse>

  <!-- 执行策略 -->
  <div class="section-title">执行策略</div>
  <a-descriptions :column="3" bordered size="small">
    <a-descriptions-item v-for="r in strategyRows" :key="r.label" :label="r.label">{{ r.value }}</a-descriptions-item>
  </a-descriptions>

  <!-- 审批规则：节点=角色，节点间依次推进 -->
  <div class="section-title">审批规则</div>
  <template v-if="rule.approval_enabled">
    <a-steps size="small" :current="-1" class="node-steps">
      <a-step
        v-for="n in rule.approval_nodes"
        :key="n.node_order"
        :title="roleMap[n.role_id] || `角色#${n.role_id}`"
        :description="approveModeText[n.approve_mode] || n.approve_mode"
      />
    </a-steps>
    <div class="flag-line">
      <a-tag :color="rule.allow_withdraw ? 'green' : 'default'">撤回：{{ rule.allow_withdraw ? '允许' : '禁止' }}</a-tag>
      <a-tag :color="rule.allow_transfer ? 'green' : 'default'">转审（预留）：{{ rule.allow_transfer ? '允许' : '禁止' }}</a-tag>
      <a-tag :color="rule.allow_countersign ? 'green' : 'default'">加签（预留）：{{ rule.allow_countersign ? '允许' : '禁止' }}</a-tag>
    </div>
  </template>
  <a-tag v-else color="orange">免审批：提交后直接进入执行</a-tag>

  <!-- 通知规则 -->
  <div class="section-title">通知规则（{{ rule.notify_rules.length }}）</div>
  <a-empty v-if="!rule.notify_rules.length" description="未配置，按默认策略通知" :image-style="{ height: '48px' }" />
  <a-table
    v-else
    bordered
    size="small"
    :pagination="false"
    row-key="event"
    :columns="[
      { title: '触发事件', key: 'event', width: 130 },
      { title: '收件人', key: 'receivers' },
      { title: '渠道', key: 'channels', width: 200 },
    ]"
    :data-source="rule.notify_rules"
  >
    <template #bodyCell="{ column, record }">
      <template v-if="column.key === 'event'">{{ eventText[record.event] || record.event }}</template>
      <template v-else-if="column.key === 'receivers'">
        <a-tag v-for="r in record.receivers" :key="r">{{ receiverLabel(r, roleMap) }}</a-tag>
      </template>
      <template v-else-if="column.key === 'channels'">
        <a-tag v-for="c in record.channels" :key="c" color="blue">{{ channelText[c] || c }}</a-tag>
      </template>
    </template>
  </a-table>
</template>

<style scoped>
.section-title {
  font-weight: 600;
  margin: 18px 0 10px;
}
.sub-title {
  font-weight: 600;
  font-size: 13px;
  margin: 12px 0 8px;
}
.step-type {
  margin-left: 8px;
}
.node-steps {
  margin: 6px 0 12px;
}
.flag-line {
  display: flex;
  gap: 4px;
}
</style>
