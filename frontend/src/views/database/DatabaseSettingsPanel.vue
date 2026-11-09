<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { DatabaseOutlined, DownOutlined } from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import { updateConfigs } from '@/api/system'

const props = defineProps<{
  configs: Record<string, unknown> | null
}>()
defineEmits<{
  reload: []
}>()
const collapsed = ref(false),
  saving = ref(false),
  error = ref(''),
  values = reactive<Record<string, string>>({}),
  saved = reactive<Record<string, number>>({})
const fields = [
  {
    key: 'execution_timeout_seconds',
    label: '查询执行总时限',
    unit: '秒',
    min: 1,
    max: 600,
    tip: '后续开始任务使用新值，运行任务沿用原截止时间'
  },
  {
    key: 'max_result_rows',
    label: '单次结果记录数',
    unit: '条',
    min: 1,
    max: 10000,
    tip: '与字节上限同时限制，任务开始时快照'
  },
  {
    key: 'max_result_bytes',
    label: '单次结果总大小',
    unit: '字节',
    min: 65536,
    max: 16777216,
    tip: '以整数字节保存，默认 5242880 字节（5 MiB）'
  },
  {
    key: 'result_ttl_minutes',
    label: '结果保存时间',
    unit: '分钟',
    min: 1,
    max: 1440,
    tip: '任务开始时快照，完成后计算到期'
  },
  {
    key: 'history_retention_days',
    label: '查询历史保留时间',
    unit: '天',
    min: 1,
    max: 365,
    tip: '按当前配置独立清理，不清理未终态任务'
  },
  {
    key: 'audit_retention_days',
    label: '独立执行审计保留时间',
    unit: '天',
    min: 1,
    max: 1095,
    tip: '按独立事件组清理，与查询历史分别设置'
  },
]
watch(() => props.configs, cfg => {
  for (const field of fields) {
    const value = cfg?.[`database.${field.key}`]
    values[field.key] = typeof value === 'number' ? String(value) : ''
    if (typeof value === 'number') saved[field.key] = value
    else delete saved[field.key]
  }
}, { immediate: true })

const hasChanges = computed(() => fields.some(field => Number(values[field.key]) !== saved[field.key]))

/** 六项先整体校验，再以一次请求保存改动；确认和提交共用锁，避免重复写入。 */
async function save() {
  if (saving.value || !props.configs) return
  const changes: Record<string, number> = {}
  let retentionReduced = false
  for (const field of fields) {
    const raw = values[field.key], value = Number(raw)
    if (!/^\d+$/.test(raw) || !Number.isSafeInteger(value) || value < field.min || value > field.max) {
      error.value = `${field.label}须为 ${field.min}～${field.max} 的整数`
      return
    }
    if (value !== saved[field.key]) changes[`database.${field.key}`] = value
    if (field.key.endsWith('retention_days') && value < saved[field.key]) retentionReduced = true
  }
  error.value = ''
  if (!Object.keys(changes).length) return
  saving.value = true
  const write = async () => {
    try {
      await updateConfigs(changes)
      for (const field of fields) {
        const value = changes[`database.${field.key}`]
        if (value !== undefined) saved[field.key] = value
      }
      message.success('数据库配置已保存')
    } catch (e) {
      error.value = e instanceof Error ? e.message : '配置保存失败'
    } finally {
      saving.value = false
    }
  }
  if (retentionReduced) Modal.confirm({ title: '缩短保留时间？', content: '后续清理将删除已有超期记录，历史与审计分别执行。本次全部改动将在确认后一起保存。', onOk: write, onCancel: () => { saving.value = false } })
  else await write()
}
</script>
<template>
  <a-card class="db-settings-panel" data-testid="database-settings">
    <div class="db-settings-head" :class="{'is-collapsed':collapsed}" @click="collapsed=!collapsed">
      <div class="op-icon-grad db-settings-icon"><DatabaseOutlined /></div>
      <div class="db-settings-title">
        <b>数据库查询策略</b>
        <span>查询执行、结果限制与历史审计保留时间</span>
      </div>
      <a-button type="primary" :loading="saving" :disabled="!configs||saving||!hasChanges" @click.stop="save">保存</a-button>
      <a-button type="text" class="db-settings-toggle" :aria-expanded="!collapsed" aria-label="展开或收起数据库设置">
        <DownOutlined class="db-settings-collapse-icon" />
      </a-button>
    </div>
    <div v-show="!collapsed">
      <a-alert v-if="!configs" type="error" message="数据库配置尚未加载，无法保存前端默认值。">
        <template #action>
          <a-button size="small" @click="$emit('reload')">重新加载</a-button>
        </template>
      </a-alert>
      <a-alert v-if="error" type="error" :message="error" show-icon />
      <div class="db-settings-grid">
        <div v-for="field in fields" :key="field.key" class="db-settings-field">
          <label :for="`db-setting-${field.key}`">{{field.label}}</label>
          <div class="db-settings-control">
            <a-input :id="`db-setting-${field.key}`" v-model:value="values[field.key]" inputmode="numeric"
              :disabled="!configs||saving" />
            <span>{{field.unit}}</span>
          </div>
          <div class="db-settings-tip">
            {{field.tip}}
            <span v-if="field.key==='max_result_bytes' && values[field.key]">
              · 当前 {{(Number(values[field.key])/1048576).toFixed(2)}} MiB</span>
          </div>
        </div>
      </div>
    </div>
  </a-card>
