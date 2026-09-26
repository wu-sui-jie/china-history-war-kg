<template>
  <div class="map-page">
    <div class="page-header">
      <div>
        <h1>历史地图视图</h1>
        <p>按真实经纬度落点展示战争地点、路线与事件包含的全部实体。</p>
      </div>
      <div class="header-actions">
        <lay-button @click="router.push('/knowledge/timeline')">查看时间轴</lay-button>
        <lay-button type="primary" @click="loadData">刷新</lay-button>
      </div>
    </div>

    <div class="filter-bar">
      <div class="filter-main">
        <lay-input v-model="filters.keyword" placeholder="搜索地点、现代地名或行政区" />
        <lay-select v-model="filters.dynasty">
          <lay-select-option value="">全部朝代</lay-select-option>
          <lay-select-option v-for="item in dynasties" :key="item" :value="item">{{ item }}</lay-select-option>
        </lay-select>
      </div>
      <div class="filter-actions">
        <lay-button type="primary" @click="loadData">筛选</lay-button>
        <lay-button @click="resetFilters">重置</lay-button>
      </div>
    </div>

    <div class="summary-grid">
      <div class="summary-card">
        <span>可定位事件</span>
        <strong>{{ mapData.summary?.mappable_events || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>单点事件</span>
        <strong>{{ mapData.summary?.single_point_events || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>可视路线</span>
        <strong>{{ mapData.summary?.route_events || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>待定位地点</span>
        <strong>{{ mapData.summary?.unmapped_places || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>低置信坐标</span>
        <strong>{{ mapData.summary?.low_confidence_places || 0 }}</strong>
      </div>
    </div>

    <div class="content-grid">
      <div class="panel chart-panel">
        <div class="panel-header">
          <h3>中国战争行军地图</h3>
          <div class="map-mode-switch">
            <button :class="{ active: mapMode === 'place' }" @click="setMapMode('place')">地点</button>
            <button :class="{ active: mapMode === 'event' }" @click="setMapMode('event')">事件</button>
            <button :class="{ active: mapMode === 'route' }" @click="setMapMode('route')">路线</button>
          </div>
        </div>
        <div class="map-shell">
          <div ref="chartRef" class="chart-box"></div>
          <div class="map-control-stack">
            <button title="放大" @click="zoomMap(0.72)">+</button>
            <button title="缩小" @click="zoomMap(1.28)">-</button>
            <button title="定位选中地点" @click="focusSelectedPlace">◎</button>
            <button title="复位视图" @click="resetMapView">↺</button>
          </div>
          <div class="map-overlay">
            <div class="map-title">历史战争行军态势</div>
            <div class="map-subtitle">
              {{ mapSubtitle }}
            </div>
            <div v-if="mapDegraded" class="map-degraded">
              省级底图未加载成功，当前显示的是简化示意方块；地点与路线数据不受影响。
            </div>
          </div>
          <div class="map-legend">
            <span><i class="dot place"></i>战争地点</span>
            <span><i class="dot event"></i>战争事件</span>
            <span><i class="dot active"></i>选中地点</span>
            <span><i class="line"></i>推断路线</span>
          </div>
        </div>
      </div>

      <div class="panel detail-panel">
        <div class="panel-header">
          <h3>{{ selectedRoute?.event_name || selectedEventPoint?.event_name || selectedPlace?.place_name || '地图详情' }}</h3>
          <span>{{ selectedRoute ? '推断路线' : (selectedEventPoint ? '事件点' : (selectedPlace?.compare_name || '点击地图中的地点或事件')) }}</span>
        </div>

        <div v-if="selectedRoute" class="detail-stack">
          <div class="info-card">
            <div class="info-grid">
              <span>起点：{{ selectedRoute.from_name }}</span>
              <span>终点：{{ selectedRoute.to_name }}</span>
              <span>关系链：{{ selectedRoute.relation_chain }}</span>
              <span>来源：{{ selectedRoute.route_source_label || '按关系类型推断' }}</span>
            </div>
          </div>
          <div class="entity-block">
            <h4>关系证据</h4>
            <div v-for="step in selectedRoute.relation_steps || []" :key="`${step.name}-${step.relation_type}`" class="evidence-item">
              <strong>{{ step.name }} · {{ step.relation_type }}</strong>
              <span>{{ relationSourceLabel(step.source_type) }}{{ step.confidence ? ` · ${step.confidence}` : '' }}</span>
              <span>坐标：{{ step.coord_source_label || '未标注' }}{{ step.coord_confidence ? ` · ${coordConfidenceLabel(step.coord_confidence)}` : '' }}</span>
              <p>{{ step.evidence || '暂无证据文本' }}</p>
            </div>
          </div>
        </div>

        <div v-else-if="selectedEventPoint" class="detail-stack">
          <div class="info-card">
            <div class="info-grid">
              <span>朝代：{{ selectedEventPoint.dynasty || '未标注' }}</span>
              <span>地点：{{ selectedEventPoint.place_name || '待补全' }}</span>
              <span>关系：{{ selectedEventPoint.relation_type || '关联地点' }}</span>
              <span>坐标：{{ selectedEventPoint.longitude }}, {{ selectedEventPoint.latitude }}</span>
              <span>坐标来源：{{ selectedEventPoint.coord_source_label || '未标注' }}</span>
              <span>坐标置信度：{{ coordConfidenceLabel(selectedEventPoint.coord_confidence) }}</span>
            </div>
            <div class="actions">
              <lay-button size="sm" @click="router.push(selectedEventPoint.detail_route)">事件详情</lay-button>
            </div>
          </div>
          <div class="entity-block">
            <h4>路线状态</h4>
            <div class="empty-state">
              {{ selectedEventPoint.has_route ? '该事件存在多个可定位地点，路线按关系类型推断。' : '该事件目前只有一个可定位地点，因此显示为事件点。' }}
            </div>
          </div>
          <div class="entity-block">
            <h4>关系证据</h4>
            <div class="evidence-item">
              <strong>{{ selectedEventPoint.place_name }} · {{ selectedEventPoint.relation_type || '关联地点' }}</strong>
              <span>{{ relationSourceLabel(selectedEventPoint.source_type) }}{{ selectedEventPoint.confidence ? ` · ${selectedEventPoint.confidence}` : '' }}</span>
              <p>{{ selectedEventPoint.evidence || '暂无证据文本' }}</p>
            </div>
          </div>
        </div>

        <div v-else-if="selectedPlace" class="detail-stack">
          <div class="info-card">
            <div class="info-grid">
              <span>朝代：{{ selectedPlace.dynasty || '未标注' }}</span>
              <span>今地：{{ selectedPlace.modern_name || '待补全' }}</span>
              <span>行政区：{{ selectedPlace.coord_hint || '待补全' }}</span>
              <span>坐标：{{ selectedPlace.longitude }}, {{ selectedPlace.latitude }}</span>
              <span>坐标来源：{{ selectedPlace.coord_source_label || '未标注' }}</span>
              <span>坐标置信度：{{ coordConfidenceLabel(selectedPlace.coord_confidence) }}</span>
            </div>
            <div class="actions">
              <lay-button size="sm" @click="router.push(selectedPlace.detail_route)">地点详情</lay-button>
            </div>
          </div>

          <div class="entity-block">
            <h4>包含事件</h4>
            <div class="chip-grid">
              <lay-tag v-for="item in selectedPlace.events || []" :key="item.id" @click="openDetail('Event', item.id)">
                {{ item.name }}
              </lay-tag>
            </div>
          </div>

          <div class="entity-block">
            <h4>涉及人物</h4>
            <div class="chip-grid">
              <lay-tag v-for="item in selectedPlace.entities?.persons || []" :key="item.id" @click="openDetail('Person', item.id)">
                {{ item.name }} · {{ item.relation_type }}
              </lay-tag>
              <div v-if="!(selectedPlace.entities?.persons || []).length" class="empty-state">暂无人物实体</div>
            </div>
          </div>

          <div class="entity-block">
            <h4>涉及组织</h4>
            <div class="chip-grid">
              <lay-tag v-for="item in selectedPlace.entities?.organizations || []" :key="item.id" @click="openDetail('Organization', item.id)">
                {{ item.name }} · {{ item.relation_type }}
              </lay-tag>
              <div v-if="!(selectedPlace.entities?.organizations || []).length" class="empty-state">暂无组织实体</div>
            </div>
          </div>

          <div class="entity-block">
            <h4>关联事件</h4>
            <div class="chip-grid">
              <lay-tag v-for="item in selectedPlace.entities?.related_events || []" :key="item.id" @click="openDetail('Event', item.id)">
                {{ item.name }} · {{ item.relation_type }}
              </lay-tag>
              <div v-if="!(selectedPlace.entities?.related_events || []).length" class="empty-state">暂无事件链路</div>
            </div>
          </div>
        </div>

        <div v-else class="empty-state">地图已加载。可切换地点、事件或路线模式查看空间分布。</div>
      </div>
    </div>

    <div class="content-grid lower-grid">
      <div class="panel">
        <div class="panel-header">
          <h3>战争路线 / 推断路径</h3>
          <span>{{ routeLineItems.length }} 条可视路线</span>
        </div>
        <div class="route-list">
          <div
            v-for="item in routeLineItems"
            :key="`${item.event_id}-${item.from_name}-${item.to_name}`"
            class="route-item"
            :class="{ active: selectedRoute?.event_id === item.event_id && selectedRoute?.from_name === item.from_name && selectedRoute?.to_name === item.to_name }"
            @click="selectRoute(item)"
          >
            <strong>{{ item.event_name }}</strong>
            <p>{{ item.from_name }} → {{ item.to_name }}</p>
            <span>{{ item.relation_chain }} · {{ item.route_source_label || '按关系类型推断' }}</span>
          </div>
          <div v-if="!routeLineItems.length" class="empty-state">暂无路线数据</div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3>事件发生地清单</h3>
          <span>{{ (mapData.points || []).length }} 个地点</span>
        </div>
        <div class="place-list">
          <div
            v-for="item in mapData.points || []"
            :key="item.place_id"
            class="place-card"
            :class="{ active: selectedPlace?.place_id === item.place_id }"
            @click="selectPlace(item)"
          >
            <div class="place-top">
              <div>
                <h4>{{ item.place_name }}</h4>
                <p>{{ item.compare_name }}</p>
              </div>
              <lay-tag color="#8b1e23">{{ item.event_count }} 场</lay-tag>
            </div>
            <div class="meta-grid">
              <span>今地：{{ item.modern_name || '待补全' }}</span>
              <span>行政区：{{ item.coord_hint || '待补全' }}</span>
              <span>坐标：{{ item.coord_source_label || '未解析' }} · {{ coordConfidenceLabel(item.coord_confidence) }}</span>
            </div>
          </div>
          <div v-if="!(mapData.points || []).length" class="empty-state">暂无地点数据</div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3>待定位地点</h3>
          <span>{{ (mapData.unmapped_places || []).length }} 个地点</span>
        </div>
        <div class="place-list">
          <div
            v-for="item in mapData.unmapped_places || []"
            :key="`unmapped-${item.place_id}`"
            class="place-card muted"
            @click="router.push(item.detail_route)"
          >
            <div class="place-top">
              <div>
                <h4>{{ item.place_name }}</h4>
                <p>{{ item.reason }}</p>
              </div>
              <lay-tag>{{ item.event_count }} 场</lay-tag>
            </div>
            <div class="meta-grid">
              <span>今地：{{ item.modern_name || '待补全' }}</span>
              <span>朝代：{{ item.dynasty || '未标注' }}</span>
              <span>{{ item.coord_note || '需要补充古今地名或坐标' }}</span>
            </div>
          </div>
          <div v-if="!(mapData.unmapped_places || []).length" class="empty-state">暂无待定位地点</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { layer } from '@layui/layui-vue'
import * as echarts from 'echarts'
import { getEventMap } from '../../api/module/workspace'
import { apiErrorMessage } from '../../utils/apiError'

const router = useRouter()
const route = useRoute()
const chartRef = ref<HTMLElement | null>(null)
const dynasties = ref<string[]>([])
const selectedPlace = ref<any>(null)
const selectedEventPoint = ref<any>(null)
const selectedRoute = ref<any>(null)
const selectedEventName = ref('')
const selectedEventIds = ref<number[]>([])
const mapMode = ref<'place' | 'event' | 'route'>('place')
let chart: echarts.ECharts | null = null

const filters = reactive({
  keyword: '',
  dynasty: '',
})

const mapData = ref<any>({
  summary: {},
  dynasties: [],
  points: [],
  place_points: [],
  event_points: [],
  unmapped_places: [],
  routes: [],
  route_lines: [],
  dynasty_distribution: [],
})

const routeLineItems = computed(() => (mapData.value.route_lines || []).filter((item: any) => Array.isArray(item.coords) && item.coords.length >= 2))
const eventPointItems = computed(() => (mapData.value.event_points || []).filter((item: any) => validCoord(item)))
const normalizeName = (value: any) => String(value || '').trim()
const placeAliases = (place: any) => {
  const aliases = new Set<string>()
  ;[place?.label_name, place?.place_name, place?.modern_name, place?.compare_name].forEach((value) => {
    const normalized = normalizeName(value)
    if (!normalized) return
    aliases.add(normalized)
    normalized.split('/').map((item) => item.trim()).filter(Boolean).forEach((item) => aliases.add(item))
  })
  return aliases
}
const selectedRouteKey = computed(() => {
  if (selectedRoute.value) return `${selectedRoute.value.event_id}-${selectedRoute.value.from_name}-${selectedRoute.value.to_name}`
  if (selectedEventName.value) return selectedEventName.value
  if (selectedEventPoint.value) return `event-${selectedEventPoint.value.event_id}`
  return selectedEventIds.value.join(',')
})
const mapSubtitle = computed(() => {
  if (selectedEventPoint.value) return `${selectedEventPoint.value.event_name}：${selectedEventPoint.value.has_route ? '推断路线' : '单点事件'}`
  if (selectedEventName.value) return `${selectedEventName.value}：推断路线`
  if (selectedEventIds.value.length && selectedPlace.value) return `${selectedPlace.value.place_name}：关联事件推断路线`
  if (selectedRoute.value) return `${selectedRoute.value.event_name}：${selectedRoute.value.from_name} → ${selectedRoute.value.to_name}`
  if (mapMode.value === 'event') return '按事件显示可定位点；单地点事件也会显示'
  if (mapMode.value === 'route') return '路线按事件-地点关系类型排序推断生成'
  return '按地点聚合显示可定位战争地点'
})
const displayedRouteLines = computed(() => {
  if (selectedEventName.value) {
    return routeLineItems.value.filter((item: any) => String(item.event_name || '').trim() === selectedEventName.value)
  }
  if (selectedEventIds.value.length) {
    const ids = new Set(selectedEventIds.value.map((item) => Number(item)))
    return routeLineItems.value.filter((item: any) => ids.has(Number(item.event_id)))
  }
  if (mapMode.value === 'route') return routeLineItems.value
  return selectedRoute.value ? [selectedRoute.value] : []
})
const displayedRoutePointNames = computed(() => {
  const names = displayedRouteLines.value.flatMap((item: any) => [item.from_name, item.to_name])
  return new Set(names.map((item: any) => String(item || '').trim()).filter(Boolean))
})
const displayedPoints = computed(() => {
  if (displayedRouteLines.value.length) {
    const names = displayedRoutePointNames.value
    const points = (mapData.value.points || []).filter((item: any) => {
      return [...placeAliases(item)].some((value) => names.has(value))
    })
    if (selectedPlace.value && !points.some((item: any) => item.place_id === selectedPlace.value.place_id)) {
      return [selectedPlace.value, ...points]
    }
    return points
  }
  if (mapMode.value === 'event') return []
  return selectedPlace.value ? [selectedPlace.value] : (mapData.value.points || [])
})
const displayedEventPoints = computed(() => {
  if (selectedEventPoint.value) return [selectedEventPoint.value]
  if (selectedEventName.value) {
    return eventPointItems.value.filter((item: any) => String(item.event_name || '').trim() === selectedEventName.value)
  }
  if (mapMode.value !== 'event') return []
  return eventPointItems.value
})

const mapState = reactive({
  center: [104, 36] as [number, number],
  zoom: 1.25,
})

const makeRectFeature = (name: string, left: number, bottom: number, right: number, top: number) => ({
  type: 'Feature',
  properties: { name },
  geometry: {
    type: 'Polygon',
    coordinates: [[
      [left, bottom],
      [right, bottom],
      [right, top],
      [left, top],
      [left, bottom],
    ]],
  },
})

const chinaProvinceGeoJson = {
  type: 'FeatureCollection',
  features: [
    makeRectFeature('新疆', 73, 35, 96, 49),
    makeRectFeature('西藏', 78, 27, 99, 36),
    makeRectFeature('青海', 89, 32, 103, 39),
    makeRectFeature('甘肃', 94, 34, 108, 42),
    makeRectFeature('宁夏', 104, 35, 108, 39),
    makeRectFeature('内蒙古', 106, 40, 124, 49),
    makeRectFeature('黑龙江', 123, 44, 134, 53),
    makeRectFeature('吉林', 121, 41, 131, 46),
    makeRectFeature('辽宁', 118, 38, 126, 43),
    makeRectFeature('北京', 115.4, 39.4, 117.6, 41.1),
    makeRectFeature('天津', 116.8, 38.5, 118.2, 40.1),
    makeRectFeature('河北', 113, 36, 120, 42),
    makeRectFeature('山西', 110, 34, 114, 40),
    makeRectFeature('陕西', 106, 31, 111, 39),
    makeRectFeature('河南', 110, 31, 116.5, 36.8),
    makeRectFeature('山东', 116, 34, 122.8, 38.8),
    makeRectFeature('江苏', 118, 30.5, 122, 35.2),
    makeRectFeature('安徽', 115, 29.5, 119, 34.8),
    makeRectFeature('上海', 120.7, 30.7, 122.1, 31.9),
    makeRectFeature('湖北', 108, 29, 116, 33.5),
    makeRectFeature('湖南', 109, 24.5, 114.5, 30.5),
    makeRectFeature('江西', 113.5, 24.5, 118.5, 30.5),
    makeRectFeature('浙江', 118.5, 27, 122.5, 31),
    makeRectFeature('福建', 116.5, 23, 120.5, 27.8),
    makeRectFeature('台湾', 120, 22, 122.5, 25.5),
    makeRectFeature('四川', 97, 26, 108, 34),
    makeRectFeature('重庆', 105, 28, 110, 31.8),
    makeRectFeature('贵州', 103, 24, 109, 29.5),
    makeRectFeature('云南', 97, 21, 106, 28),
    makeRectFeature('广西', 106, 21, 112.5, 25.8),
    makeRectFeature('广东', 110, 20, 117.5, 25.5),
    makeRectFeature('香港', 113.8, 22.1, 114.5, 22.6),
    makeRectFeature('澳门', 113.4, 22.0, 113.7, 22.3),
    makeRectFeature('海南', 108.5, 18, 111.5, 20.5),
  ],
}

const mapDegraded = ref(false)
let mapRegistered = false
let mapRegisterPromise: Promise<void> | null = null

// 底图按「随包文件 → 官方服务 → 内置示意方块」的顺序取，正常情况下走第一个。
//
// 为什么不再只靠官方服务：geo.datav.aliyun.com 开了防盗链（Referer ACL），从页面上直接
// fetch 会带上本站 Referer 而被 403 拒绝，拿回一张 HTML 错误页，于是静默落到示意方块——
// 地图看着像"一堆矩形拼起来的"，实际是底图没取到，与地点/路线数据无关。
// 随包文件的来源与刷新方式见 frontend/README.md「地图底图随包发布」。
const LOCAL_GEOJSON_URL = `${import.meta.env.BASE_URL}geo/china.json`
const REMOTE_GEOJSON_URL = 'https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json'

const PROVINCE_NAME_SUFFIXES = ['维吾尔自治区', '回族自治区', '壮族自治区', '特别行政区', '自治区', '省', '市']

/** 底图里的省名是全称（新疆维吾尔自治区），直接当地图标签太长，注册前压成短名。 */
const shortenProvinceName = (name: string) =>
  PROVINCE_NAME_SUFFIXES.reduce(
    (value, suffix) => (value.endsWith(suffix) ? value.slice(0, -suffix.length) : value),
    name,
  )

const registerChinaMap = (geojson: any) => {
  if (Array.isArray(geojson?.features)) {
    geojson.features.forEach((feature: any) => {
      const name = feature?.properties?.name
      if (name) feature.properties.name = shortenProvinceName(name)
    })
  }
  echarts.registerMap('china-war', geojson)
}

const fetchGeoJson = async (url: string, init?: RequestInit) => {
  const response = await fetch(url, init)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}

const ensureChinaMapRegistered = async () => {
  if (mapRegistered) return
  if (mapRegisterPromise) return mapRegisterPromise

  mapRegisterPromise = (async () => {
    try {
      registerChinaMap(await fetchGeoJson(LOCAL_GEOJSON_URL))
    } catch (localError) {
      console.warn('本地省级底图加载失败，改用官方服务:', localError)
      try {
        // 必须显式 no-referrer：带 Referer 的请求会被官方服务的防盗链直接 403
        registerChinaMap(await fetchGeoJson(REMOTE_GEOJSON_URL, { referrerPolicy: 'no-referrer' }))
      } catch (remoteError) {
        console.warn('中国省级底图加载失败，改用简化示意方块兜底:', remoteError)
        registerChinaMap(chinaProvinceGeoJson)
        mapDegraded.value = true
      }
    }
    mapRegistered = true
  })()

  return mapRegisterPromise
}

const setMapMode = (mode: 'place' | 'event' | 'route') => {
  mapMode.value = mode
  selectedPlace.value = null
  selectedEventPoint.value = null
  selectedRoute.value = null
  selectedEventName.value = ''
  selectedEventIds.value = []
  renderChart()
}

const selectPlace = (item: any) => {
  selectedPlace.value = item
  selectedEventPoint.value = null
  selectedEventName.value = ''
  selectedRoute.value = null
  selectedEventIds.value = []
  const relatedRoute = routeLineItems.value.find((route: any) => {
    const names = [route.from_name, route.to_name].map(normalizeName)
    return [...placeAliases(item)].some((value) => names.includes(value))
  })
  const relatedEventIds = getRelatedEventIds(item)
  const eventRoutes = routeLineItems.value.filter((route: any) => relatedEventIds.includes(Number(route.event_id)))

  if (eventRoutes.length) {
    selectedEventIds.value = relatedEventIds
    focusRoutesViewport(eventRoutes)
  } else if (relatedRoute) {
    selectedRoute.value = relatedRoute
    focusRouteViewport(relatedRoute)
  } else if (validCoord(item)) {
    mapState.center = [Number(item.longitude), Number(item.latitude)]
    mapState.zoom = Math.max(mapState.zoom, 4)
  }
}

const getRelatedEventIds = (item: any) => {
  return (item?.event_ids || item?.events || [])
    .map((event: any) => typeof event === 'object' ? Number(event.id) : Number(event))
    .filter((id: number) => Number.isFinite(id))
}

const openDetail = (type: string, id: number) => {
  router.push(`/knowledge/entity-detail?type=${type}&id=${id}&back=${encodeURIComponent('/knowledge/map')}`)
}

const relationSourceLabel = (sourceType: string) => {
  const labels: Record<string, string> = {
    extraction: '模型抽取',
    rule: '规则推断',
    manual: '人工确认',
  }
  return labels[sourceType] || '关系来源'
}

const coordConfidenceLabel = (confidence: string) => {
  const labels: Record<string, string> = {
    high: '高',
    medium: '中',
    low: '低',
  }
  return labels[confidence] || confidence || '未标注'
}

const validCoord = (item: any) => Number.isFinite(Number(item?.longitude)) && Number.isFinite(Number(item?.latitude))

const findPointByName = (name: string) => {
  const target = String(name || '').trim()
  if (!target) return null
  return (mapData.value.points || []).find((item: any) => {
    return placeAliases(item).has(target)
  }) || null
}

const findPointByEventName = (name: string) => {
  const target = String(name || '').trim()
  if (!target) return null
  return (mapData.value.points || []).find((item: any) => {
    return (item.events || []).some((event: any) => String(event?.name || '').trim() === target)
  }) || null
}

const selectEventPoint = (item: any) => {
  selectedEventPoint.value = item
  selectedPlace.value = null
  selectedRoute.value = null
  selectedEventIds.value = [Number(item.event_id)].filter((id) => Number.isFinite(id))
  selectedEventName.value = item.has_route ? item.event_name : ''
  const eventRoutes = routeLineItems.value.filter((route: any) => Number(route.event_id) === Number(item.event_id))
  if (eventRoutes.length) {
    focusRoutesViewport(eventRoutes)
  } else if (validCoord(item)) {
    mapState.center = [Number(item.longitude), Number(item.latitude)]
    mapState.zoom = Math.max(mapState.zoom, 4)
  }
  renderChart()
}

const zoomMap = (factor: number) => {
  mapState.zoom = Math.max(0.85, Math.min(8, mapState.zoom / factor))
  renderChart()
}

const focusSelectedPlace = () => {
  if (!selectedPlace.value || !validCoord(selectedPlace.value)) return
  mapState.center = [Number(selectedPlace.value.longitude), Number(selectedPlace.value.latitude)]
  mapState.zoom = Math.max(mapState.zoom, 4)
  renderChart()
}

const resetMapView = () => {
  mapState.center = [104, 36]
  mapState.zoom = 1.25
  selectedPlace.value = null
  selectedEventPoint.value = null
  selectedRoute.value = null
  selectedEventName.value = ''
  selectedEventIds.value = []
  renderChart()
}

const focusRouteViewport = (route: any) => {
  const coords = route?.coords || []
  if (coords.length < 2) return
  const xs = coords.map((coord: number[]) => coord[0])
  const ys = coords.map((coord: number[]) => coord[1])
  const xMin = Math.min(...xs)
  const xMax = Math.max(...xs)
  const yMin = Math.min(...ys)
  const yMax = Math.max(...ys)
  mapState.center = [(xMin + xMax) / 2, (yMin + yMax) / 2]
  mapState.zoom = Math.max(2.2, Math.min(7, 18 / Math.max(xMax - xMin, yMax - yMin, 2)))
}

const selectRoute = (route: any) => {
  selectedEventName.value = ''
  selectedEventIds.value = []
  selectedEventPoint.value = null
  selectedRoute.value = route
  const endPoint = findPointByName(route.to_name) || findPointByName(route.from_name)
  if (endPoint) selectedPlace.value = endPoint
  focusRouteViewport(route)
  renderChart()
}

const focusRoutesViewport = (routes: any[]) => {
  const coords = routes.flatMap((item) => item.coords || [])
  if (!coords.length) return
  const xs = coords.map((coord: number[]) => coord[0])
  const ys = coords.map((coord: number[]) => coord[1])
  const xMin = Math.min(...xs)
  const xMax = Math.max(...xs)
  const yMin = Math.min(...ys)
  const yMax = Math.max(...ys)
  mapState.center = [(xMin + xMax) / 2, (yMin + yMax) / 2]
  mapState.zoom = Math.max(2.2, Math.min(7, 18 / Math.max(xMax - xMin, yMax - yMin, 2)))
}

const findRoutesByPlaceName = (placeName: string, place: any = null) => {
  const aliases = new Set<string>(
    [
      placeName,
      place?.label_name,
      place?.place_name,
      place?.modern_name,
      place?.compare_name,
    ]
      .map(normalizeName)
      .filter(Boolean)
  )
  ;[...aliases].forEach((value) => {
    value.split('/').map((item) => item.trim()).filter(Boolean).forEach((item) => aliases.add(item))
  })

  return routeLineItems.value.filter((item: any) => {
    return [item.from_name, item.to_name]
      .map(normalizeName)
      .some((value) => aliases.has(value))
  })
}

const applyRouteFocusFromQuery = () => {
  const placeName = String(route.query.place || '').trim()
  const eventName = String(route.query.event || '').trim()
  if (!placeName && !eventName) {
    selectedPlace.value = null
    selectedEventPoint.value = null
    selectedRoute.value = null
    selectedEventName.value = ''
    selectedEventIds.value = []
    mapState.center = [104, 36]
    mapState.zoom = 1.25
    return
  }

  if (eventName) {
    const eventRoutes = routeLineItems.value.filter((item: any) => String(item.event_name || '').trim() === eventName)
    selectedEventName.value = eventRoutes.length ? eventName : ''
    selectedEventIds.value = []
    selectedRoute.value = null
    selectedEventPoint.value = null
    if (eventRoutes.length) {
      const endPoint = findPointByName(eventRoutes[0].to_name) || findPointByName(eventRoutes[0].from_name)
      selectedPlace.value = endPoint || null
      focusRoutesViewport(eventRoutes)
    } else {
      const eventPoint = findPointByEventName(eventName)
      selectedEventPoint.value = eventPoint || null
      if (eventPoint && validCoord(eventPoint)) {
        mapState.center = [Number(eventPoint.longitude), Number(eventPoint.latitude)]
        mapState.zoom = 4
      }
    }
    return
  }

  const place = findPointByName(placeName)
  if (place) selectedPlace.value = place

  const matchedRoute = findRoutesByPlaceName(placeName, place)[0]

  if (place) {
    selectedRoute.value = null
    selectedEventPoint.value = null
    selectedEventName.value = ''
    const relatedEventIds = getRelatedEventIds(place)
    const eventRoutes = routeLineItems.value.filter((item: any) => relatedEventIds.includes(Number(item.event_id)))
    if (eventRoutes.length) {
      selectedEventIds.value = relatedEventIds
      focusRoutesViewport(eventRoutes)
      return
    }
    selectedEventIds.value = []
  }

  if (matchedRoute) {
    selectRoute(matchedRoute)
  } else if (place && validCoord(place)) {
    mapState.center = [Number(place.longitude), Number(place.latitude)]
    mapState.zoom = 4
  }
}

const renderChart = async () => {
  await nextTick()
  if (!chartRef.value) return
  await ensureChinaMapRegistered()
  if (!chart) {
    chart = echarts.init(chartRef.value)
    chart.on('click', (params: any) => {
      if (params.seriesType === 'lines' && params?.data?.raw) {
        selectRoute(params.data.raw)
        return
      }
      if (params?.data?.point_type === 'event' && params?.data?.raw) {
        selectEventPoint(params.data.raw)
        return
      }
      if (params?.data?.raw) {
        selectPlace(params.data.raw)
        renderChart()
        return
      }
    })
    chart.on('georoam', () => {
      const option: any = chart?.getOption()
      const geoOption = option?.geo?.[0]
      if (geoOption?.center) mapState.center = geoOption.center
      if (geoOption?.zoom) mapState.zoom = geoOption.zoom
    })
  }

  const scatterData = displayedPoints.value
    .filter((item: any) => validCoord(item))
    .map((item: any) => ({
      name: item.label_name,
      value: [Number(item.longitude), Number(item.latitude), item.event_count],
      raw: item,
      point_type: 'place',
      itemStyle: {
        color: selectedPlace.value?.place_id === item.place_id ? '#8b1e23' : '#c59b58',
        borderColor: '#fff',
        borderWidth: selectedPlace.value?.place_id === item.place_id ? 3 : 1.5,
        shadowBlur: selectedPlace.value?.place_id === item.place_id ? 18 : 8,
        shadowColor: 'rgba(15, 23, 42, 0.22)',
      },
    }))

  const eventScatterData = displayedEventPoints.value
    .filter((item: any) => validCoord(item))
    .map((item: any) => ({
      name: item.label_name,
      value: [Number(item.longitude), Number(item.latitude), Math.max(1, item.related_place_count || 1)],
      raw: item,
      point_type: 'event',
      itemStyle: {
        color: selectedEventPoint.value?.event_id === item.event_id ? '#8b1e23' : '#2563eb',
        borderColor: '#fff',
        borderWidth: selectedEventPoint.value?.event_id === item.event_id ? 3 : 1.5,
        shadowBlur: selectedEventPoint.value?.event_id === item.event_id ? 18 : 8,
        shadowColor: 'rgba(15, 23, 42, 0.22)',
      },
    }))

  const lineData = displayedRouteLines.value.map((item: any) => ({
    coords: item.coords,
    event_name: item.event_name,
    relation_chain: item.relation_chain,
    raw: item,
    lineStyle: {
      color: 'rgba(139, 30, 35, 0.92)',
      width: 4,
      curveness: 0.18,
    },
  }))

  chart.setOption({
    animationDuration: 650,
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter(params: any) {
        if (params.seriesType === 'scatter') {
          const raw = params.data.raw
          if (params.data.point_type === 'event') {
            return [
              `<strong>${raw.event_name}</strong>`,
              `地点：${raw.place_name}`,
              `关系：${raw.relation_type || '关联地点'}`,
              `来源：${relationSourceLabel(raw.source_type)}`,
              `坐标：${raw.coord_source_label || '未标注'} · ${coordConfidenceLabel(raw.coord_confidence)}`,
              raw.has_route ? '路线：可推断' : '路线：单点事件',
            ].join('<br/>')
          }
          return [
            `<strong>${raw.place_name}</strong>`,
            `今地：${raw.modern_name || '待补全'}`,
            `事件：${raw.event_count} 场`,
            `坐标：${raw.longitude}, ${raw.latitude}`,
            `坐标来源：${raw.coord_source_label || '未标注'} · ${coordConfidenceLabel(raw.coord_confidence)}`,
          ].join('<br/>')
        }
        if (params.seriesType === 'lines') {
          return [
            `<strong>${params.data.event_name}</strong>`,
            `${params.data.raw?.from_name || '起点'} → ${params.data.raw?.to_name || '终点'}`,
            params.data.relation_chain,
            params.data.raw?.route_source_label || '按关系类型推断',
            (params.data.raw?.relation_steps || []).map((step: any) => `${step.relation_type}：${step.coord_source_label || '未标注'} / ${step.evidence || '暂无证据'}`).join('<br/>'),
          ].join('<br/>')
        }
        return params.name || ''
      },
    },
    geo: {
      map: 'china-war',
      roam: true,
      zoom: mapState.zoom,
      center: mapState.center,
      scaleLimit: { min: 0.85, max: 8 },
      label: {
        show: true,
        color: 'rgba(71, 85, 105, 0.64)',
        fontSize: 11,
      },
      itemStyle: {
        areaColor: '#e6eadf',
        borderColor: 'rgba(120, 139, 116, 0.72)',
        borderWidth: 1,
      },
      emphasis: {
        label: { color: '#8b1e23', fontWeight: 700 },
        itemStyle: { areaColor: '#f3e5c8' },
      },
    },
    graphic: [
      {
        type: 'group',
        right: 18,
        top: 18,
        children: [
          {
            type: 'rect',
            shape: { width: 190, height: 52, r: 12 },
            style: {
              fill: 'rgba(255,255,255,0.72)',
              stroke: 'rgba(196, 155, 88, 0.24)',
              lineWidth: 1,
            },
          },
          {
            type: 'text',
            left: 14,
            top: 9,
            style: {
              text: `缩放 ${mapState.zoom.toFixed(1)}x`,
              fill: '#475569',
              font: '12px sans-serif',
            },
          },
          {
            type: 'text',
            left: 14,
            top: 29,
            style: {
              text: `中心 ${mapState.center[0].toFixed(1)}, ${mapState.center[1].toFixed(1)}`,
              fill: '#475569',
              font: '12px sans-serif',
            },
          },
        ],
      },
    ],
    series: [
      {
        type: 'lines',
        coordinateSystem: 'geo',
        z: 2,
        polyline: false,
        effect: {
          show: true,
          symbol: 'arrow',
          trailLength: 0.18,
          color: '#8b1e23',
          symbolSize: 9,
          period: 4,
        },
        lineStyle: {
          color: 'rgba(139, 30, 35, 0.36)',
          width: 2,
          curveness: 0.18,
          shadowBlur: 8,
          shadowColor: 'rgba(139, 30, 35, 0.18)',
        },
        label: {
          show: false,
          formatter: '',
          color: '#7f1d1d',
          fontSize: 11,
          fontWeight: 700,
          backgroundColor: 'rgba(255,255,255,0.82)',
          borderRadius: 4,
          padding: [2, 4],
        },
        data: lineData,
      },
      {
        type: 'scatter',
        coordinateSystem: 'geo',
        symbol: 'pin',
        symbolSize: (value: number[]) => Math.max(22, Math.min(48, value[2] * 3 + 20)),
        label: {
          show: true,
          formatter: '{b}',
          position: 'top',
          color: '#1f2937',
          fontSize: 12,
          fontWeight: 600,
          backgroundColor: 'rgba(255,255,255,0.84)',
          padding: [3, 5],
          borderRadius: 5,
        },
        data: scatterData,
        z: 3,
      },
      {
        type: 'scatter',
        coordinateSystem: 'geo',
        symbol: 'circle',
        symbolSize: (value: number[]) => Math.max(18, Math.min(34, value[2] * 4 + 16)),
        label: {
          show: true,
          formatter: '{b}',
          position: 'top',
          color: '#1f2937',
          fontSize: 12,
          fontWeight: 600,
          backgroundColor: 'rgba(255,255,255,0.84)',
          padding: [3, 5],
          borderRadius: 5,
        },
        data: eventScatterData,
        z: 4,
      },
      {
        type: 'effectScatter',
        coordinateSystem: 'geo',
        rippleEffect: { scale: 3.4, brushType: 'stroke' },
        symbolSize: 16,
        data: selectedEventPoint.value && validCoord(selectedEventPoint.value)
          ? [{
              name: selectedEventPoint.value.label_name,
              value: [Number(selectedEventPoint.value.longitude), Number(selectedEventPoint.value.latitude), 1],
            }]
          : selectedPlace.value && validCoord(selectedPlace.value)
          ? [{
              name: selectedPlace.value.label_name,
              value: [Number(selectedPlace.value.longitude), Number(selectedPlace.value.latitude), selectedPlace.value.event_count],
            }]
          : [],
        itemStyle: { color: '#8b1e23' },
        z: 4,
      },
    ],
  }, true)
}

const loadData = async () => {
  // 后端失败是 HTTP 5xx，异常分支里才拿得到后端的 msg
  try {
    const res = await getEventMap({ ...filters })
    if (res.code === 200) {
      mapData.value = res.data || {}
      dynasties.value = res.data?.dynasties || []
      selectedPlace.value = null
      selectedEventPoint.value = null
      selectedRoute.value = null
      selectedEventName.value = ''
      selectedEventIds.value = []
      mapState.center = [104, 36]
      mapState.zoom = 1.25
      applyRouteFocusFromQuery()
      renderChart()
      return
    }
    layer.msg(res.msg || '加载地图数据失败', { icon: 2 })
  } catch (error) {
    console.error('加载地图数据失败:', error)
    layer.msg(apiErrorMessage(error, '加载地图数据失败，请稍后重试'), { icon: 2 })
  }
}

const resetFilters = () => {
  filters.keyword = ''
  filters.dynasty = ''
  loadData()
}

watch(() => [mapData.value.points, mapData.value.event_points, mapMode.value, selectedPlace.value?.place_id, selectedEventPoint.value?.event_id, selectedRouteKey.value], () => renderChart(), { deep: true })
watch(() => [route.query.place, route.query.event], () => {
  applyRouteFocusFromQuery()
  renderChart()
})

onMounted(() => {
  loadData()
  window.addEventListener('resize', renderChart)
})

onUnmounted(() => {
  window.removeEventListener('resize', renderChart)
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.map-page {
  padding: 20px;
  min-height: 100%;
  background:
    radial-gradient(circle at top left, rgba(139, 30, 35, 0.12), transparent 24%),
    linear-gradient(180deg, #f7f2ea 0%, #eef4fb 100%);
}

.page-header,
.filter-bar,
.summary-card,
.panel,
.place-card,
.info-card {
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 12px 28px rgba(74, 54, 24, 0.08);
}

.page-header,
.filter-bar,
.panel-header,
.place-top,
.actions {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.page-header,
.filter-bar,
.panel,
.place-card,
.info-card {
  padding: 18px;
}

.page-header,
.filter-bar,
.summary-grid,
.content-grid {
  margin-bottom: 16px;
}

.filter-bar {
  align-items: center;
  flex-wrap: nowrap;
  padding: 16px 18px;
}

.filter-main {
  display: grid;
  grid-template-columns: minmax(320px, 1fr) 220px;
  gap: 12px;
  flex: 1;
  min-width: 0;
}

.filter-actions {
  display: flex;
  gap: 10px;
  flex: 0 0 auto;
}

.filter-actions :deep(.layui-btn) {
  min-width: 72px;
}

.page-header h1,
.panel-header h3,
.place-top h4,
.entity-block h4 {
  margin: 0;
  color: #2f3542;
}

.page-header p,
.route-item p,
.place-top p,
.meta-grid span,
.empty-state,
.panel-header span {
  margin: 0;
  color: #6b7280;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.map-mode-switch {
  display: inline-flex;
  padding: 3px;
  border: 1px solid rgba(196, 155, 88, 0.24);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
}

.map-mode-switch button {
  min-width: 58px;
  height: 30px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #475569;
  cursor: pointer;
  font-weight: 700;
}

.map-mode-switch button.active {
  background: #0f9f8f;
  color: #fff;
  box-shadow: 0 8px 16px rgba(15, 159, 143, 0.22);
}

.summary-grid,
.content-grid,
.detail-stack,
.entity-block,
.route-list,
.place-list,
.meta-grid,
.info-grid,
.chip-grid {
  display: grid;
  gap: 14px;
}

.summary-grid {
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
}

.summary-card {
  min-width: 0;
  text-align: center;
}

.summary-card strong {
  display: block;
  margin-top: 10px;
  font-size: 30px;
  color: #111827;
}

.content-grid {
  grid-template-columns: 1.25fr 0.95fr;
}

.lower-grid {
  align-items: start;
}

.map-shell {
  position: relative;
  min-height: 620px;
  border-radius: 18px;
  overflow: hidden;
  background:
    radial-gradient(circle at 64% 38%, rgba(125, 211, 252, 0.28), transparent 30%),
    radial-gradient(circle at 36% 46%, rgba(190, 196, 146, 0.26), transparent 36%),
    linear-gradient(145deg, #e8f4fb 0%, #f8f4ea 52%, #e8f1ea 100%);
}

.chart-box {
  position: relative;
  z-index: 1;
  height: 560px;
  min-height: 620px;
  border-radius: 18px;
}

.map-control-stack {
  position: absolute;
  right: 18px;
  top: 92px;
  z-index: 8;
  display: grid;
  gap: 8px;
}

.map-control-stack button {
  width: 38px;
  height: 38px;
  border: 1px solid rgba(196, 155, 88, 0.28);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.92);
  color: #334155;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.14);
  cursor: pointer;
  font-size: 18px;
  font-weight: 700;
}

.map-control-stack button:hover {
  color: #8b1e23;
  border-color: rgba(139, 30, 35, 0.32);
  background: #fff8ef;
}

.map-overlay {
  position: absolute;
  left: 18px;
  top: 18px;
  z-index: 7;
  max-width: 460px;
  padding: 13px 16px;
  border: 1px solid rgba(196, 155, 88, 0.26);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.84);
  box-shadow: 0 14px 30px rgba(15, 23, 42, 0.12);
  backdrop-filter: blur(8px);
}

.map-title {
  color: #8b1e23;
  font-size: 17px;
  font-weight: 800;
}

.map-subtitle {
  margin-top: 5px;
  color: #64748b;
  font-size: 12px;
}

.map-degraded {
  margin-top: 8px;
  padding: 7px 9px;
  border-radius: 8px;
  background: rgba(139, 30, 35, 0.08);
  color: #8b1e23;
  font-size: 12px;
  line-height: 1.6;
}

.map-legend {
  position: absolute;
  left: 18px;
  bottom: 18px;
  z-index: 7;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 9px 12px;
  border: 1px solid rgba(196, 155, 88, 0.24);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.12);
}

.map-legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #475569;
  font-size: 12px;
  white-space: nowrap;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}

.dot.place {
  background: #c59b58;
}

.dot.event {
  background: #2563eb;
}

.dot.active {
  background: #8b1e23;
}

.line {
  width: 24px;
  height: 2px;
  display: inline-block;
  background: #8b1e23;
  position: relative;
}

.line::after {
  content: "";
  position: absolute;
  right: -2px;
  top: -4px;
  border-left: 7px solid #8b1e23;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
}

.detail-panel {
  min-height: 560px;
}

.info-grid,
.meta-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.entity-block,
.route-item,
.place-card {
  background: #f8fafc;
  border-radius: 14px;
}

.evidence-item {
  display: grid;
  gap: 6px;
  padding: 12px;
  border: 1px solid rgba(196, 155, 88, 0.18);
  border-radius: 10px;
  background: #fff;
}

.evidence-item strong {
  color: #1f2937;
}

.evidence-item span,
.evidence-item p {
  margin: 0;
  color: #64748b;
  line-height: 1.65;
}

.route-item {
  padding: 14px;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  border: 1px solid transparent;
}

.route-item.active,
.route-item:hover {
  transform: translateY(-2px);
  border-color: rgba(139, 30, 35, 0.25);
  background: #fff8ef;
  box-shadow: 0 12px 24px rgba(74, 54, 24, 0.1);
}

.chip-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.route-list,
.place-list,
.detail-stack {
  max-height: 560px;
  overflow: auto;
}

.place-card {
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.place-card.active,
.place-card:hover {
  transform: translateY(-2px);
  border-color: rgba(139, 30, 35, 0.28);
  box-shadow: 0 16px 30px rgba(74, 54, 24, 0.12);
}

.place-card.muted {
  background: #f8fafc;
  border-style: dashed;
}

:deep(.layui-tag) {
  cursor: pointer;
}

@media (max-width: 1100px) {
  .content-grid,
  .summary-grid,
  .info-grid,
  .meta-grid {
    grid-template-columns: 1fr;
  }

  .filter-bar {
    align-items: stretch;
    flex-direction: column;
  }

  .filter-main {
    grid-template-columns: 1fr;
  }

  .filter-actions {
    justify-content: flex-end;
  }

  .detail-panel,
  .map-shell,
  .chart-box {
    min-height: auto;
    height: 420px;
  }

  .map-overlay {
    max-width: calc(100% - 84px);
  }
}

@media (max-width: 768px) {
  .map-page {
    padding: 14px;
  }
}
</style>
