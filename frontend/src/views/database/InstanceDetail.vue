<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as db from '@/api/database'
import { useUserStore } from '@/stores/user'
import { engineNames, errorText, timeText } from './runtime'
import './database.css'

const props = defineProps<{ instanceId: number }>()
const emit = defineEmits<{ close: [] }>()
const user = useUserStore()
const deployText: Record<string, { text: string; color: string }> = {
  shell: { text: 'Shell', color: 'orange' },
  docker: { text: 'Docker', color: 'geekblue' },
  k8s: { text: 'K8s', color: 'purple' }
}
const envColors: Record<string, string> = { demo: 'green', stage: 'orange', prod: 'red' }
const id = computed(() => props.instanceId),
  detail = ref<db.InstanceDetail | null>(null)
const loading = ref(false),
  error = ref('')
const roles = ref<db.Relation[]>([]),
  rolesLoaded = ref(false)
let generation = 0

async function loadDetail() {
  const g = ++generation, targetId = id.value
  detail.value = null
  error.value = ''
  loading.value = true
  roles.value = []
  rolesLoaded.value = false
  try {
    const data = await db.getInstance(targetId)
    if (g !== generation) return
    detail.value = data
    if (user.hasPerm('database:grant')) {
      const granted = await db.getInstanceRoles(targetId)
      if (g === generation) {
        roles.value = granted.items
        rolesLoaded.value = true
      }
    }
  } catch (e) {
    if (g === generation) error.value = errorText(e)
  } finally {
    if (g === generation) loading.value = false
  }
}

watch(id, loadDetail)
onMounted(loadDetail)
onBeforeUnmount(() => {
  generation++
})
</script>
<template>
  <a-drawer :open="true" :title="detail?.name || '实例详情'" :width="650" @close="emit('close')">
    <a-alert v-if="error" :message="error" type="error" show-icon>
      <template #action>
        <a-button size="small" @click="loadDetail">重试</a-button>
      </template>
    </a-alert>
    <a-spin :spinning="loading">
      <template v-if="detail">
        <a-descriptions :column="1" bordered size="small" class="op-desc-table">
          <a-descriptions-item label="实例名称">{{detail.name}}</a-descriptions-item>
          <a-descriptions-item label="数据库引擎">{{engineNames[detail.engine]}}</a-descriptions-item>
          <a-descriptions-item label="环境"><a-tag>{{detail.environment}}</a-tag></a-descriptions-item>
          <a-descriptions-item label="业务线">{{detail.business_line||'—'}}</a-descriptions-item>
          <a-descriptions-item label="登记状态">
            <a-tag :color="detail.enabled?'success':'default'">{{detail.enabled?'启用':'停用'}}</a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="集群名称">{{detail.cluster_name||'—'}}</a-descriptions-item>
          <a-descriptions-item label="集群角色">{{detail.cluster_role||'—'}}</a-descriptions-item>
          <a-descriptions-item label="登记版本">{{detail.version||'—'}}</a-descriptions-item>
          <a-descriptions-item label="统一入口">{{detail.host.includes(':') && !detail.host.startsWith('[') ? '['+detail.host+']' : detail.host}}:{{detail.port}}</a-descriptions-item>
          <a-descriptions-item label="默认库">{{detail.connection_options.default_database ?? detail.connection_options.database ?? '—'}}</a-descriptions-item>
          <a-descriptions-item label="只读凭据"><template v-if="detail.credential">{{detail.credential.name}}（{{detail.credential.login_user||'仅口令'}}）</template><span v-else>未绑定只读凭据 · 待配置</span></a-descriptions-item>
          <a-descriptions-item label="TLS">
            <a-tag :color="detail.connection_options.tls?'success':'default'">{{detail.connection_options.tls?'开启':'关闭'}}</a-tag>
            <span v-if="detail.connection_options.tls">{{detail.connection_options.tls_verify?'验证证书及主机名':'加密但不验证证书'}}</span>
          </a-descriptions-item>
          <a-descriptions-item label="查询角色">
            <template v-if="user.hasPerm('database:grant')">
              <a-space v-if="roles.length" wrap><a-tag v-for="role in roles" :key="role.id">{{role.name}}</a-tag></a-space>
              <span v-else>{{rolesLoaded?'尚未授权查询角色':loading?'加载中':'角色加载失败'}}</span>
            </template>
            <span v-else>—</span>
          </a-descriptions-item>
          <a-descriptions-item label="描述">{{detail.description||'—'}}</a-descriptions-item>
          <a-descriptions-item label="创建时间">{{timeText(detail.created_at)}}</a-descriptions-item>
        </a-descriptions>

        <div class="detail-hosts-title">
          <span>关联应用（{{detail.apps.length}}）</span>
        </div>
        <a-empty v-if="!detail.apps.length" description="暂无关联应用" />
        <a-table v-else bordered :columns="[{title:'应用名',dataIndex:'name',key:'name'},{title:'语言',dataIndex:'language',key:'language',width:90},{title:'部署方式',dataIndex:'deploy_type',key:'deploy_type',width:90}]"
          :data-source="detail.apps" row-key="id" size="small" :pagination="false">
          <template #bodyCell="{column,record}">
            <template v-if="column.key==='name'">
              <a v-if="user.hasPerm('cmdb:read')" :href="'/cmdb/apps?id='+record.id+'&related=database'">{{record.name}}</a>
              <span v-else>{{record.name}}</span>
            </template>
            <a-tag v-else-if="column.key==='deploy_type'&&record.deploy_type" :color="deployText[record.deploy_type]?.color">
              {{deployText[record.deploy_type]?.text||record.deploy_type}}
            </a-tag>
            <template v-else>{{record[column.dataIndex]||'—'}}</template>
          </template>
        </a-table>

        <div class="detail-hosts-title">承载主机（{{detail.hosts.length}}）</div>
        <a-empty v-if="!detail.hosts.length" description="未关联 CMDB 主机" />
        <a-table v-else bordered :columns="[{title:'主机名',dataIndex:'hostname',key:'hostname'},{title:'IP 地址',dataIndex:'ip',key:'ip',width:130},{title:'环境',dataIndex:'environment',key:'environment',width:80}]"
          :data-source="detail.hosts" row-key="id" size="small" :pagination="false">
          <template #bodyCell="{column,record}">
            <template v-if="column.key==='hostname'">
              <a v-if="user.hasPerm('cmdb:read')" :href="'/cmdb/hosts?id='+record.id+'&related=database'">{{record.hostname}}</a>
              <span v-else>{{record.hostname}}</span>
            </template>
            <a-tag v-else-if="column.key==='environment'&&record.environment" :color="envColors[record.environment]">{{record.environment}}</a-tag>
            <template v-else>{{record[column.dataIndex]||'—'}}</template>
          </template>
        </a-table>

        <div class="detail-hosts-title">最近连接测试</div>
        <a-descriptions :column="1" bordered size="small" class="op-desc-table">
          <a-descriptions-item label="连接状态">{{detail.last_probe_status==='succeeded'?'通过':detail.last_probe_status==='failed'?'失败':'尚未检测'}}</a-descriptions-item>
          <a-descriptions-item label="检测时间">{{timeText(detail.last_probe_at)}}</a-descriptions-item>
          <a-descriptions-item label="摘要">{{detail.last_probe_message||'尚无安全摘要'}}</a-descriptions-item>
        </a-descriptions>
      </template>
    </a-spin>
  </a-drawer>
</template>

<style scoped>
.detail-hosts-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  margin: 18px 0 10px;
}
</style>
