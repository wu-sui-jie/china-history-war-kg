<!--
  文本实体与事件识别页面 - 完整版

  功能: 从用户输入的文本中识别历史实体、事件和关系
    - 文本输入
    - 实体识别（人物、地点、组织）及其属性
    - 事件识别及其属性
    - 关系识别和图谱可视化
    - 历史记录管理
    - 结果导出

  API: POST /api/extract/entities-events
  参数: text(用户输入的文本)
-->
<template>
  <div class="text-extract-container">
    <!-- 历史记录侧边栏 -->
    <div class="history-sidebar" :class="{ 'sidebar-collapsed': !showHistory }">
      <div class="sidebar-header">
        <div class="sidebar-header-top">
          <button type="button" class="new-extract-button" @click="createNewExtract">
            ➕ 新建识别
          </button>
          <div class="collapse-btn" @click="toggleHistory" title="收起历史记录">
            <span>◀</span>
          </div>
        </div>
      </div>
      <div class="history-list">
        <div
          v-for="(item, index) in historyList"
          :key="item.id"
          class="history-item"
          :class="{ 'active': currentHistoryIndex === index }"
        >
          <div class="history-item-content" @click="loadHistory(index)">
            <div class="history-title">{{ item.title || '未命名识别' }}</div>
            <div class="history-time">{{ formatChatTime(item.time) }}</div>
            <div class="history-stats">
              <span v-if="item.result?.summary?.event_count">⚔️{{ item.result.summary.event_count }}</span>
              <span v-if="item.result?.summary?.person_count">👤{{ item.result.summary.person_count }}</span>
              <span v-if="item.result?.summary?.place_count">📍{{ item.result.summary.place_count }}</span>
            </div>
          </div>
          <div class="history-actions">
            <span class="delete-icon" @click.stop="deleteHistory(index)">🗑️</span>
          </div>
        </div>
        <div v-if="historyList.length === 0" class="history-empty">
          暂无历史记录
        </div>
      </div>
    </div>

    <!-- 展开按钮（侧边栏收起时显示） -->
    <div v-if="!showHistory" class="expand-btn" @click="toggleHistory" title="展开历史记录">
      <span class="expand-icon">▶</span>
      <span class="expand-text">历史记录</span>
    </div>

    <!-- 中间输入区域 -->
    <div class="input-section">
      <div class="section-header">
        <h3>📝 文本输入</h3>
        <span class="char-count">{{ textLength }} / 1000 字符</span>
      </div>
      <div class="textarea-wrapper">
        <textarea
          v-model="inputText"
          placeholder="请输入要分析的历史文本，例如：&#10;&#10;商汤发起灭夏战争，采取分别翦除夏朝羽翼的策略，各个击破位于夏、商之间的韦、顾、昆吾等夏属国，使夏孤立无援。接着率战车70乘，敢死士6000，约会诸侯军由商都沿黄河南岸西进..."
          :maxlength="1000"
          @input="updateCharCount"
        ></textarea>
      </div>
      <div class="action-bar">
        <button
          class="extract-button"
          :disabled="!inputText.trim() || loading"
          @click="startExtract"
        >
          <span v-if="loading" class="loading-spinner"></span>
          <span v-else>🔍</span>
          {{ loading ? '识别中...' : '开始识别' }}
        </button>
        <button class="clear-button" @click="clearAll" :disabled="loading">
          🗑️ 清空
        </button>
      </div>

      <!-- 示例文本 -->
      <div class="examples-section">
        <div class="examples-title">💡 示例文本</div>
        <div class="examples-list">
          <div
            v-for="(example, index) in examples"
            :key="index"
            class="example-item"
            @click="useExample(example)"
          >
            {{ example.title }}
          </div>
        </div>
      </div>
    </div>

    <!-- 右侧结果区域 -->
    <div class="result-section">
      <!-- 结果头部 -->
      <div class="result-header">
        <h3>📊 识别结果</h3>
        <div class="result-actions" v-if="result">
          <button class="export-btn" @click="exportToMarkdown" title="导出为Markdown">
            📥 导出MD
          </button>
          <button class="export-btn" @click="exportToJSON" title="导出为JSON">
            📥 导出JSON
          </button>
        </div>
      </div>

      <!-- 识别统计 -->
      <div v-if="result" class="stats-bar">
        <div class="stat-item">
          <span class="stat-icon">📍</span>
          <span class="stat-label">地点</span>
          <span class="stat-value">{{ result.summary.place_count }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-icon">🏛️</span>
          <span class="stat-label">组织</span>
          <span class="stat-value">{{ result.summary.organization_count }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-icon">👤</span>
          <span class="stat-label">人物</span>
          <span class="stat-value">{{ result.summary.person_count }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-icon">⚔️</span>
          <span class="stat-label">事件</span>
          <span class="stat-value">{{ result.summary.event_count }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-icon">🔗</span>
          <span class="stat-label">关系</span>
          <span class="stat-value">{{ result.summary.relation_count }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-icon">⏱️</span>
          <span class="stat-label">耗时</span>
          <span class="stat-value">{{ result.process_time }}s</span>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-if="!result && !loading" class="empty-state">
        <div class="empty-icon">📊</div>
        <div class="empty-text">输入文本后点击"开始识别"</div>
        <div class="empty-hint">系统将自动提取实体、事件及其关系</div>
      </div>

      <!-- 加载状态 -->
      <div v-if="loading" class="loading-state">
        <div class="loading-icon">⏳</div>
        <div class="loading-text">正在使用大模型识别文本中的实体、事件和关系...</div>
        <div class="loading-hint">这可能需要2分钟到5分钟，请耐心等待</div>
      </div>

      <!-- 识别结果 -->
      <div v-if="result && !loading" class="result-content">
        <!-- 事件识别结果 -->
        <div v-if="result.events.length > 0" class="result-card">
          <div class="card-header" @click="toggleSection('events')">
            <h4>⚔️ 识别到的事件 ({{ result.events.length }})</h4>
            <span class="toggle-icon">{{ expandedSections.events ? '▼' : '▶' }}</span>
          </div>
          <div v-if="expandedSections.events" class="card-content">
            <div
              v-for="(event, index) in result.events"
              :key="index"
              class="event-card"
            >
              <div class="event-header">
                <span class="event-name">{{ event.EventName }}</span>
                <span v-if="event.EventType" class="event-type">{{ event.EventType }}</span>
              </div>
              <div class="event-attributes">
                <div v-if="event.DynastyName" class="attr-item">
                  <span class="attr-label">朝代:</span>
                  <span class="attr-value">{{ event.DynastyName }}</span>
                </div>
                <div v-if="event.StartDate" class="attr-item">
                  <span class="attr-label">开始时间:</span>
                  <span class="attr-value">{{ event.StartDate }}</span>
                </div>
                <div v-if="event.EndDate" class="attr-item">
                  <span class="attr-label">结束时间:</span>
                  <span class="attr-value">{{ event.EndDate }}</span>
                </div>
                <div v-if="event.Place" class="attr-item">
                  <span class="attr-label">地点:</span>
                  <span class="attr-value">{{ event.Place }}</span>
                </div>
                <div v-if="event.Aggressor" class="attr-item">
                  <span class="attr-label">进攻方:</span>
                  <span class="attr-value">{{ event.Aggressor }}</span>
                </div>
                <div v-if="event.Defender" class="attr-item">
                  <span class="attr-label">防守方:</span>
                  <span class="attr-value">{{ event.Defender }}</span>
                </div>
                <div v-if="event.Allies" class="attr-item">
                  <span class="attr-label">盟友:</span>
                  <span class="attr-value">{{ event.Allies }}</span>
                </div>
                <div v-if="event.Commanders" class="attr-item">
                  <span class="attr-label">指挥官:</span>
                  <span class="attr-value">{{ event.Commanders }}</span>
                </div>
                <div v-if="event.KeyPersons" class="attr-item">
                  <span class="attr-label">关键人物:</span>
                  <span class="attr-value">{{ event.KeyPersons }}</span>
                </div>
                <div v-if="event.Result" class="attr-item">
                  <span class="attr-label">结果:</span>
                  <span class="attr-value">{{ event.Result }}</span>
                </div>
                <div v-if="event.Impact" class="attr-item">
                  <span class="attr-label">影响:</span>
                  <span class="attr-value">{{ event.Impact }}</span>
                </div>
                <div v-if="event.TroopSize" class="attr-item">
                  <span class="attr-label">兵力:</span>
                  <span class="attr-value">{{ event.TroopSize }}</span>
                </div>
              </div>
              <div v-if="event.source_text" class="event-source">
                <span class="source-label">原文:</span>
                <span class="source-text">{{ truncateText(event.source_text, 150) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 实体识别结果 -->
        <div class="result-card">
          <div class="card-header" @click="toggleSection('entities')">
            <h4>🏷️ 识别到的实体</h4>
            <span class="toggle-icon">{{ expandedSections.entities ? '▼' : '▶' }}</span>
          </div>
          <div v-if="expandedSections.entities" class="card-content">
            <!-- 地点实体 -->
            <div v-if="result.entities.places.length > 0" class="entity-group">
              <h5>📍 地点 ({{ result.entities.places.length }})</h5>
              <div class="entity-table-wrapper">
                <table class="entity-table">
                  <thead>
                    <tr>
                      <th>地名</th>
                      <th>现代名</th>
                      <th>朝代</th>
                      <th>省</th>
                      <th>市</th>
                      <th>区/县</th>
                      <th>原文</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(place, index) in result.entities.places" :key="index">
                      <td class="entity-name">{{ place.geo_name }}</td>
                      <td>{{ place.modern_name || '-' }}</td>
                      <td>{{ place.DynastyName || '-' }}</td>
                      <td>{{ place.Province || '-' }}</td>
                      <td>{{ place.City || '-' }}</td>
                      <td>{{ place.District_County || '-' }}</td>
                      <td class="source-cell" :title="place.source_text">{{ truncateText(place.source_text, 30) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <!-- 组织实体 -->
            <div v-if="result.entities.organizations.length > 0" class="entity-group">
              <h5>🏛️ 组织 ({{ result.entities.organizations.length }})</h5>
              <div class="entity-table-wrapper">
                <table class="entity-table">
                  <thead>
                    <tr>
                      <th>组织名</th>
                      <th>类型</th>
                      <th>朝代</th>
                      <th>原文</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(org, index) in result.entities.organizations" :key="index">
                      <td class="entity-name">{{ org.OrgName }}</td>
                      <td>{{ org.OrgType || '-' }}</td>
                      <td>{{ org.DynastyName || '-' }}</td>
                      <td class="source-cell" :title="org.source_text">{{ truncateText(org.source_text, 30) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <!-- 人物实体 -->
            <div v-if="result.entities.persons.length > 0" class="entity-group">
              <h5>👤 人物 ({{ result.entities.persons.length }})</h5>
              <div class="entity-table-wrapper">
                <table class="entity-table">
                  <thead>
                    <tr>
                      <th>姓名</th>
                      <th>朝代</th>
                      <th>所属组织</th>
                      <th>角色</th>
                      <th>备注</th>
                      <th>原文</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(person, index) in result.entities.persons" :key="index">
                      <td class="entity-name">{{ person.PersonName }}</td>
                      <td>{{ person.DynastyName || '-' }}</td>
                      <td>{{ person.OrgName || '-' }}</td>
                      <td>{{ person.Role || '-' }}</td>
                      <td>{{ person.Note || '-' }}</td>
                      <td class="source-cell" :title="person.source_text">{{ truncateText(person.source_text, 30) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

        <!-- 关系识别结果 -->
        <div class="result-card">
          <div class="card-header" @click="toggleSection('relations')">
            <h4>🔗 识别到的关系</h4>
            <span class="toggle-icon">{{ expandedSections.relations ? '▼' : '▶' }}</span>
          </div>
          <div v-if="expandedSections.relations" class="card-content">
            <!-- 事件-地点关系 -->
            <div v-if="result.relations.event_place.length > 0" class="relation-group">
              <h5>📍 事件-地点关系 ({{ result.relations.event_place.length }})</h5>
              <div class="relation-list">
                <div v-for="(rel, index) in result.relations.event_place" :key="index" class="relation-item">
                  <span class="rel-source">{{ rel.EventName }}</span>
                  <span class="rel-arrow">→</span>
                  <span class="rel-type">{{ rel.relation }}</span>
                  <span class="rel-arrow">→</span>
                  <span class="rel-target">{{ rel.PlaceName || rel.modern_name }}</span>
                  <span v-if="rel.evidence" class="rel-evidence" :title="rel.evidence">📄</span>
                </div>
              </div>
            </div>

            <!-- 事件-人物关系 -->
            <div v-if="result.relations.event_person.length > 0" class="relation-group">
              <h5>👤 事件-人物关系 ({{ result.relations.event_person.length }})</h5>
              <div class="relation-list">
                <div v-for="(rel, index) in result.relations.event_person" :key="index" class="relation-item">
                  <span class="rel-source">{{ rel.EventName }}</span>
                  <span class="rel-arrow">→</span>
                  <span class="rel-type">{{ rel.relation }}</span>
                  <span class="rel-arrow">→</span>
                  <span class="rel-target">{{ rel.PersonName }}</span>
                  <span v-if="rel.evidence" class="rel-evidence" :title="rel.evidence">📄</span>
                </div>
              </div>
            </div>

            <!-- 事件-组织关系 -->
            <div v-if="result.relations.event_organization.length > 0" class="relation-group">
              <h5>🏛️ 事件-组织关系 ({{ result.relations.event_organization.length }})</h5>
              <div class="relation-list">
                <div v-for="(rel, index) in result.relations.event_organization" :key="index" class="relation-item">
                  <span class="rel-source">{{ rel.EventName }}</span>
                  <span class="rel-arrow">→</span>
                  <span class="rel-type">{{ rel.relation }}</span>
                  <span class="rel-arrow">→</span>
                  <span class="rel-target">{{ rel.OrgName }}</span>
                  <span v-if="rel.evidence" class="rel-evidence" :title="rel.evidence">📄</span>
                </div>
              </div>
            </div>

            <!-- 事件-事件关系 -->
            <div v-if="result.relations.event_event.length > 0" class="relation-group">
              <h5>⚔️ 事件-事件关系 ({{ result.relations.event_event.length }})</h5>
              <div class="relation-list">
                <div v-for="(rel, index) in result.relations.event_event" :key="index" class="relation-item">
                  <span class="rel-source">{{ rel.EventName_A }}</span>
                  <span class="rel-arrow">→</span>
                  <span class="rel-type">{{ rel.relation }}</span>
                  <span class="rel-arrow">→</span>
                  <span class="rel-target">{{ rel.EventName_B }}</span>
                  <span v-if="rel.evidence" class="rel-evidence" :title="rel.evidence">📄</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 知识图谱可视化 -->
        <div v-if="graphData.nodes.length > 0" class="result-card graph-card">
          <div class="card-header" @click="toggleSection('graph')">
            <h4>🔗 关系图谱可视化</h4>
            <span class="toggle-icon">{{ expandedSections.graph ? '▼' : '▶' }}</span>
          </div>
          <div v-if="expandedSections.graph" class="card-content graph-content">
            <div class="graph-container">
              <kg-graph :data="graphData"></kg-graph>
            </div>
            <div class="graph-legend">
              <div class="legend-item"><span class="legend-dot event"></span> 事件</div>
              <div class="legend-item"><span class="legend-dot person"></span> 人物</div>
              <div class="legend-item"><span class="legend-dot place"></span> 地点</div>
              <div class="legend-item"><span class="legend-dot org"></span> 组织</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted } from 'vue';
import { layer } from '@layui/layui-vue'
import { useRouter } from 'vue-router';
import { extractEntitiesEvents } from '../../api/module/node'
import KgGraph from '../inference/components/KgGraph.vue';
import { formatChatTime } from '../../utils/date';
import { readScoped, writeScoped } from '../../utils/userScopedStorage';
import { useUserStore } from '../../store/user';

const router = useRouter();
const userStore = useUserStore();

// 识别记录的存储 key：按账号隔离（问题二方案 A；老全局记录由 readScoped 归档）
const EXTRACT_HISTORY_KEY = 'extractHistory';

// 状态变量
const inputText = ref('');
const loading = ref(false);
const result = ref<any>(null);
const textLength = ref(0);
const showHistory = ref(true);

// 历史记录接口
interface HistoryItem {
  id: number;
  title: string;
  text: string;
  result: any;
  time: number;
}

// 历史记录列表
const historyList = ref<HistoryItem[]>([]);
const currentHistoryIndex = ref(-1);

// 展开状态
const expandedSections = reactive<Record<string, boolean>>({
  events: true,
  entities: true,
  relations: true,
  graph: true
});

// 示例文本
const examples = [
  {
    title: '商灭夏鸣条之战',
    text: '商汤发起灭夏战争，采取分别翦除夏朝羽翼的策略，各个击破位于夏、商之间的韦、顾、昆吾等夏属国，使夏孤立无援。接着采纳伊尹的建议，率战车70乘，敢死士6000，约会诸侯军由商都沿黄河南岸西进，利用大河掩护，隐蔽进军，待至潼关附近，再渡河而北，攻击夏都西河。夏桀仓促率部应战，酉出拒汤，战于鸣条之野。战前，汤作"汤誓"，列述夏桀之罪，说明代天伐罪之意，激励士气。交战中，商军奋勇攻杀，夏军大败。夏放弃西河东人于山，出太行东南涉河，避走三股，被商军俘获，汤将桀放逐至南巢，历500年之夏亡。'
  },
  {
    title: '牧野之战',
    text: '周武王为了灭商，试探诸侯的态度，利用文王在诸侯中的地位，曾载文王的木主伐商，从者800诸侯。周武王当时虽得到诸侯的拥护，但仍感力量不足，乃在孟津与诸侯结盟后退军。又经过两年的准备，周武王得知商纣王统治集团分崩离析，王族重臣比干被杀，箕子被囚，微子出奔。商军主力远征东夷，朝歌空虚，即率战车三百乘，虎责军三千人，甲士4万5千人向朝歌进军。周军进至孟津时，与庸、蜀、羌、聚、微、卢、彭、濮等诸侯的军队会合。纣王惊闻周军来袭，武装大批奴隶和战争俘虏开往牧野迎战。两军一交战，商军奴隶兵倒戈，周联军乘势攻击，很快夺占商都朝歌。纣王自杀，商亡。'
  }
];

// 计算属性：图谱数据
const graphData = computed(() => {
  if (!result.value) return { nodes: [], lines: [] };

  const nodes: any[] = [];
  const lines: any[] = [];
  const addedNodes = new Set<string>();

  // 添加事件节点
  result.value.events.forEach((event: any) => {
    const key = `Event-${event.EventName}`;
    if (!addedNodes.has(key)) {
      nodes.push({
        id: event.EventName,
        name: event.EventName,
        type: 'Event',
        category: 0,
        symbolSize: 60,
        value: event.DynastyName || ''
      });
      addedNodes.add(key);
    }
  });

  // 添加地点节点
  result.value.entities.places.forEach((place: any) => {
    const key = `Place-${place.geo_name}`;
    if (!addedNodes.has(key)) {
      nodes.push({
        id: place.geo_name,
        name: place.geo_name,
        type: 'Place',
        category: 1,
        symbolSize: 40
      });
      addedNodes.add(key);
    }
  });

  // 添加组织节点
  result.value.entities.organizations.forEach((org: any) => {
    const key = `Org-${org.OrgName}`;
    if (!addedNodes.has(key)) {
      nodes.push({
        id: org.OrgName,
        name: org.OrgName,
        type: 'Organization',
        category: 2,
        symbolSize: 45
      });
      addedNodes.add(key);
    }
  });

  // 添加人物节点
  result.value.entities.persons.forEach((person: any) => {
    const key = `Person-${person.PersonName}`;
    if (!addedNodes.has(key)) {
      nodes.push({
        id: person.PersonName,
        name: person.PersonName,
        type: 'Person',
        category: 3,
        symbolSize: 35
      });
      addedNodes.add(key);
    }
  });

  // 添加事件-地点关系
  result.value.relations.event_place.forEach((rel: any) => {
    const placeName = rel.PlaceName || rel.modern_name;
    if (rel.EventName && placeName) {
      lines.push({
        from: rel.EventName,
        to: placeName,
        text: rel.relation || '发生地',
        inferred: false
      });
    }
  });

  // 添加事件-人物关系
  result.value.relations.event_person.forEach((rel: any) => {
    if (rel.EventName && rel.PersonName) {
      lines.push({
        from: rel.EventName,
        to: rel.PersonName,
        text: rel.relation || '参与',
        inferred: false
      });
    }
  });

  // 添加事件-组织关系
  result.value.relations.event_organization.forEach((rel: any) => {
    if (rel.EventName && rel.OrgName) {
      lines.push({
        from: rel.EventName,
        to: rel.OrgName,
        text: rel.relation || '参战',
        inferred: false
      });
    }
  });

  // 添加事件-事件关系
  result.value.relations.event_event.forEach((rel: any) => {
    if (rel.EventName_A && rel.EventName_B) {
      lines.push({
        from: rel.EventName_A,
        to: rel.EventName_B,
        text: rel.relation || '关联',
        inferred: false
      });
    }
  });

  return { nodes, lines };
});

// 方法
function updateCharCount() {
  textLength.value = inputText.value.length;
}

function toggleSection(section: string) {
  expandedSections[section] = !expandedSections[section];
}

function toggleHistory() {
  showHistory.value = !showHistory.value;
}

function truncateText(text: string, maxLen: number) {
  if (!text) return '-';
  return text.length > maxLen ? text.substring(0, maxLen) + '...' : text;
}

async function startExtract() {
  if (!inputText.value.trim() || loading.value) return;

  loading.value = true;
  result.value = null;

  try {
    const response = await extractEntitiesEvents({ text: inputText.value });

    if (response && response.code === 200) {
      result.value = response.data;
      // 识别成功后保存到历史记录
      saveToHistory();
    } else {
      layer.msg(response?.msg || '识别失败，请重试', { icon: 2 });
    }
  } catch (error: any) {
    console.error('识别请求失败:', error);
    layer.msg('识别请求失败: ' + (error instanceof Error ? error.message : String(error)), { icon: 2 });
  } finally {
    loading.value = false;
  }
}

function clearAll() {
  inputText.value = '';
  result.value = null;
  textLength.value = 0;
}

function useExample(example: any) {
  inputText.value = example.text;
  textLength.value = example.text.length;
}

// 从localStorage加载历史记录（按账号隔离）
function loadHistoryFromStorage() {
  try {
    const stored = readScoped<any[]>(EXTRACT_HISTORY_KEY, userStore.userInfo?.id, []);
    if (Array.isArray(stored)) {
      historyList.value = stored;
    }
  } catch (error) {
    console.error('加载历史记录失败:', error);
  }
}

// 保存历史记录到localStorage（按账号隔离）
function saveHistoryToStorage() {
  try {
    writeScoped(EXTRACT_HISTORY_KEY, userStore.userInfo?.id, historyList.value);
  } catch (error) {
    console.error('保存历史记录失败:', error);
  }
}

// 创建新的识别任务
function createNewExtract() {
  inputText.value = '';
  result.value = null;
  textLength.value = 0;
  currentHistoryIndex.value = -1;
}

// 保存当前识别结果到历史记录
function saveToHistory() {
  if (!result.value || !inputText.value.trim()) return;

  const title = inputText.value.substring(0, 20) + (inputText.value.length > 20 ? '...' : '');
  const historyItem: HistoryItem = {
    id: Date.now(),
    title: title,
    text: inputText.value,
    result: result.value,
    time: Date.now()
  };

  // 检查是否已存在相同文本的记录
  const existingIndex = historyList.value.findIndex(item => item.text === inputText.value);
  if (existingIndex >= 0) {
    // 更新现有记录
    historyList.value[existingIndex] = historyItem;
    currentHistoryIndex.value = existingIndex;
  } else {
    // 添加新记录到顶部
    historyList.value.unshift(historyItem);
    currentHistoryIndex.value = 0;
  }

  // 限制历史记录数量为50条
  if (historyList.value.length > 50) {
    historyList.value = historyList.value.slice(0, 50);
  }

  saveHistoryToStorage();
}

// 加载历史记录
function loadHistory(index: number) {
  const item = historyList.value[index];
  if (!item) return;

  inputText.value = item.text;
  result.value = item.result;
  textLength.value = item.text.length;
  currentHistoryIndex.value = index;
}

// 删除历史记录
function deleteHistory(index: number) {
  historyList.value.splice(index, 1);
  if (currentHistoryIndex.value === index) {
    currentHistoryIndex.value = -1;
    result.value = null;
  } else if (currentHistoryIndex.value > index) {
    currentHistoryIndex.value--;
  }
  saveHistoryToStorage();
}

// 导出为Markdown
function exportToMarkdown() {
  if (!result.value) {
    layer.msg('没有可导出的识别结果', { icon: 0 });
    return;
  }

  try {
    let md = `# 文本实体与事件识别结果\n\n`;
    md += `**导出时间**: ${new Date().toLocaleString('zh-CN')}\n\n`;
    md += `## 原始文本\n\n${inputText.value}\n\n`;

    // 统计信息
    if (result.value.summary) {
      md += `## 识别统计\n\n`;
      md += `| 类型 | 数量 |\n|------|------|\n`;
      md += `| 地点 | ${result.value.summary.place_count || 0} |\n`;
      md += `| 组织 | ${result.value.summary.organization_count || 0} |\n`;
      md += `| 人物 | ${result.value.summary.person_count || 0} |\n`;
      md += `| 事件 | ${result.value.summary.event_count || 0} |\n`;
      md += `| 关系 | ${result.value.summary.relation_count || 0} |\n`;
      md += `| 耗时 | ${result.value.process_time || 0}s |\n\n`;
    }

    // 事件
    if (result.value.events?.length > 0) {
      md += `## ⚔️ 识别到的事件 (${result.value.events.length})\n\n`;
      result.value.events.forEach((event: any, idx: number) => {
        md += `### ${idx + 1}. ${event.EventName}\n\n`;
        if (event.EventType) md += `- **类型**: ${event.EventType}\n`;
        if (event.DynastyName) md += `- **朝代**: ${event.DynastyName}\n`;
        if (event.StartDate) md += `- **开始时间**: ${event.StartDate}\n`;
        if (event.EndDate) md += `- **结束时间**: ${event.EndDate}\n`;
        if (event.Place) md += `- **地点**: ${event.Place}\n`;
        if (event.Aggressor) md += `- **进攻方**: ${event.Aggressor}\n`;
        if (event.Defender) md += `- **防守方**: ${event.Defender}\n`;
        if (event.Commanders) md += `- **指挥官**: ${event.Commanders}\n`;
        if (event.Result) md += `- **结果**: ${event.Result}\n`;
        if (event.source_text) md += `- **原文**: ${event.source_text}\n`;
        md += `\n`;
      });
    }

    // 实体
    md += `## 🏷️ 识别到的实体\n\n`;

    if (result.value.entities?.places?.length > 0) {
      md += `### 📍 地点 (${result.value.entities.places.length})\n\n`;
      md += `| 地名 | 现代名 | 朝代 | 省 | 市 |\n|------|--------|------|-----|-----|\n`;
      result.value.entities.places.forEach((p: any) => {
        md += `| ${p.geo_name || '-'} | ${p.modern_name || '-'} | ${p.DynastyName || '-'} | ${p.Province || '-'} | ${p.City || '-'} |\n`;
      });
      md += `\n`;
    }

    if (result.value.entities?.organizations?.length > 0) {
      md += `### 🏛️ 组织 (${result.value.entities.organizations.length})\n\n`;
      md += `| 组织名 | 类型 | 朝代 |\n|--------|------|------|\n`;
      result.value.entities.organizations.forEach((o: any) => {
        md += `| ${o.OrgName || '-'} | ${o.OrgType || '-'} | ${o.DynastyName || '-'} |\n`;
      });
      md += `\n`;
    }

    if (result.value.entities?.persons?.length > 0) {
      md += `### 👤 人物 (${result.value.entities.persons.length})\n\n`;
      md += `| 姓名 | 朝代 | 所属组织 | 角色 | 备注 |\n|------|------|----------|------|------|\n`;
      result.value.entities.persons.forEach((p: any) => {
        md += `| ${p.PersonName || '-'} | ${p.DynastyName || '-'} | ${p.OrgName || '-'} | ${p.Role || '-'} | ${p.Note || '-'} |\n`;
      });
      md += `\n`;
    }

    // 关系
    if (result.value.relations) {
      md += `## 🔗 识别到的关系\n\n`;

      if (result.value.relations.event_place?.length > 0) {
        md += `### 事件-地点关系\n\n`;
        md += `| 事件 | 关系 | 地点 | 证据 |\n|------|------|------|------|\n`;
        result.value.relations.event_place.forEach((r: any) => {
          md += `| ${r.EventName || '-'} | ${r.relation || '-'} | ${r.PlaceName || r.modern_name || '-'} | ${r.evidence || '-'} |\n`;
        });
        md += `\n`;
      }

      if (result.value.relations.event_person?.length > 0) {
        md += `### 事件-人物关系\n\n`;
        md += `| 事件 | 关系 | 人物 | 证据 |\n|------|------|------|------|\n`;
        result.value.relations.event_person.forEach((r: any) => {
          md += `| ${r.EventName || '-'} | ${r.relation || '-'} | ${r.PersonName || '-'} | ${r.evidence || '-'} |\n`;
        });
        md += `\n`;
      }

      if (result.value.relations.event_organization?.length > 0) {
        md += `### 事件-组织关系\n\n`;
        md += `| 事件 | 关系 | 组织 | 证据 |\n|------|------|------|------|\n`;
        result.value.relations.event_organization.forEach((r: any) => {
          md += `| ${r.EventName || '-'} | ${r.relation || '-'} | ${r.OrgName || '-'} | ${r.evidence || '-'} |\n`;
        });
        md += `\n`;
      }
    }

    // 创建下载
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `识别结果_${new Date().toISOString().split('T')[0]}.md`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  } catch (error) {
    console.error('导出Markdown失败:', error);
    layer.msg('导出失败', { icon: 2 });
  }
}

// 导出为JSON
function exportToJSON() {
  if (!result.value) {
    layer.msg('没有可导出的识别结果', { icon: 0 });
    return;
  }

  try {
    const exportData = {
      text: inputText.value,
      exportTime: new Date().toISOString(),
      result: result.value
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `识别结果_${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  } catch (error) {
    console.error('导出JSON失败:', error);
    layer.msg('导出失败', { icon: 2 });
  }
}

// 组件挂载时加载历史记录
onMounted(async () => {
  // 先确保拿到账号 id 再读历史（否则退回共享 key，见 store.ensureUserInfo）
  await userStore.ensureUserInfo();
  loadHistoryFromStorage();
});
</script>

<style scoped>
.text-extract-container {
  display: flex;
  height: 100%;
  gap: 16px;
  padding: 16px;
  background: #f5f7fa;
}

/* 左侧输入区域 */
.input-section {
  width: 380px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  background: linear-gradient(135deg, #009688 0%, #26a69a 100%);
  color: white;
}

.section-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.char-count {
  font-size: 12px;
  opacity: 0.9;
}

.textarea-wrapper {
  flex: 1;
  padding: 14px;
}

.textarea-wrapper textarea {
  width: 100%;
  height: 100%;
  min-height: 280px;
  padding: 14px;
  border: 2px solid #e0e0e0;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
  resize: none;
  transition: border-color 0.3s;
  font-family: inherit;
}

.textarea-wrapper textarea:focus {
  outline: none;
  border-color: #009688;
}

.action-bar {
  display: flex;
  gap: 10px;
  padding: 0 14px 14px;
}

.extract-button {
  flex: 1;
  padding: 10px 20px;
  background: linear-gradient(135deg, #009688 0%, #26a69a 100%);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.extract-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 150, 136, 0.4);
}

.extract-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.clear-button {
  padding: 10px 16px;
  background: #f5f5f5;
  color: #666;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
}

.clear-button:hover:not(:disabled) {
  background: #e0e0e0;
}

.examples-section {
  padding: 14px;
  border-top: 1px solid #eee;
}

.examples-title {
  font-size: 12px;
  color: #666;
  margin-bottom: 10px;
}

.examples-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.example-item {
  padding: 6px 14px;
  background: #f0f9f6;
  color: #009688;
  border-radius: 16px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.3s;
  border: 1px dashed #009688;
}

.example-item:hover {
  background: #009688;
  color: white;
  border-style: solid;
}

/* 右侧结果区域 */
.result-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow-y: auto;
}

/* 统计栏 */
.stats-bar {
  display: flex;
  gap: 20px;
  padding: 12px 20px;
  background: linear-gradient(135deg, #e0f2f1 0%, #b2dfdb 100%);
  border-bottom: 1px solid #e0e0e0;
  flex-wrap: wrap;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.stat-icon {
  font-size: 16px;
}

.stat-label {
  font-size: 12px;
  color: #666;
}

.stat-value {
  font-size: 14px;
  font-weight: 600;
  color: #009688;
}

/* 空状态和加载状态 */
.empty-state,
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 40px;
  text-align: center;
}

.empty-icon,
.loading-icon {
  font-size: 48px;
  margin-bottom: 16px;
}

.empty-text {
  font-size: 16px;
  color: #333;
  margin-bottom: 8px;
}

.empty-hint {
  font-size: 13px;
  color: #999;
}

.loading-text {
  font-size: 15px;
  color: #333;
  margin-bottom: 8px;
}

.loading-hint {
  font-size: 13px;
  color: #999;
}

/* 加载动画 */
.loading-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-radius: 50%;
  border-top-color: white;
  animation: spin 1s ease-in-out infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 结果内容 */
.result-content {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 结果卡片 */
.result-card {
  background: #f8fafc;
  border-radius: 10px;
  border: 1px solid #e0e0e0;
  overflow: hidden;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #fff;
  cursor: pointer;
  border-bottom: 1px solid #e0e0e0;
}

.card-header:hover {
  background: #f0f9f6;
}

.card-header h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

.toggle-icon {
  font-size: 12px;
  color: #666;
}

.card-content {
  padding: 16px;
}

/* 事件卡片 */
.event-card {
  background: #fff;
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 12px;
  border: 1px solid #e8e8e8;
}

.event-card:last-child {
  margin-bottom: 0;
}

.event-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.event-name {
  font-size: 15px;
  font-weight: 600;
  color: #c62828;
}

.event-type {
  padding: 2px 8px;
  background: #ffebee;
  color: #c62828;
  border-radius: 4px;
  font-size: 11px;
}

.event-attributes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}

.attr-item {
  display: flex;
  gap: 6px;
  font-size: 12px;
}

.attr-label {
  color: #666;
  white-space: nowrap;
}

.attr-value {
  color: #333;
}

.event-source {
  font-size: 12px;
  color: #666;
  padding-top: 8px;
  border-top: 1px dashed #e0e0e0;
}

.source-label {
  color: #999;
}

.source-text {
  color: #666;
  font-style: italic;
}

/* 实体分组 */
.entity-group {
  margin-bottom: 16px;
}

.entity-group:last-child {
  margin-bottom: 0;
}

.entity-group h5 {
  margin: 0 0 10px 0;
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.entity-table-wrapper {
  overflow-x: auto;
}

.entity-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.entity-table th {
  background: #f0f9f6;
  padding: 8px 10px;
  text-align: left;
  font-weight: 600;
  color: #009688;
  border-bottom: 2px solid #009688;
  white-space: nowrap;
}

.entity-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #e8e8e8;
  color: #333;
}

.entity-table tr:hover td {
  background: #f8f8f8;
}

.entity-name {
  font-weight: 600;
  color: #1565c0;
}

.source-cell {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #999;
  font-size: 11px;
}

/* 关系分组 */
.relation-group {
  margin-bottom: 16px;
}

.relation-group:last-child {
  margin-bottom: 0;
}

.relation-group h5 {
  margin: 0 0 10px 0;
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.relation-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.relation-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #fff;
  border-radius: 6px;
  border: 1px solid #e8e8e8;
  font-size: 12px;
}

.rel-source {
  font-weight: 600;
  color: #1565c0;
}

.rel-arrow {
  color: #999;
}

.rel-type {
  padding: 2px 8px;
  background: #e0f2f1;
  color: #009688;
  border-radius: 4px;
  font-size: 11px;
}

.rel-target {
  font-weight: 600;
  color: #c62828;
}

.rel-evidence {
  margin-left: auto;
  cursor: help;
}

/* 图谱卡片 */
.graph-card .card-content {
  padding: 0;
}

.graph-content {
  display: flex;
  flex-direction: column;
}

.graph-container {
  height: 450px;
  padding: 16px;
}

.graph-legend {
  display: flex;
  gap: 16px;
  padding: 12px 16px;
  background: #f8f8f8;
  border-top: 1px solid #e0e0e0;
  justify-content: center;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #666;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.legend-dot.event {
  background: #c62828;
}

.legend-dot.person {
  background: #1565c0;
}

.legend-dot.place {
  background: #e65100;
}

.legend-dot.org {
  background: #6a1b9a;
}

/* 历史记录侧边栏 */
.history-sidebar {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.history-sidebar.sidebar-collapsed {
  width: 0;
  min-width: 0;
  padding: 0;
  margin: 0;
  overflow: hidden;
  border: none;
  box-shadow: none;
}

.sidebar-header {
  padding: 12px;
  border-bottom: 1px solid #eee;
  background: linear-gradient(135deg, #f5f7fa 0%, #e8f4f8 100%);
}

.sidebar-header-top {
  display: flex;
  gap: 8px;
  align-items: center;
}

.new-extract-button {
  flex: 1;
  padding: 10px;
  border: none;
  border-radius: 8px;
  background: #009688;
  color: white;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s;
}

.new-extract-button:hover {
  background: #00796b;
  transform: translateY(-1px);
}

.collapse-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: #f0f0f0;
  cursor: pointer;
  transition: all 0.3s;
  font-size: 12px;
  color: #666;
}

.collapse-btn:hover {
  background: #e0e0e0;
  color: #333;
}

/* 展开按钮 */
.expand-btn {
  width: 36px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  cursor: pointer;
  transition: all 0.3s;
  padding: 14px 8px;
}

.expand-btn:hover {
  background: #f0f9f6;
  box-shadow: 0 4px 16px rgba(0, 150, 136, 0.15);
}

.expand-icon {
  font-size: 14px;
  color: #009688;
}

.expand-text {
  writing-mode: vertical-rl;
  font-size: 12px;
  color: #666;
  letter-spacing: 2px;
}

.history-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.history-item {
  display: flex;
  align-items: center;
  padding: 10px;
  margin-bottom: 6px;
  border-radius: 8px;
  border: 1px solid #eee;
  cursor: pointer;
  transition: all 0.3s;
}

.history-item:hover {
  background: #f5f5f5;
  border-color: #ddd;
}

.history-item.active {
  background: #e0f2f1;
  border-color: #009688;
}

.history-item-content {
  flex: 1;
  overflow: hidden;
}

.history-title {
  font-size: 13px;
  font-weight: 500;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 4px;
}

.history-time {
  font-size: 11px;
  color: #999;
  margin-bottom: 4px;
}

.history-stats {
  display: flex;
  gap: 8px;
  font-size: 11px;
  color: #666;
}

.history-actions {
  opacity: 0;
  transition: opacity 0.3s;
}

.history-item:hover .history-actions {
  opacity: 1;
}

.delete-icon {
  cursor: pointer;
  font-size: 14px;
}

.history-empty {
  text-align: center;
  color: #999;
  font-size: 13px;
  padding: 20px;
}

/* 结果头部 */
.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid #eee;
}

.result-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #333;
}

.result-actions {
  display: flex;
  gap: 8px;
}

.export-btn {
  padding: 6px 12px;
  border: 1px solid #009688;
  border-radius: 6px;
  background: white;
  color: #009688;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.3s;
}

.export-btn:hover {
  background: #009688;
  color: white;
}

/* 响应式 */
@media (max-width: 1024px) {
  .text-extract-container {
    flex-direction: column;
  }

  .history-sidebar {
    width: 100%;
    max-height: 200px;
    border-radius: 12px;
  }

  .history-sidebar.sidebar-collapsed {
    max-height: 0;
    width: 100%;
  }

  .expand-btn {
    width: 100%;
    height: auto;
    flex-direction: row;
    padding: 8px 16px;
    border-radius: 12px;
  }

  .expand-text {
    writing-mode: horizontal-tb;
    letter-spacing: normal;
  }

  .input-section {
    width: 100%;
  }

  .textarea-wrapper textarea {
    min-height: 180px;
  }
}
</style>
