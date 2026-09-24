/**
 * 图谱组件之间共享的小工具。
 *
 * 原先 `EntityGraph.vue` 与 `OverviewGraph.vue` 各写一份 `loadNodeRelations`（逐字重复，
 * 只有容错写法不同），`KgGraph.vue` 与问答页各自实现过节点显示名。这里收敛为单一来源。
 */

/** 把「节点关系」接口的返回合并进现有图数据（按 id / from-to-text 去重）。 */
export function mergeNodeRelations(
  current: { nodes?: any[]; lines?: any[] },
  incoming: { nodes?: any[]; lines?: any[] },
) {
  const currentNodes = new Set((current.nodes || []).map((node: any) => String(node.id)))
  const currentLines = new Set(
    (current.lines || []).map((line: any) => lineKey(line)),
  )

  const newNodes = (incoming.nodes || []).filter((node: any) => !currentNodes.has(String(node.id)))
  const newLines = (incoming.lines || []).filter((line: any) => !currentLines.has(lineKey(line)))

  return {
    nodes: [...(current.nodes || []), ...newNodes],
    lines: [...(current.lines || []), ...newLines],
  }
}

/** 连线的去重键：后端有 from/to 与 source/target 两种写法，text 可能缺失。 */
function lineKey(line: any): string {
  return `${line.from || line.source}-${line.to || line.target}-${line.text || ''}`
}

/**
 * ECharts 图谱的分类名（图例/配色按它分组）。
 *
 * 与 `utils/knowledge.ts` 的 `typeLabel`（详情页用的"参战组织"）**刻意不同名**：
 * 图谱侧历史上一直用「势力组织」，`KgGraph.vue` 的配色表也以它作键。
 * 要统一两套措辞，得连着配色表与存量截图一起改，属于独立议题。
 */
export const graphCategoryLabelMap: Record<string, string> = {
  Event: '战争事件',
  事件: '战争事件',
  Organization: '势力组织',
  势力组织: '势力组织',
  Person: '历史人物',
  历史人物: '历史人物',
  Place: '战争地点',
  战争地点: '战争地点',
}

export const graphCategoryLabel = (type?: string) =>
  graphCategoryLabelMap[String(type || '').trim()] || String(type || '').trim()