</template>
<style scoped>
.db-settings-panel {
  --block-accent: #4b7bf2;
  grid-column: 1/-1;
  min-width: 0;
  border-radius: 14px;
  background: linear-gradient(145deg,color-mix(in srgb,var(--block-accent) 6%,var(--bg-card)),var(--bg-card) 65%);
  border-color: color-mix(in srgb,var(--block-accent) 18%,var(--border));
  box-shadow: 0 5px 18px color-mix(in srgb,var(--block-accent) 5%,transparent);
}

.db-settings-panel :deep(.ant-card-body) {
  padding: 20px;
}

.db-settings-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  cursor: pointer;
  user-select: none;
}

.db-settings-head.is-collapsed {
  margin-bottom: 0;
}

.db-settings-icon {
  flex-shrink: 0;
  color: var(--block-accent);
  background: color-mix(in srgb,var(--block-accent) 13%,transparent);
  border: 1px solid color-mix(in srgb,var(--block-accent) 16%,transparent);
  box-shadow: none;
}

.db-settings-title {
  flex: 1;
  min-width: 0;
}

.db-settings-title b {
  display: block;
  font-size: 15px;
  color: var(--text-1);
}

.db-settings-title b::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 12px;
  margin-right: 7px;
  border-radius: 2px;
  background: var(--block-accent);
  vertical-align: -1px;
}

.db-settings-title span {
  display: block;
  font-size: 12px;
  color: var(--text-3);
  margin-top: 4px;
}

.db-settings-toggle {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  padding: 0;
  color: var(--text-3);
}

.db-settings-collapse-icon {
  transition: transform .2s ease;
}

.db-settings-head.is-collapsed .db-settings-collapse-icon {
  transform: rotate(-90deg);
}

.db-settings-grid {
  display: grid;
  grid-template-columns: repeat(2,minmax(0,1fr));
  gap: 12px;
}

.db-settings-field {
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid color-mix(in srgb,var(--block-accent) 11%,var(--border));
  border-radius: 10px;
  background: color-mix(in srgb,var(--block-accent) 3%,var(--bg-card));
}

.db-settings-field>label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-2);
  margin-bottom: 10px;
}

.db-settings-control {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-2);
  font-size: 13px;
}

.db-settings-control :deep(.ant-input) {
  flex: 0 1 180px;
  min-width: 0;
  width: 180px;
}

.db-settings-control>span {
  flex-shrink: 0;
}

.db-settings-tip {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-3);
  overflow-wrap: anywhere;
}

@media(min-width:1450px) {
  .db-settings-grid {
    grid-template-columns: repeat(3,minmax(0,1fr));
  }
}

@media(max-width:900px) {
  .db-settings-grid {
    grid-template-columns: minmax(0,1fr);
  }
}
</style>
