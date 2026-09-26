"""
抽取编排的共享入口。

**为什么要有这个模块。** "分段循环 + 三阶段调用 + 去重收集 + 失败诊断"只能有一份实现：
backend 的 `llm_pipeline.extract_all_optimized` 与离线链路的 `main.py:process_long_text`
/`process_single_file` 曾经是两份平行实现，而平行实现已经漂移过一次——"实体对象被当
`place_list` 传进提示词"这个 bug 就是它的产物。现在编排只有这一份，两边各自的后处理
用钩子挂进来，结构上不会再漂。

**分工**（共享编排与调用方各自的边界）：

| 环节 | 归谁 |
| --- | --- |
| 分段、三阶段调用、跨段收集、失败诊断（`stage_ok` / `partial_errors`） | **本模块** |
| 字段归一（backend 的 `_to_str` / `_normalize_dynasty` / `_normalize_role` / `_normalize_event_name`） | backend，挂 `on_entities_ready` / `on_events_ready` |
| `enrich_entities_from_events` / `cleanup_entity_conflicts` / `finalize_outputs` | 离线链路，挂 `on_entities_ready` / `on_chunk_done` |
| 分段缓存（离线链路独有） | 本模块提供，双方按需开关 |

**统一口径**（两份实现不一致时以这边为准，逐条都在"两边一致"的前提下选的）：

1. **实体名列表**：只放非空名称、去掉首尾空白、段内重复保留。
   不能把空名一起 join 进提示词（`geo_name` 为 `None` 时还会直接抛异常）。
2. **失败策略**：**按阶段继续**——某一段的某阶段失败只作废该阶段，其余阶段照跑，失败逐条进
   `partial_errors`、`stage_ok` 只给成功的阶段计数。一段里任一阶段失败就把整段三个结果
   全清空是错的（局部失败不该整体作废）。
   `ChunkExtraction.ok`（三阶段全成功）仍被离线链路用来决定"要不要写缓存"。
3. **关系阶段**：本段（经 `on_events_ready` 过滤后的）事件列表为空时**跳过**调用。
   `RelationExtractor.extract` 本身对空事件就是早返回，所以这条只影响控制台提示文字。
4. **分段失败兜底**：切分抛异常时退化成"整篇一段"，不让整个任务因切分崩掉。

**不做什么**：不去重（backend 按名字去重、离线链路走 `ResultMerger`，语义不同，各自保留）；
不抛 `ExtractionUnavailable`（那是 backend 接口层的口径，由 backend 自己按 `stage_ok` 判定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from war_extraction.config import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP
from war_extraction.core.text_splitter import TextSplitter
from war_extraction.extractors.entity_extractor import EntityExtractor
from war_extraction.extractors.event_extractor import EventExtractor
from war_extraction.extractors.relation_extractor import RelationExtractor
from war_extraction.models import (
    EntityExtractionResult,
    Event,
    EventEventRelation,
    EventExtractionResult,
    EventOrganizationRelation,
    EventPersonRelation,
    EventPlaceRelation,
    OrganizationEntity,
    PersonEntity,
    PlaceEntity,
    RelationExtractionResult,
)

#: 三个阶段的固定顺序与诊断键名
STAGES = ("entity", "event", "relation")

#: 钩子签名（都在"该段抽取完之后、进入下一阶段或落盘之前"被调用）
#:   on_entities_ready(chunk_text, entities, events) -> entities
#:       影响传给事件阶段/关系阶段的实体名列表（离线链路在这里做实体回填与清洗）
#:   on_events_ready(chunk_text, events) -> events
#:       决定"要不要跑关系阶段"（backend 在这里做跨段事件去重：本段事件全是旧的就别跑了）
#:   on_chunk_done(chunk_text, entities, events, relations) -> (entities, events, relations)
#:       落盘 / 写缓存之前的最后一道后处理（离线链路在这里跑 finalize_outputs）
EntityHook = Callable[[str, EntityExtractionResult, EventExtractionResult], EntityExtractionResult]
EventGateHook = Callable[[str, List[Event]], List[Event]]
ChunkDoneHook = Callable[
    [str, EntityExtractionResult, EventExtractionResult, RelationExtractionResult],
    Tuple[EntityExtractionResult, EventExtractionResult, RelationExtractionResult],
]
ProgressHook = Callable[..., None]


def entities_from_dict(data: Dict) -> EntityExtractionResult:
    """把缓存里的字典还原成实体结果。"""
    return EntityExtractionResult(
        places=[PlaceEntity(**p) for p in data.get("places", [])],
        organizations=[OrganizationEntity(**o) for o in data.get("organizations", [])],
        persons=[PersonEntity(**p) for p in data.get("persons", [])],
    )


def events_from_dict(data: Dict) -> EventExtractionResult:
    """把缓存里的字典还原成事件结果。"""
    return EventExtractionResult(events=[Event(**e) for e in data.get("events", [])])


def relations_from_dict(data: Dict) -> RelationExtractionResult:
    """把缓存里的字典还原成关系结果。"""
    return RelationExtractionResult(
        event_place_relations=[EventPlaceRelation(**r) for r in data.get("event_place_relations", [])],
        event_organization_relations=[
            EventOrganizationRelation(**r) for r in data.get("event_organization_relations", [])],
        event_person_relations=[EventPersonRelation(**r) for r in data.get("event_person_relations", [])],
        event_event_relations=[EventEventRelation(**r) for r in data.get("event_event_relations", [])],
    )


def _names(values) -> List[str]:
    """实体名列表：去空白、丢空名、保留段内重复（统一口径第 1 条）。"""
    names = []
    for value in values:
        name = ("" if value is None else str(value)).strip()
        if name:
            names.append(name)
    return names


@dataclass
class ChunkExtraction:
    """单个文本段的三阶段结果（已过调用方的钩子）。"""

    index: int
    start: int
    end: int
    text: str
    entities: EntityExtractionResult
    events: EventExtractionResult
    relations: RelationExtractionResult
    #: 三阶段是否都成功——离线链路据此决定要不要把这一段写进缓存
    ok: bool = True
    from_cache: bool = False
    #: 本段的失败说明（与 diagnostics["partial_errors"] 同一批文案）
    errors: List[str] = field(default_factory=list)


@dataclass
class ExtractionRun:
    """一次抽取的全部段落与诊断。"""

    chunks: List[ChunkExtraction]
    diagnostics: Dict[str, Any]

    @property
    def entities(self) -> List[EntityExtractionResult]:
        return [chunk.entities for chunk in self.chunks]

    @property
    def events(self) -> List[EventExtractionResult]:
        return [chunk.events for chunk in self.chunks]

    @property
    def relations(self) -> List[RelationExtractionResult]:
        return [chunk.relations for chunk in self.chunks]

    @property
    def stage_ok(self) -> Dict[str, int]:
        return dict(self.diagnostics.get("stage_ok") or {})

    @property
    def partial_errors(self) -> List[str]:
        return list(self.diagnostics.get("partial_errors") or [])


def run_extraction(llm, text: str, *, splitter: Optional[TextSplitter] = None,
                   chunk_size: Optional[int] = None, overlap: Optional[int] = None,
                   cache=None, cache_context_meta: Optional[Dict] = None,
                   read_cache: bool = False, write_cache: bool = False,
                   on_entities_ready: Optional[EntityHook] = None,
                   on_events_ready: Optional[EventGateHook] = None,
                   on_chunk_done: Optional[ChunkDoneHook] = None,
                   on_chunk_start: Optional[ProgressHook] = None,
                   on_chunk_finish: Optional[ProgressHook] = None) -> ExtractionRun:
    """
    分段跑完"实体 → 事件 → 关系"三阶段，返回逐段结果与失败诊断。

    Args:
        llm: LLM 客户端（只要有 `call(prompt, ...)`）
        text: 待抽取文本
        splitter: 分段器；给了就以它为准（chunk_size/overlap 忽略）
        chunk_size / overlap: 未给 splitter 时按它构造，默认取 config 的 1800 / 200
        cache: 分段缓存（`CacheManager` 实例）；None 表示不读不写
        cache_context_meta: 缓存上下文（`config.cache_context(...)`），键的一部分
        read_cache / write_cache: 缓存开关。**只有三阶段全成功的段才写缓存**，
            失败段下次运行会自动重试（原离线链路口径）
        on_entities_ready: 实体阶段的钩子，返回值用于构建后续阶段的实体名列表
        on_events_ready: 事件阶段的"门"钩子，返回空列表则跳过关系阶段
        on_chunk_done: 每段最后的钩子（落盘/写缓存前）
        on_chunk_start(index, total, start, end) / on_chunk_finish(chunk): 进度回调

    Returns:
        `ExtractionRun`
    """
    if splitter is None:
        splitter = TextSplitter(chunk_size=chunk_size or DEFAULT_CHUNK_SIZE,
                               overlap=overlap if overlap is not None else DEFAULT_OVERLAP)

    try:
        chunks = splitter.split(text)
    except Exception as exc:  # noqa: BLE001
        # 统一口径第 4 条：切分失败退化成"整篇一段"，不让整个任务因切分崩掉
        print(f"文本切分失败（{type(exc).__name__}: {exc}），退化为整篇单段处理")
        chunks = [(0, len(text), text)]

    entity_extractor = EntityExtractor(llm)
    event_extractor = EventExtractor(llm)
    relation_extractor = RelationExtractor(llm)

    stage_ok = {stage: 0 for stage in STAGES}
    partial_errors: List[str] = []
    results: List[ChunkExtraction] = []
    cache_hits = 0

    total = len(chunks)
    for index, (start, end, chunk_text) in enumerate(chunks, 1):
        # 空白段直接跳过：给它跑三阶段只会烧额度、换回空结果
        if not chunk_text.strip():
            continue
        if on_chunk_start:
            on_chunk_start(index, total, start, end)

        chunk = _run_single_chunk(
            index=index, start=start, end=end, chunk_text=chunk_text,
            entity_extractor=entity_extractor, event_extractor=event_extractor,
            relation_extractor=relation_extractor,
            cache=cache, cache_context_meta=cache_context_meta,
            read_cache=read_cache, write_cache=write_cache,
            on_entities_ready=on_entities_ready, on_events_ready=on_events_ready,
            on_chunk_done=on_chunk_done, stage_ok=stage_ok, partial_errors=partial_errors,
        )
        if chunk.from_cache:
            cache_hits += 1
        results.append(chunk)
        if on_chunk_finish:
            on_chunk_finish(chunk)

    diagnostics = {
        "stage_ok": stage_ok,
        "partial_errors": partial_errors,
        "chunks": total,
        "cache_hits": cache_hits,
        "failed_chunks": sum(1 for chunk in results if not chunk.ok),
    }
    return ExtractionRun(chunks=results, diagnostics=diagnostics)


def _run_single_chunk(*, index: int, start: int, end: int, chunk_text: str,
                      entity_extractor, event_extractor, relation_extractor,
                      cache, cache_context_meta, read_cache: bool, write_cache: bool,
                      on_entities_ready, on_events_ready, on_chunk_done,
                      stage_ok: Dict[str, int], partial_errors: List[str]) -> ChunkExtraction:
    """跑一个文本段；缓存命中时直接走"还原 + on_chunk_done"。"""
    cached = cache.get(chunk_text, cache_context_meta) if (cache and read_cache) else None
    if cached:
        entities = entities_from_dict(cached.get("entities") or {})
        events = events_from_dict(cached.get("events") or {})
        relations = relations_from_dict(cached.get("relations") or {})
        if on_chunk_done:
            entities, events, relations = on_chunk_done(chunk_text, entities, events, relations)
        return ChunkExtraction(index=index, start=start, end=end, text=chunk_text,
                              entities=entities, events=events, relations=relations,
                              ok=True, from_cache=True)

    chunk_errors: List[str] = []

    def fail(stage: str, exc: Exception) -> None:
        # 文案格式必须是 "[start-end] 阶段失败: exc"：backend 界面直接展示这串文字
        message = "[%s-%s] %s阶段失败: %s" % (start, end, stage, exc)
        print(message)
        chunk_errors.append(message)
        partial_errors.append(message)

    entities = EntityExtractionResult()
    try:
        entities = entity_extractor.extract(chunk_text)
        stage_ok["entity"] += 1
    except Exception as exc:  # noqa: BLE001
        fail("实体抽取", exc)

    # 事件阶段的实体名列表：实体阶段抽出来的那一批（含重复、去空白，统一口径第 1 条）
    place_list = "、".join(_names(p.geo_name for p in entities.places))
    org_list = "、".join(_names(o.OrgName for o in entities.organizations))
    person_list = "、".join(_names(p.PersonName for p in entities.persons))

    events = EventExtractionResult()
    try:
        events = event_extractor.extract(chunk_text, place_list, org_list, person_list)
        stage_ok["event"] += 1
    except Exception as exc:  # noqa: BLE001
        fail("事件抽取", exc)

    if on_entities_ready:
        # 实体阶段的钩子：backend 在这里做"跨段去重收集"（返回值不变），
        # 离线链路在这里做 enrich_entities_from_events + cleanup_entity_conflicts（返回值变了）。
        # 钩子返回后的名称列表才是关系阶段要用的那一份（两边都是这个顺序，不能挪）。
        entities = on_entities_ready(chunk_text, entities, events)
        place_list = "、".join(_names(p.geo_name for p in entities.places))
        org_list = "、".join(_names(o.OrgName for o in entities.organizations))
        person_list = "、".join(_names(p.PersonName for p in entities.persons))

    # "门"钩子：返回空列表就不跑关系阶段（backend 用它在"本段事件全是旧事件"时省一次调用）
    gate_events = on_events_ready(chunk_text, events.events) if on_events_ready else list(events.events)

    relations = RelationExtractionResult()
    if gate_events:
        try:
            relations = relation_extractor.extract(chunk_text, events.events,
                                                   place_list, org_list, person_list)
            stage_ok["relation"] += 1
        except Exception as exc:  # noqa: BLE001
            fail("关系抽取", exc)

    if on_chunk_done:
        entities, events, relations = on_chunk_done(chunk_text, entities, events, relations)

    ok = not chunk_errors
    if cache and write_cache and ok:
        try:
            cache.set(chunk_text, {
                "entities": entities.model_dump(),
                "events": events.model_dump(),
                "relations": relations.model_dump(),
            }, cache_context_meta)
        except Exception as exc:  # noqa: BLE001
            print(f"  缓存保存失败: {exc}")

    return ChunkExtraction(index=index, start=start, end=end, text=chunk_text,
                          entities=entities, events=events, relations=relations,
                          ok=ok, errors=chunk_errors)
