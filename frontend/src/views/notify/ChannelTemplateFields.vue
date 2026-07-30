<script setup lang="ts">
// 渠道消息模板编辑区（M6 增强）：标题/正文模板 + 可点击插入的变量标签
// props.config 为父组件 channelForms.xxx.config 的引用，直接改动即保持响应式
import { ref } from 'vue'

const props = defineProps<{ config: Record<string, any> }>()

/** 支持的模板变量（与后端 emit 基础变量 + 业务变量同口径） */
const templateVars: Array<{ key: string; label: string }> = [
  { key: 'event', label: '事件名称' },
  { key: 'ticket_no', label: '工单号' },
  { key: 'ticket_title', label: '工单标题' },
  { key: 'app_name', label: '应用名' },
  { key: 'creator', label: '创建人' },
  { key: 'node', label: '审批节点' },
  { key: 'role', label: '审批角色' },
  { key: 'approver', label: '审批人' },
  { key: 'comment', label: '审批意见' },
  { key: 'reason', label: '中断原因' },
  { key: 'detail', label: '详情' },
  { key: 'receiver', label: '收件人' },
  { key: 'time', label: '触发时间' },
  { key: 'ref_id', label: '工单ID' },
  { key: 'default_title', label: '默认标题' },
  { key: 'default_content', label: '默认正文' },
]

/** 最近聚焦的模板输入框：点击变量标签时插到对应字段末尾 */
const lastFocus = ref<'title' | 'content'>('content')

/** 点击变量标签：把 {变量} 追加到最近聚焦的模板输入框 */
function insertVar(key: string) {
  const field = lastFocus.value === 'title' ? 'title_template' : 'content_template'
  props.config[field] = (props.config[field] || '') + `{${key}}`
}
</script>

<template>
  <div class="tpl-fields">
    <div class="tpl-head">消息模板<span>（选填，该渠道所有事件共用）</span></div>
    <div class="grid2">
      <a-form-item label="标题模板" class="tpl-span2">
        <a-input
          v-model:value="config.title_template"
          :maxlength="200"
          placeholder="留空使用系统默认标题，如：【{event}】{ticket_no} {ticket_title}"
          @focus="lastFocus = 'title'"
        />
      </a-form-item>
      <a-form-item label="正文模板" class="tpl-span2">
        <a-textarea
          v-model:value="config.content_template"
          :maxlength="2000"
          :rows="3"
          placeholder="留空使用系统默认正文，如：{default_content}（审批人：{approver}）"
          @focus="lastFocus = 'content'"
        />
      </a-form-item>
    </div>
    <div class="tpl-vars">
      <a-tag
        v-for="v in templateVars"
        :key="v.key"
        class="tpl-var"
        @click="insertVar(v.key)"
      >{{ '{' + v.key + '}' }} {{ v.label }}</a-tag>
    </div>
    <div class="field-tip">
      点击变量标签插入占位符（插入到最近点击的模板输入框）；留空使用系统默认文案；未知或缺失的变量将原样保留。
      模板效果可通过「测试发送」按钮用示例工单数据预览。
    </div>
  </div>
</template>

<style scoped>
.tpl-fields {
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px dashed var(--border-color, #e5e7eb);
}
.tpl-head {
  font-weight: 600;
  margin-bottom: 12px;
}
.tpl-head span {
  font-weight: 400;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 16px;
}
.tpl-span2 {
  grid-column: span 2;
}
.tpl-vars {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 4px;
  margin-bottom: 8px;
}
.tpl-var {
  cursor: pointer;
  user-select: none;
}
.tpl-var:hover {
  color: var(--ant-color-primary, #1677ff);
  border-color: var(--ant-color-primary, #1677ff);
}
.field-tip {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
  line-height: 1.7;
}
</style>
