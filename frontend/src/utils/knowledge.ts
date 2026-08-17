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

export const typeAliasMap: Record<string, string> = {
  Event: 'Event',
  event: 'Event',
  事件: 'Event',
  战争事件: 'Event',
  鎴樹簤浜嬩欢: 'Event',
  Place: 'Place',
  place: 'Place',
  地点: 'Place',
  战争地点: 'Place',
  鎴樹簤鍦扮偣: 'Place',
  Person: 'Person',
  person: 'Person',
  人物: 'Person',
  历史人物: 'Person',
  鍘嗗彶浜虹墿: 'Person',
  Organization: 'Organization',
  organization: 'Organization',
  组织: 'Organization',
  势力组织: 'Organization',
  参战组织: 'Organization',
  鍔垮姏缁勭粐: 'Organization',
  鍙傛垬缁勭粐: 'Organization',
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
