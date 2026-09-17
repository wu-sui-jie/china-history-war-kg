<script setup lang="ts">
import PanelEmpty from '@/components/panel/PanelEmpty.vue'
import type { EntityCard } from '@/types/contract'

defineProps<{ cards: EntityCard[] }>()
</script>

<template>
  <div v-if="cards.length" class="entity-card-list">
    <article v-for="card in cards" :key="card.entity_id" class="entity-card">
      <header class="entity-card-head">
        <span class="entity-type-badge" :data-type="card.type">{{ card.type }}</span>
        <h3>{{ card.name }}</h3>
      </header>
      <dl class="entity-card-meta">
        <div v-if="card.dynasty">
          <dt>朝代</dt>
          <dd>{{ card.dynasty }}</dd>
        </div>
        <div v-if="card.start_date || card.end_date">
          <dt>时间</dt>
          <dd>{{ card.start_date || '' }}{{ card.end_date ? ' 至 ' + card.end_date : '' }}</dd>
        </div>
        <div v-if="card.event_type">
          <dt>战争类型</dt>
          <dd>{{ card.event_type }}</dd>
        </div>
        <div v-if="card.role">
          <dt>身份</dt>
          <dd>{{ card.role }}</dd>
        </div>
        <div v-if="card.org">
          <dt>所属</dt>
          <dd>{{ card.org }}{{ card.org_type ? '（' + card.org_type + '）' : '' }}</dd>
        </div>
        <div v-if="card.province || card.city || card.modern_name">
          <dt>位置</dt>
          <dd>
            {{ [card.province, card.city].filter(Boolean).join(' · ') }}
            {{ card.modern_name ? '（今' + card.modern_name + '）' : '' }}
          </dd>
        </div>
        <div v-if="card.aliases && card.aliases.length">
          <dt>别名</dt>
          <dd>{{ card.aliases.join('、') }}</dd>
        </div>
      </dl>
      <p v-if="card.description" class="entity-card-desc">{{ card.description }}</p>
      <footer v-if="card.source" class="entity-card-source">来源：{{ card.source }}</footer>
    </article>
  </div>
  <PanelEmpty v-else text="本问题暂无命中的实体信息卡。" />
</template>
