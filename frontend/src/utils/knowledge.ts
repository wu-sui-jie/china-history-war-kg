export const typeLabelMap: Record<string, string> = {
  Event: '战争事件',
  Place: '战争地点',
  Organization: '参战组织',
  Person: '历史人物',
}

export const fieldLabelMap: Record<string, string> = {
  id: '编号',
  type: '类型',
  EventName: '事件名称',
  EventType: '事件类型',
  StartDate: '开始时间',
  EndDate: '结束时间',
  DynastyName: '所属朝代',
  Place: '地点',
  Aggressor: '发起方',
  Defender: '防守方',
  KeyPersons: '关键人物',
  Action: '主要行为',
  Result: '事件结果',
  TroopSize: '规模',
  Impact: '历史影响',
  source_text: '来源原文',
  relations: '关联关系',
  Remark: '备注',
  geo_name: '历史地名',
  modern_name: '现代地名',
  Province: '省',
  City: '市',
  District_County: '区县',
  Specific_location: '具体位置',
  longitude: '经度',
  latitude: '纬度',
  coord_source: '坐标来源',
  coord_confidence: '坐标置信度',
  coord_note: '坐标说明',
  OrgName: '组织名称',
  OrgType: '组织类型',
  Description: '简介',
  PersonName: '人物名称',
  Role: '角色',
}

export const fieldValueLabelMap: Record<string, Record<string, string>> = {
  coord_confidence: {
    high: '高',
    medium: '中',
    low: '低',
  },
  coord_source: {
    manual: '人工确认',
    literature: '文献考证',
    map_tool: '地图工具',
    extraction: '数据抽取',
    city_centroid: '城市中心点',
    province_centroid: '省级中心点',
    historical_region: '历史区域估算',
    historical_region_fuzzy: '历史区域模糊估算',
    unresolved: '未解析',
  },
}

export const problemLabelMap: Record<string, string> = {
  missing_start_date: '缺少开始时间',
  missing_end_date: '缺少结束时间',
  end_before_start: '结束时间早于开始时间',
  invalid_start_date: '开始时间格式异常',
  invalid_end_date: '结束时间格式异常',
}

// 类型别名：接口与历史数据里出现过的各种写法 → 四种标准类型。
// 2026-09-25（FE-13）：原先还认五个 GBK 乱码 key（如 鎴樹簤浜嬩欢 = 战争事件）——
// 那是把"数据源头按 GBK 读 UTF-8"的问题藏在了展示层。已核实当前 SQLite 与 Neo4j
// 里没有乱码值，改由后端在导入/同步写库前统一复原（common_utils.repair_mojibake），
// 这里不再兼容乱码。
export const typeAliasMap: Record<string, string> = {
  Event: 'Event',
  event: 'Event',
  事件: 'Event',
  战争事件: 'Event',
  Place: 'Place',
  place: 'Place',
  地点: 'Place',
  战争地点: 'Place',
  Person: 'Person',
  person: 'Person',
  人物: 'Person',
  历史人物: 'Person',
  Organization: 'Organization',
  organization: 'Organization',
  组织: 'Organization',
  势力组织: 'Organization',
  参战组织: 'Organization',
}

export const normalizeType = (type?: string) => typeAliasMap[String(type || '').trim()] || String(type || '').trim()

export const typeLabel = (type?: string) => typeLabelMap[normalizeType(type)] || type || '-'

export const fieldLabel = (field?: string) => fieldLabelMap[field || ''] || '未命名属性'

export const fieldValueLabel = (field: string, value: any) => {
  if (value === null || value === undefined || value === '') return '-'
  const text = String(value)
  return fieldValueLabelMap[field]?.[text] || text
}

export const problemLabel = (problem?: string) => problemLabelMap[problem || ''] || problem || '-'

export const nodeDisplayName = (node: Record<string, any> = {}) =>
  node.EventName || node.PersonName || node.OrgName || node.geo_name || node.name || node.label || node.id || '未命名实体'

export const toEditableFields = (node: Record<string, any>) => {
  const hiddenKeys = new Set(['id', 'neo4j_id', 'created_at', 'updated_at', 'type'])
  return Object.entries(node || {}).filter(([key]) => !hiddenKeys.has(key))
}

/** 把节点 relations 字段（数组 / JSON 字符串 / 单个对象）按关系类型分组。
 *  原先在 inference/index.vue 与 knowledge/EntityDetail.vue 各写一份，逐字相同。 */
export const groupRelationAttributes = (rawValue: any) => {
  if (!rawValue) return []

  let relationItems: any[] = []
  if (Array.isArray(rawValue)) {
    relationItems = rawValue
  } else if (typeof rawValue === 'string') {
    try {
      const parsed = JSON.parse(rawValue)
      relationItems = Array.isArray(parsed) ? parsed : [parsed]
    } catch {
      return []
    }
  } else if (typeof rawValue === 'object') {
    relationItems = [rawValue]
  }

  const grouped = new Map<string, { relation: string; targets: Set<string>; evidences: Set<string> }>()

  relationItems.forEach((item) => {
    if (!item || typeof item !== 'object') return

    const relation = String(item.type || item.relation || item.label || '未标注关系').trim()
    const targets = [
      item.to,
      item.target,
      item.object,
      ...(Array.isArray(item.targets) ? item.targets : []),
      ...(Array.isArray(item.objects) ? item.objects : []),
    ]
      .flat()
      .map((value) => String(value || '').trim())
      .filter(Boolean)

    const evidences = [item.evidence, ...(Array.isArray(item.evidences) ? item.evidences : [])]
      .flat()
      .map((value) => String(value || '').trim())
      .filter(Boolean)

    if (!grouped.has(relation)) {
      grouped.set(relation, { relation, targets: new Set<string>(), evidences: new Set<string>() })
    }

    const current = grouped.get(relation)!
    targets.forEach((target) => current.targets.add(target))
    evidences.forEach((evidence) => current.evidences.add(evidence))
  })

  return Array.from(grouped.values()).map((item) => ({
    relation: item.relation,
    targets: Array.from(item.targets),
    evidences: Array.from(item.evidences),
  }))
}
