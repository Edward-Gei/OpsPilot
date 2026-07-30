// 表格通用工具：列宽拖拽（三个管理列表页共用，避免重复代码）
import type { TableColumnType } from 'ant-design-vue'

/**
 * a-table @resizeColumn 回调：把拖拽后的宽度写回列定义。
 * 使用要求：columns 必须是响应式数组（ref/reactive），且列带 number 型 width + resizable: true。
 */
export function onResizeColumn(width: number, column: TableColumnType) {
  column.width = width
}

/** 给非固定列批量开启拖拽调宽（固定列/操作列保持固定宽度不参与拖拽） */
export function makeResizable<T extends TableColumnType>(columns: T[]): T[] {
  return columns.map((col) =>
    col.fixed ? col : { ...col, resizable: true, minWidth: 80, maxWidth: 480 },
  )
}
