"""
规则引擎与大模型集成推理模块

功能说明:
    - 规则引擎：基于预定义规则（rules/rule_base.json）进行推理
    - 大模型集成：使用deepseek-r1:7b模型生成自然语言回答
    - 知识图谱查询：从Neo4j查询实体关系和路径
    - 智能问答：结合规则推理和大模型生成最终答案

处理流程:
    1. 从问题中提取实体
    2. 从Neo4j查询实体信息和关系
    3. 应用规则推理生成隐含关系
    4. 使用大模型生成最终回答
    5. 返回回答和知识图谱可视化数据
"""
import json
import threading
import time
import ollama
import hashlib
from typing import List, Dict, Any, Optional
from collections import OrderedDict

from common_utils import lru_get as _lru_get
from dynasty_data import DYNASTY_SCOPE_MAP
from common_utils import lru_set as _lru_set
from concurrent.futures import ThreadPoolExecutor, as_completed

from logging_util import get_logger

logger = get_logger(__name__)


class _ThreadLocalNeo4j:
    """把 neo4j_db 实例包装成「每线程一个 Graph」的代理（BE-6）。

    py2neo 的 Graph 不是线程安全的（并发 run 会共用同一连接与事务状态），
    而本模块用 ThreadPoolExecutor 并发查询图谱。ThreadPoolExecutor 的线程是复用的，
    所以每个线程建一个连接就够了，实际连接数上限 = 线程池大小（4）。
    其余属性透传给原实例，调用方无需感知。
    """

    def __init__(self, base):
        self._base = base
        self._local = threading.local()

    @property
    def graph(self):
        graph = getattr(self._local, "graph", None)
        if graph is None:
            try:
                from model_search import neo4j_db as _neo4j_db_cls
                graph = _neo4j_db_cls().graph
            except Exception as exc:  # noqa: BLE001
                # 建独立连接失败就退回共享实例：退化为原行为，不影响功能
                logger.warning("为查询线程创建独立 Neo4j 连接失败，回退共享连接: %s", exc)
                graph = self._base.graph
            self._local.graph = graph
        return graph

    def __getattr__(self, item):
        return getattr(self._base, item)



def _stable_hash(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class RuleLLMIntegration:
    """
    规则和大模型集成的推理引擎
    结合规则库和大语言模型的能力，基于知识图谱进行推理
    """
    
    def __init__(self, rule_file_path: str = 'rules/rule_base.json', 
                 model_name: str = "deepseek-r1:7b",
                 max_depth: int = 30):
        """
        初始化推理引擎
        
        Args:
            rule_file_path: 规则库文件路径
            model_name: 使用的大模型名称
            max_depth: 图谱搜索的最大深度
        """
        self.model_name = model_name
        self.max_depth = max_depth
        self._retrieval_cache = OrderedDict()
        self._answer_cache = OrderedDict()
        
        # 加载规则库
        self.rules = self._load_rules(rule_file_path)
        
        # 规则分类缓存，用于快速查找
        self._build_rule_indices()
        
        logger.info(f"规则与大模型推理引擎已初始化, 使用模型: {model_name}, 规则数量: {len(self.rules)}")
    
    def _load_rules(self, rule_file_path: str) -> List[Dict]:
        """加载规则库"""
        try:
            with open(rule_file_path, 'r', encoding='utf-8') as f:
                rules = json.load(f)
            logger.info(f"成功加载规则库，共 {len(rules)} 条规则")
            return rules
        except Exception as e:
            logger.warning(f"加载规则库失败: {str(e)}")
            return []
    
    def _build_rule_indices(self):
        """构建规则索引，方便后续查找"""
        # 按具体关系索引
        self.relation_rules = {}
        # 按复合规则索引
        self.composite_rules = []
        
        for rule in self.rules:
            # 检查是否是复合规则
            if 'composite' in rule.get('condition', {}) and rule['condition']['composite']:
                self.composite_rules.append(rule)
                continue
            
            # 按具体关系索引
            if 'relation' in rule.get('condition', {}):
                relation = rule['condition']['relation']
                if relation not in self.relation_rules:
                    self.relation_rules[relation] = []
                self.relation_rules[relation].append(rule)
        
        logger.info(f"规则索引构建完成:  "
              f"{len(self.relation_rules)} 种具体关系, {len(self.composite_rules)} 条复合规则")
    
    def _find_applicable_rules(self,
                              relation: Optional[str] = None) -> List[Dict]:
        """
        查找适用于给定关系的规则
        
        Args:
            relation: 具体关系
            
        Returns:
            适用规则列表
        """
        applicable_rules = []
        
        # 按具体关系查找
        if relation and relation in self.relation_rules:
            applicable_rules.extend(self.relation_rules[relation])
        
        # 按优先级排序
        if applicable_rules:
            applicable_rules.sort(key=lambda x: x.get('priority', 0), reverse=True)
        
        return applicable_rules
    
    def get_entity_relationships(self, entity_id: int, neo4j_db) -> List[Dict]:
        """
        获取实体的关系
        
        Args:
            entity_id: 实体ID
            neo4j_db: Neo4j数据库实例
            
        Returns:
            关系列表
        """
        try:
            # 获取出向关系
            query_outgoing = """
            MATCH (n)-[r]->(m)
            WHERE ID(n) = $entity_id
            RETURN n, r, m, 'outgoing' as direction
            """
            
            # 获取入向关系
            query_incoming = """
            MATCH (n)<-[r]-(m)
            WHERE ID(n) = $entity_id
            RETURN n, r, m, 'incoming' as direction
            """
            
            # 合并结果
            results_outgoing = neo4j_db.graph.run(query_outgoing, entity_id=int(entity_id)).data()
            results_incoming = neo4j_db.graph.run(query_incoming, entity_id=int(entity_id)).data()
            
            relationships = []
            processed_relations = set()  # 用于去重
            
            # 处理所有结果
            for result in results_outgoing + results_incoming:
                source_node = result['n'] if result['direction'] == 'outgoing' else result['m']
                target_node = result['m'] if result['direction'] == 'outgoing' else result['n']
                relation = result['r']
                
                # 创建关系的唯一标识，避免重复
                relation_id = f"{source_node.identity}_{target_node.identity}_{type(relation).__name__}"
                if relation_id in processed_relations:
                    continue
                
                processed_relations.add(relation_id)
                
                rel_type = type(relation).__name__
                
                rel_info = {
                    'source': {
                        'id': source_node.identity,
                        'name': source_node.get('name', ''),
                        'type': list(source_node.labels)[0] if source_node.labels else '',
                        'properties': {k: v for k, v in source_node.items()}
                    },
                    'target': {
                        'id': target_node.identity,
                        'name': target_node.get('name', ''),
                        'type': list(target_node.labels)[0] if target_node.labels else '',
                        'properties': {k: v for k, v in target_node.items()}
                    },
                    'relation': rel_type,
                    'properties': {k: v for k, v in relation.items()},
                    'direction': result['direction']
                }
                relationships.append(rel_info)
            
            logger.info(f"获取到实体ID({entity_id})的 {len(relationships)} 个关系")
            return relationships
        except Exception as e:
            logger.info(f"获取实体关系时出错: {str(e)}")
            return []
    
    def search_paths_between_entities(self, entity1_id: int, entity2_id: int, 
                                     neo4j_db, max_depth: Optional[int] = None) -> List[Dict]:
        """
        搜索两个实体之间的路径
        
        Args:
            entity1_id: 第一个实体ID
            entity2_id: 第二个实体ID
            neo4j_db: Neo4j数据库实例
            max_depth: 最大搜索深度
            
        Returns:
            路径列表
        """
        if max_depth is None:
            max_depth = self.max_depth
            
        try:
            # 添加检查：如果起始和结束节点相同，则跳过路径搜索
            if entity1_id == entity2_id:
                logger.info(f"跳过相同节点的路径搜索: {entity1_id} -> {entity1_id}")
                return []
                
            # 变长路径的范围必须内联，先确保是正整数（避免任何非数字内容进入 Cypher）
            depth = max(1, min(int(max_depth), 64))

            # 查询从entity1到entity2的有向路径
            query_forward = f"""
            MATCH path = shortestPath((n)-[*1..{depth}]->(m))
            WHERE ID(n) = $start_id AND ID(m) = $end_id
            RETURN path
            """
            
            # 查询从entity2到entity1的有向路径
            query_backward = f"""
            MATCH path = shortestPath((n)-[*1..{depth}]->(m))
            WHERE ID(n) = $start_id AND ID(m) = $end_id
            RETURN path
            """
            
            # 合并结果
            results = []
            forward_results = neo4j_db.graph.run(
                query_forward, start_id=int(entity1_id), end_id=int(entity2_id)
            ).data()
            backward_results = neo4j_db.graph.run(
                query_backward, start_id=int(entity2_id), end_id=int(entity1_id)
            ).data()
            
            # 如果有正向路径，优先使用正向路径
            if forward_results:
                results = forward_results
            elif backward_results:
                results = backward_results
            
            paths = []
            for result in results:
                path = result.get('path')
                if not path:
                    continue
                    
                nodes = list(path.nodes)
                rels = list(path.relationships)
                
                path_data = []
                last_node_name = None
                last_node_id = None
                
                for i, node in enumerate(nodes):
                    node_data = {
                        'name': node['name'],
                        'id': node.identity,
                        'type': list(node.labels)[0] if node.labels else ''
                    }
                    
                    # 添加所有节点属性
                    for key, value in node.items():
                        if key != 'name' and key != 'id' and key != 'type':
                            node_data[key] = value
                    
                    # 添加关系信息
                    if i > 0 and i-1 < len(rels):
                        rel = rels[i-1]
                        rel_type = type(rel).__name__
                        
                        if rel.start_node.identity == last_node_id:
                            node_data['relation'] = f"{last_node_name} -{rel_type}-> {node['name']}"
                            node_data['relation_direction'] = 'outgoing'
                        else:
                            node_data['relation'] = f"{last_node_name} <-{rel_type}- {node['name']}"
                            node_data['relation_direction'] = 'incoming'
                        
                        node_data['relation_type'] = rel_type
                        node_data['relation_properties'] = {k: v for k, v in rel.items()}
                    
                    last_node_name = node['name']
                    last_node_id = node.identity
                    path_data.append(node_data)
                
                paths.append({
                    'path': path_data,
                    'length': len(rels),
                    'start_entity': path_data[0]['name'] if path_data else None,
                    'end_entity': path_data[-1]['name'] if path_data else None
                })
            
            logger.info(f"找到 {len(paths)} 条从 {entity1_id} 到 {entity2_id} 的路径")
            return paths
            
        except Exception as e:
            logger.info(f"搜索路径时出错: {str(e)}")
            return []
    
    def apply_inference_rules(self, relationships: List[Dict]) -> List[Dict]:
        """应用推理规则，生成新的关系"""
        inferred_relationships = []
        
        # 第一步：应用简单规则（现有逻辑）
        for rel in relationships:
            # 使用原始关系（非格式化）来匹配规则
            relation = rel.get('properties', {}).get('relation_type', '')
                
            # 跳过已经是推理关系的条目，避免重复推理
            if rel.get('properties', {}).get('inferred', False):
                continue
            
            # 走 _build_rule_indices 建好的按关系索引，避免每次全表扫描
            applicable_rules = self._find_applicable_rules(relation)
            
            if not applicable_rules:
                continue
            
            # 应用规则
            for rule in applicable_rules:
                # 获取规则的推理部分
                inference = rule.get('inference', {})
                inferred_relation = inference.get('relation', '')
                direction = inference.get('direction', 'forward')
                
                # 获取规则中定义的原始关系作为derived_from
                rule_relation = rule.get('condition', {}).get('relation', '')
                if not rule_relation:
                    rule_relation = relation
                
                # 创建新关系
                inferred_rel = {
                    'source': {
                        'id': rel['source']['id'],
                        'name': rel['source']['name'],
                        'type': rel['source']['type']
                    },
                    'target': {
                        'id': rel['target']['id'],
                        'name': rel['target']['name'],
                        'type': rel['target']['type']
                    },
                    'relation': inferred_relation,
                    'properties': {
                        'inferred': True,
                        'rule_id': rule['rule_id'],
                        'rule_name': rule.get('name', ''),
                        'derived_from': relation
                    }
                }

                # 处理其他类型关系或未指定类型关系
                if direction == "reverse":
                    inferred_rel['source'], inferred_rel['target'] = inferred_rel['target'], inferred_rel['source']
                
                # 在关系对象上添加inferred标记，便于前端识别
                inferred_rel['inferred'] = True
                
                # 记录处理日志
                logger.info(f"推理关系: {inferred_rel['source']['name']} --[{inferred_relation}]--> {inferred_rel['target']['name']}")
                logger.info(f"  基于原始关系: {rel['source']['name']} --[{relation}]--> {rel['target']['name']}")
                logger.info(f"  规则定义的关系: {rule_relation}")
                
                inferred_relationships.append(inferred_rel)
        
        # 第二步：应用复合规则
        self._apply_composite_rules(relationships, inferred_relationships)
        
        logger.info(f"根据规则推理出 {len(inferred_relationships)} 个新关系")
        return inferred_relationships
    
    def _apply_composite_rules(self, relationships: List[Dict], inferred_relationships: List[Dict]):
        """应用复合规则，处理需要多步关系的推理"""
        # 筛选出复合规则
        composite_rules = [rule for rule in self.rules if rule.get('condition', {}).get('composite', False)]
        
        if not composite_rules:
            logger.info("没有找到复合规则，跳过复合规则处理")
            return
        
        logger.info(f"开始处理 {len(composite_rules)} 个复合规则")
        
        # 构建关系索引，便于快速查找
        # 关系索引结构: {source_id: {target_id: [relation1, relation2, ...]}}
        relation_index = {}
        
        # 构建双向索引，适用于各种方向的关系查询
        for rel in relationships:
            source_id = rel['source']['id']
            target_id = rel['target']['id']
            relation = rel['relation']
            
            # 记录关系类型
            rel_info = {
                'relation': relation,
                'rel_obj': rel  # 存储完整的关系对象，便于后续处理
            }
            
            # 添加到索引，按源节点索引
            if source_id not in relation_index:
                relation_index[source_id] = {}
            if target_id not in relation_index[source_id]:
                relation_index[source_id][target_id] = []
            relation_index[source_id][target_id].append(rel_info)
        
        # 处理每个复合规则
        for rule in composite_rules:
            condition = rule.get('condition', {})
            path_length = condition.get('path_length', 2)  # 默认为2步路径
            relation_type = condition.get('relation', '')
            
            # 目前仅实现2步路径的规则
            if path_length != 2:
                logger.info(f"暂不支持长度为 {path_length} 的路径规则: {rule['name']}")
                continue
            
            logger.info(f"处理复合规则: {rule['name']}, 关系类型: {relation_type}, 路径长度: {path_length}")
            
            # 对于2步路径的规则，寻找形如 A--[rel]-->B--[rel]-->C 的路径
            # 即查找满足的A→B和B→C关系
            found_paths = []
            
            # 遍历所有可能的第一步关系
            for source_id, targets in relation_index.items():
                for middle_id, relations in targets.items():
                    # 检查A→B关系是否符合规则要求的关系类型
                    first_step_relations = [r for r in relations if r['relation'] == relation_type]
                    if not first_step_relations:
                        continue
                    
                    # 检查B是否有后续关系
                    if middle_id in relation_index:
                        # 遍历B的所有目标节点
                        for target_id, second_relations in relation_index[middle_id].items():
                            # 避免自环
                            if target_id == source_id:
                                continue
                            
                            # 检查B→C关系是否符合规则要求的关系类型
                            second_step_relations = [r for r in second_relations if r['relation'] == relation_type]
                            if not second_step_relations:
                                continue
                            
                            # 找到一条符合条件的2步路径
                            first_rel = first_step_relations[0]['rel_obj']
                            second_rel = second_step_relations[0]['rel_obj']
                            
                            path = {
                                'start': {
                                    'id': source_id,
                                    'name': first_rel['source']['name'],
                                    'type': first_rel['source']['type']
                                },
                                'middle': {
                                    'id': middle_id,
                                    'name': first_rel['target']['name'],
                                    'type': first_rel['target']['type']
                                },
                                'end': {
                                    'id': target_id,
                                    'name': second_rel['target']['name'],
                                    'type': second_rel['target']['type']
                                },
                                'first_relation': first_rel,
                                'second_relation': second_rel
                            }
                            
                            found_paths.append(path)
            
            # 应用推理规则，根据找到的路径创建新的关系
            inference = rule.get('inference', {})
            inferred_relation = inference.get('relation', '')
            direction = inference.get('direction', 'forward')
            
            for path in found_paths:
                # 创建新的推理关系
                new_rel = {
                    'source': path['start'],
                    'target': path['end'],
                    'relation': inferred_relation,
                    'properties': {
                        'inferred': True,
                        'rule_id': rule['rule_id'],
                        'rule_name': rule.get('name', ''),
                        'derived_from': f"{relation_type}链",
                        'composite': True,
                        'path_length': path_length
                    }
                }
                
                # 处理关系方向
                if direction == "reverse":
                    new_rel['source'], new_rel['target'] = new_rel['target'], new_rel['source']
                
                # 标记为推理关系
                new_rel['inferred'] = True
                
                logger.info(f"复合规则推理: {new_rel['source']['name']} --[{inferred_relation}]--> {new_rel['target']['name']}")
                logger.info(f"  基于路径: {path['start']['name']} --[{relation_type}]--> {path['middle']['name']} --[{relation_type}]--> {path['end']['name']}")
                logger.info(f"  规则: {rule.get('name', '')}")
                
                # 添加到推理关系集合
                inferred_relationships.append(new_rel)
            
            logger.info(f"复合规则 {rule['name']} 推理出 {len(found_paths)} 个新关系")

    def _build_inference_prompt(self,
                               question: str,
                               entities: List[str],
                               entity_info: List[Dict],
                               relationships: List[Dict],
                               paths: List[Dict]) -> str:
        """构建推理提示词（精简版，减少token消耗）"""

        # 预处理用户问题 - 移除多余空格并确保问题以问号结尾
        processed_question = question.strip()
        if processed_question and not processed_question.endswith(('?', '？')):
            processed_question += '？'

        # 格式化实体信息为文本（只保留关键属性）
        entity_info_text = ""
        for i, info in enumerate(entity_info[:10]):  # 限制最多10个实体
            if info:
                properties = info.get('properties', {})
                # 只保留关键属性
                key_props = []
                for k in ['dynasty', 'start_date', 'end_date', 'Place', 'Result', 'Role']:
                    if k in properties and properties[k]:
                        key_props.append(f"{k}: {properties[k]}")
                props_text = ", ".join(key_props)
                entity_info_text += f"{i+1}. {info.get('name', '')} ({info.get('type', '')})"
                if props_text:
                    entity_info_text += f" - {props_text}"
                entity_info_text += "\n"

        # 分离原始关系和推理关系
        original_relationships = []
        inferred_relationships = []

        for rel in relationships:
            if rel.get('properties', {}).get('inferred', False) or rel.get('inferred', False):
                inferred_relationships.append(rel)
            else:
                original_relationships.append(rel)

        # 格式化关系信息为文本（限制数量）
        relationships_text = ""
        if original_relationships:
            relationships_text += "【图谱关系】\n"
            for i, rel in enumerate(original_relationships[:15]):  # 最多15条
                source = rel.get('source', {}).get('name', '')
                target = rel.get('target', {}).get('name', '')
                relation = rel.get('relation', '')
                relationships_text += f"{source} →[{relation}]→ {target}\n"

        if inferred_relationships:
            relationships_text += "\n【推理关系】\n"
            for i, rel in enumerate(inferred_relationships[:10]):  # 最多10条
                source = rel.get('source', {}).get('name', '')
                target = rel.get('target', {}).get('name', '')
                relation = rel.get('relation', '')
                relationships_text += f"{source} →[{relation}]→ {target}\n"

        # 格式化路径信息为文本（限制数量）
        paths_text = ""
        for i, path_data in enumerate(paths[:3]):  # 最多3条路径
            path = path_data.get('path', [])
            if path:
                path_str = path[0].get('name', '')
                for j in range(1, len(path)):
                    node = path[j]
                    relation = node.get('relation_type', '')
                    path_str += f" →[{relation}]→ {node.get('name', '')}"
                paths_text += f"{i+1}. {path_str}\n"

        # 构建精简版提示词
        prompt = f"""基于知识图谱回答历史战争问题。

【问题】{processed_question}

【相关实体】{', '.join(entities[:8])}

【实体信息】
{entity_info_text if entity_info_text else '无'}

【实体关系】
{relationships_text if relationships_text else '无'}

【关系路径】
{paths_text if paths_text else '无'}

【要求】
1. 直接回答问题，不要说"根据信息"等引导语
2. 区分图谱事实和推理关系
3. 使用中文回答
4. 枚举问题需覆盖全部匹配事件
"""

        return prompt

    def _build_dynasty_event_list_prompt(self,
                                         question: str,
                                         dynasty_scope: Dict[str, Any],
                                         event_info: List[Dict]) -> str:
        """为朝代战争枚举问题构建结构化提示词，减少遗漏和跑题。"""
        processed_question = question.strip()
        if processed_question and not processed_question.endswith(('?', '？')):
            processed_question += '？'

        event_lines = []
        for index, info in enumerate(event_info, 1):
            props = info.get('properties', {})
            def field(label, value):
                value_text = "" if value in (None, "None") else str(value).strip()
                return f"{label}={value_text}" if value_text else ""

            fields = [
                field("事件名", info.get('name', '')),
                field("朝代", props.get('dynasty', '')),
                field("时间", props.get('start_date', '')),
                field("地点", props.get('Place', '')),
                field("发起方", props.get('aggressor', '')),
                field("防御方", props.get('defender', '')),
                field("结果", props.get('Result', '')),
                field("影响", props.get('Impact', '')),
            ]
            event_lines.append(f"{index}. " + "；".join([item for item in fields if item]))

        return f"""
## 用户原始问题
{processed_question}

## 问题类型
朝代战争事件枚举

## 检索范围
用户询问的是"{dynasty_scope.get('display', '')}"相关战争。
后端已将图谱范围限定为朝代属于：{', '.join(dynasty_scope.get('dynasties', []))}

## 图谱事件清单
共 {len(event_info)} 条事件，必须全部覆盖：
{chr(10).join(event_lines) if event_lines else '无'}

## 回答要求
1. 必须逐条列出上方"图谱事件清单"中的全部 {len(event_info)} 条事件，不要只选代表性事件。
2. 不要添加清单之外的事件，不要把其他朝代事件混入答案。
3. 每条事件优先包含：事件名、时间、结果；如果字段为空或"不详"，可以省略该字段。
4. 可以按朝代小标题分组，但不能把多个事件合并成一句。
5. 不要给出"最准确答案是某一事件"这类单选结论。
6. 最后可以用一句话说明"以上为图谱中检索到的完整清单"。
"""

    def _format_event_record_lines(self, event_info: List[Dict]) -> str:
        """把事件节点整理成结构化清单，供专用问答提示词使用。"""
        event_lines = []
        for index, info in enumerate(event_info, 1):
            props = info.get('properties', {})

            def field(label, value):
                value_text = "" if value in (None, "None") else str(value).strip()
                return f"{label}={value_text}" if value_text else ""

            fields = [
                field("事件名", info.get('name', '')),
                field("朝代", props.get('dynasty', '')),
                field("时间", props.get('start_date', '')),
                field("地点", props.get('Place', '')),
                field("发起方", props.get('aggressor', '')),
                field("防御方", props.get('defender', '')),
                field("相关人物", props.get('person', '')),
                field("参与对象", props.get('matched_participant', '')),
                field("参与关系", props.get('participant_relation', '')),
                field("结果", props.get('Result', '')),
                field("影响", props.get('Impact', '')),
                field("备注", props.get('Remark', '')),
            ]
            event_lines.append(f"{index}. " + "；".join([item for item in fields if item]))
        return "\n".join(event_lines)

    def _build_event_detail_prompt(self, question: str, event_info: List[Dict]) -> str:
        """为事件详情问题构建结构化提示词。"""
        processed_question = question.strip()
        if processed_question and not processed_question.endswith(('?', '？')):
            processed_question += '？'

        return f"""
## 用户原始问题
{processed_question}

## 问题类型
战争事件详情

## 图谱事件记录
{self._format_event_record_lines(event_info) if event_info else '无'}

## 回答要求
1. 只基于上方图谱事件记录回答，不要添加图谱外史实。
2. 根据用户问的字段重点回答；如果用户没有限定字段，按"时间、地点、参战方/人物、结果、影响"组织。
3. 如果某字段为空或"不详"，明确说明图谱未提供该字段。
4. 如果匹配到多个事件，先说明匹配到多个，再分别回答。
"""

    def _build_participant_event_prompt(self, question: str, event_info: List[Dict]) -> str:
        """为人物/组织参与事件问题构建结构化提示词。"""
        processed_question = question.strip()
        if processed_question and not processed_question.endswith(('?', '？')):
            processed_question += '？'

        return f"""
## 用户原始问题
{processed_question}

## 问题类型
人物或组织参与战争枚举

## 图谱事件清单
共 {len(event_info)} 条事件：
{self._format_event_record_lines(event_info) if event_info else '无'}

## 回答要求
1. 必须覆盖上方清单中的全部事件，不要只选代表性事件。
2. 每条事件说明事件名、参与关系、时间和结果；字段为空或"不详"时可以省略。
3. 不要添加清单之外的事件。
4. 最后说明这些记录来自当前图谱的直接关系。
"""
    
    def generate_response_with_llm(self, 
                                  question: str,
                                  entities: List[str],
                                  entity_info: List[Dict],
                                  relationships: List[Dict],
                                  paths: List[Dict],
                                  question_type: str = "general",
                                  metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        使用大模型生成回答
        
        Args:
            question: 用户问题
            entities: 实体列表
            entity_info: 实体信息列表
            relationships: 关系列表
            paths: 路径列表
            
        Returns:
            生成的回答
        """
        metadata = metadata or {}
        if question_type == "dynasty_event_list":
            user_prompt = self._build_dynasty_event_list_prompt(
                question=question,
                dynasty_scope=metadata.get("dynasty_scope", {}),
                event_info=entity_info
            )
        elif question_type == "event_detail":
            user_prompt = self._build_event_detail_prompt(question, entity_info)
        elif question_type == "participant_event_list":
            user_prompt = self._build_participant_event_prompt(question, entity_info)
        else:
            user_prompt = self._build_inference_prompt(
                question=question,
                entities=entities,
                entity_info=entity_info,
                relationships=relationships,
                paths=paths
            )

        answer_cache_key = (
            "answer",
            self.model_name,
            question_type,
            _stable_hash({
                "question": question,
                "entities": entities,
                "entity_info": entity_info,
                "relationships": relationships,
                "paths": paths,
                "metadata": metadata,
                "prompt": user_prompt,
            })
        )
        cached_answer = _lru_get(self._answer_cache, answer_cache_key)
        if cached_answer is not None:
            logger.info(f"命中问答生成缓存: {question_type} / {question}")
            return cached_answer
        
        try:
            # 记录模型调用开始时间
            start_time = time.time()
            logger.info(f"调用 {self.model_name} 模型生成回答...")
            
            # 增强的system提示词，强调对用户问题的准确理解
            if question_type in {"dynasty_event_list", "participant_event_list"}:
                system_prompt = """你是一位中国历史战争知识图谱问答助手。当前任务是根据后端提供的结构化事件清单回答枚举类问题。
必须完整覆盖清单中的每一条事件；不得自行删减、合并、补充清单外事件；不得把枚举题回答成单个"最准确答案"。"""
            elif question_type == "event_detail":
                system_prompt = """你是一位中国历史战争知识图谱问答助手。当前任务是根据后端提供的结构化事件记录回答事件详情问题。
只能使用记录中的字段作答；字段缺失时说明图谱未提供；不得补充图谱外史实。"""
            else:
                system_prompt = """你是一位专业的中国历史战争研究与事件关系分析专家，精通中国历代战争事件、参战势力、战役过程及其历史背景。
你的职责是：
1. 只回答用户提出的原始问题，不要重复或引用我给你的指令内容
2. 不要在回答中说"根据提供的信息"或"基于图谱数据"等引导语
3. 所有回答必须基于知识图谱提供的事实，不要编造不存在的关系
4. 答案应直接、简洁，不要添加不必要的解释

重要: 不要将指令或提示词本身视为用户问题的一部分。用户的原始问题已在提示词开头明确标出。
"""
            
            # 调用大模型生成回答
            response = ollama.chat(
                model=self.model_name,
                messages=[
                    {
                        "role": "system", 
                        "content": system_prompt
                    },
                    {
                        "role": "user", 
                        "content": user_prompt
                    }
                ],
                stream=False,
                keep_alive="10m",
                options={"temperature": 0.1}  # 添加温度参数
            )
            
            # 记录模型调用耗时
            process_time = time.time() - start_time
            answer = response['message']['content']
            _lru_set(self._answer_cache, answer_cache_key, answer)
            logger.info(f"模型响应成功，耗时: {process_time:.2f}秒")
            return answer
            
        except Exception as e:
            logger.warning(f"模型调用失败: {str(e)}")
            return f"抱歉，在处理您的问题时遇到了技术问题。错误信息: {str(e)}"
    
    def _filter_relevant_entities(self, entities: List[str], question: str, neo4j_db) -> List[str]:
        """
        过滤与用户问题最相关的实体
        
        策略：
        1. 首先检查实体是否在知识图谱中存在
        2. 优先选择名称较长的实体（通常更具体）
        3. 限制实体数量，避免过多无关查询
        4. 检查实体与问题关键词的匹配度
        """
        if not entities:
            return []
        
        logger.info(f"过滤前实体列表: {entities}")
        
        # 去除问题中的常见停用词和标点
        import re
        # 提取问题中的关键词（长度>=2的中文字符序列）
        question_keywords = set(re.findall(r'[\u4e00-\u9fa5]{2,}', question))
        
        scored_entities = []
        existing_entities = set()
        try:
            query = """
            MATCH (n)
            WHERE any(entity IN $entities WHERE n.name = entity OR n.name CONTAINS entity)
            RETURN DISTINCT n.name as name
            LIMIT 50
            """
            rows = neo4j_db.graph.run(query, entities=entities).data()
            existing_names = [row.get('name', '') for row in rows]
            for entity in entities:
                if any(name == entity or entity in name for name in existing_names):
                    existing_entities.add(entity)
        except Exception as e:
            logger.info(f"批量查询实体时出错: {e}")

        for entity in entities:
            score = 0
            
            if entity in existing_entities:
                score += 10
                score += len(entity) * 0.5

                for keyword in question_keywords:
                    if entity in keyword or keyword in entity:
                        score += 5
                        break

                if entity in question:
                    score += 10
            
            scored_entities.append((entity, score))
        
        # 按分数降序排序
        scored_entities.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"实体评分结果: {scored_entities}")
        
        # 只保留分数最高的前4个实体（避免过多无关实体）
        max_entities = 4
        filtered_entities = [e[0] for e in scored_entities[:max_entities] if e[1] > 0]
        
        # 确保至少有1个实体被返回（如果有的话）
        if not filtered_entities and scored_entities:
            filtered_entities = [scored_entities[0][0]]
        
        logger.info(f"过滤后实体列表: {filtered_entities}")
        return filtered_entities

    def _extract_query_entities(self, question: str, extracted_entities: List[str]) -> List[str]:
        """只返回用户原句中明确出现的实体，用于前端展示。"""
        query_entities = []

        for dynasty in sorted(DYNASTY_SCOPE_MAP.keys(), key=len, reverse=True):
            if dynasty in question and dynasty not in query_entities:
                query_entities.append(dynasty)
                break

        for entity in extracted_entities:
            if entity in question and entity not in query_entities:
                query_entities.append(entity)

        if not query_entities:
            for entity in extracted_entities:
                if entity not in query_entities:
                    query_entities.append(entity)
                if len(query_entities) >= 3:
                    break

        return query_entities

    def _detect_dynasty_event_scope(self, question: str) -> Optional[Dict[str, Any]]:
        """识别"某朝有哪些战争/战役"这类朝代范围查询。"""
        if not any(word in question for word in ["战争", "战役", "战事", "之战", "起义", "叛乱"]):
            return None
        if not any(word in question for word in ["哪些", "那些", "有哪些", "有那些", "有什么", "列出", "包括", "多少", "几场"]):
            return None

        for dynasty in sorted(DYNASTY_SCOPE_MAP.keys(), key=len, reverse=True):
            if dynasty in question:
                return {
                    "display": dynasty,
                    "dynasties": DYNASTY_SCOPE_MAP[dynasty],
                }

        return None

    def _looks_like_event_detail_query(self, question: str, entities: List[str]) -> bool:
        """识别某个战争事件的时间、地点、结果、影响等详情问题。"""
        if not any(entity in question and any(suffix in entity for suffix in ["之战", "战役", "起义", "叛乱"]) for entity in entities):
            return False
        return any(word in question for word in [
            "时间", "何时", "什么时候", "地点", "哪里", "结果", "影响", "原因", "经过",
            "详情", "介绍", "发生", "发起方", "防御方", "参与", "参战"
        ])

    def _looks_like_participant_event_query(self, question: str) -> bool:
        """识别人/组织参与了哪些战争。"""
        return any(word in question for word in ["参加", "参与", "参战", "发动", "发起", "指挥", "统帅", "有哪些战争", "有那些战争"]) and any(
            word in question for word in ["战争", "战役", "之战", "事件"]
        )

    def _get_dynasty_event_infos(self, neo4j_db, dynasty_values: List[str], limit: int = 40) -> List[Dict]:
        """按朝代属性直接取事件节点，作为模型回答上下文。"""
        cache_key = ("dynasty_events", tuple(dynasty_values), limit)
        cached = _lru_get(self._retrieval_cache, cache_key)
        if cached is not None:
            logger.info(f"命中朝代事件检索缓存: {dynasty_values}")
            return cached

        query = """
        MATCH (n:Event)
        WHERE n.dynasty IN $dynasties
        RETURN n
        ORDER BY coalesce(n.start_date, ''), n.name
        LIMIT $limit
        """
        try:
            rows = neo4j_db.graph.run(query, dynasties=dynasty_values, limit=limit).data()
        except Exception as e:
            logger.warning(f"按朝代查询事件失败: {e}")
            return []

        event_infos = []
        seen_ids = set()
        for row in rows:
            node = row.get("n")
            if not node or node.identity in seen_ids:
                continue
            seen_ids.add(node.identity)
            event_infos.append({
                "id": node.identity,
                "name": node.get("name", ""),
                "type": list(node.labels)[0] if node.labels else "Event",
                "properties": {k: v for k, v in node.items()}
            })

        _lru_set(self._retrieval_cache, cache_key, event_infos)
        return event_infos

    def _get_event_detail_infos(self, neo4j_db, event_names: List[str], limit: int = 8) -> List[Dict]:
        """按事件名取事件详情。"""
        if not event_names:
            return []

        cache_key = ("event_detail", tuple(event_names), limit)
        cached = _lru_get(self._retrieval_cache, cache_key)
        if cached is not None:
            logger.info(f"命中事件详情检索缓存: {event_names}")
            return cached

        query = """
        MATCH (n:Event)
        WHERE any(name IN $names WHERE n.name = name OR n.name CONTAINS name OR name CONTAINS n.name)
        RETURN n
        LIMIT $limit
        """
        try:
            rows = neo4j_db.graph.run(query, names=event_names, limit=limit).data()
        except Exception as e:
            logger.warning(f"查询事件详情失败: {e}")
            return []

        event_infos = []
        seen_ids = set()
        for row in rows:
            node = row.get("n")
            if not node or node.identity in seen_ids:
                continue
            seen_ids.add(node.identity)
            event_infos.append({
                "id": node.identity,
                "name": node.get("name", ""),
                "type": list(node.labels)[0] if node.labels else "Event",
                "properties": {k: v for k, v in node.items()}
            })

        _lru_set(self._retrieval_cache, cache_key, event_infos)
        return event_infos

    def _get_participant_event_infos(self, neo4j_db, participant_names: List[str], limit: int = 40) -> List[Dict]:
        """按人物或组织名称取其直接参与的事件。"""
        if not participant_names:
            return []

        cache_key = ("participant_events", tuple(participant_names), limit)
        cached = _lru_get(self._retrieval_cache, cache_key)
        if cached is not None:
            logger.info(f"命中参与事件检索缓存: {participant_names}")
            return cached

        query = """
        MATCH (p)-[r]-(e:Event)
        WHERE any(name IN $names WHERE p.name = name OR p.name CONTAINS name OR name CONTAINS p.name)
        RETURN DISTINCT e, p.name as participant, type(r) as relation
        ORDER BY coalesce(e.start_date, ''), e.name
        LIMIT $limit
        """
        try:
            rows = neo4j_db.graph.run(query, names=participant_names, limit=limit).data()
        except Exception as e:
            logger.warning(f"查询参与事件失败: {e}")
            return []

        event_infos = []
        seen_ids = set()
        for row in rows:
            node = row.get("e")
            if not node or node.identity in seen_ids:
                continue
            seen_ids.add(node.identity)
            props = {k: v for k, v in node.items()}
            props["matched_participant"] = row.get("participant", "")
            props["participant_relation"] = row.get("relation", "")
            event_infos.append({
                "id": node.identity,
                "name": node.get("name", ""),
                "type": list(node.labels)[0] if node.labels else "Event",
                "properties": props
            })

        _lru_set(self._retrieval_cache, cache_key, event_infos)
        return event_infos

    def process_question(self, question: str, entity_extractor, neo4j_db) -> Dict:
        """
        处理用户问题，返回推理结果
        
        Args:
            question: 用户问题
            entity_extractor: 实体提取器实例
            neo4j_db: Neo4j数据库实例
            
        Returns:
            包含回答和可视化数据的字典
        """
        start_time = time.time()
        
        try:
            # 预处理用户问题
            if not question or not question.strip():
                return {
                    'answer': "请提供有效的问题。",
                    'entities': [],
                    'kg_data': {'nodes': [], 'lines': []},
                    'process_time': time.time() - start_time
                }
            
            # 规范化问题格式
            processed_question = question.strip()
            # 检查问题是否过短或可能无效
            if len(processed_question) < 3:
                logger.info(f"警告: 问题过短或可能无效: '{processed_question}'")
            
            logger.info(f"开始处理用户问题: '{processed_question}'")
            
            # 1. 从问题中提取实体
            dynasty_scope = self._detect_dynasty_event_scope(processed_question)
            if dynasty_scope:
                extracted_entities = []
                query_entities = [dynasty_scope["display"]]
                event_detail_query = False
                participant_event_query = False
            else:
                extracted_entities = entity_extractor.extract_entities(processed_question)
                query_entities = self._extract_query_entities(processed_question, extracted_entities)
                event_detail_query = self._looks_like_event_detail_query(processed_question, extracted_entities)
                participant_event_query = self._looks_like_participant_event_query(processed_question)
            
            # 1.5 过滤实体，只保留最相关的。朝代范围题已由规则明确实体，避免再走一次模型抽取。
            if dynasty_scope:
                entities = query_entities.copy()
                logger.info(f"命中朝代事件范围查询，跳过实体抽取模型: {query_entities}")
            else:
                entities = self._filter_relevant_entities(extracted_entities, processed_question, neo4j_db)
            
            # 如果没有识别到实体，但问题中可能包含地名相关词汇，尝试进行模糊匹配
            if not entities:
                # 检查问题中是否包含可能的地名关键词
                all_keywords = ["之战", "战役", "战事", "战争", "起义", "叛乱", "征讨", "围城","帝", "王", "将军", "太守", "丞相", "元帅", "侯", "公","国", "朝", "军", "部", "盟", "部族", "王朝", "政权","城", "关", "州", "郡", "县", "府", "道", "路", "山", "河"]
                has_keyword = any(keyword in processed_question for keyword in all_keywords)
                
                if has_keyword:
                    logger.info(f"未识别到明确实体，但问题中包含战争图谱相关关键词，尝试进行模糊匹配")
                    # 提取问题中的所有可能实体词 (简单处理，实际应使用NLP工具)
                    potential_entities = []
                    for keyword in all_keywords:
                        if keyword in processed_question and len(keyword) > 1:  # 避免单字匹配
                            potential_entities.append(keyword)
                    
                    if potential_entities:
                        logger.info(f"从问题中提取潜在地名关键词: {potential_entities}")
                        entities = potential_entities
            
            if not entities:
                return {
                    'answer': '抱歉，我无法从您的问题中识别出任何明确的战争事件、人物、组织或地点实体。请尝试提供更具体的名称，例如"赤壁之战"、"曹操"、"魏国"、"襄阳城"等。',
                    'entities': [],
                    'kg_data': {'nodes': [], 'lines': []},
                    'process_time': time.time() - start_time
                }
            
            logger.info(f"从问题中识别到的实体: {entities}")
            logger.info(f"用户原句明确实体: {query_entities}")
            
            # 记录查询涉及的所有节点和关系
            all_entity_info = []
            all_relationships = []
            all_paths = []
            
            # 2. 从知识图谱中查询实体信息
            entity_info_map = {}  # 实体ID到实体信息的映射
            if dynasty_scope:
                all_entity_info = self._get_dynasty_event_infos(neo4j_db, dynasty_scope["dynasties"])
                for info in all_entity_info:
                    entity_info_map[info['id']] = info
                query_entities = query_entities or [dynasty_scope["display"]]
                entities = query_entities
            elif event_detail_query:
                event_names = [entity for entity in entities if any(suffix in entity for suffix in ["之战", "战役", "起义", "叛乱"])]
                all_entity_info = self._get_event_detail_infos(neo4j_db, event_names or entities)
                for info in all_entity_info:
                    entity_info_map[info['id']] = info
                query_entities = query_entities or event_names or entities
                entities = query_entities
            elif participant_event_query:
                participant_names = query_entities or entities
                all_entity_info = self._get_participant_event_infos(neo4j_db, participant_names)
                for info in all_entity_info:
                    entity_info_map[info['id']] = info
                entities = query_entities or entities

            # 并行查询实体信息
            query_entities_list = [] if (dynasty_scope or event_detail_query or participant_event_query) else entities
            if query_entities_list:
                logger.info(f"开始并行查询 {len(query_entities_list)} 个实体信息...")
                query_start = time.time()

                # 线程池里并发查询：每线程用各自的连接
                worker_db = _ThreadLocalNeo4j(neo4j_db)

                def query_single_entity(entity, db=worker_db):
                    """查询单个实体信息"""
                    try:
                        # 先进行精确查询
                        query = """
                        MATCH (n)
                        WHERE n.name = $entity
                        RETURN n
                        LIMIT 1
                        """
                        results = db.graph.run(query, entity=entity).data()

                        if results:
                            node = results[0]['n']
                            return [{
                                'id': node.identity,
                                'name': node['name'],
                                'type': list(node.labels)[0] if node.labels else '',
                                'properties': {k: v for k, v in node.items()}
                            }]

                        # 尝试模糊查询
                        fuzzy_query = """
                        MATCH (n)
                        WHERE n.name CONTAINS $entity OR $entity CONTAINS n.name
                        RETURN n
                        LIMIT 5
                        """
                        fuzzy_results = db.graph.run(fuzzy_query, entity=entity).data()

                        if fuzzy_results:
                            return [{
                                'id': result['n'].identity,
                                'name': result['n']['name'],
                                'type': list(result['n'].labels)[0] if result['n'].labels else '',
                                'properties': {k: v for k, v in result['n'].items()}
                            } for result in fuzzy_results]

                        return []
                    except Exception as e:
                        logger.warning(f"查询实体 '{entity}' 失败: {e}")
                        return []

                # 使用线程池并行查询
                with ThreadPoolExecutor(max_workers=min(4, len(query_entities_list))) as executor:
                    future_to_entity = {
                        executor.submit(query_single_entity, entity): entity
                        for entity in query_entities_list
                    }

                    for future in as_completed(future_to_entity):
                        entity = future_to_entity[future]
                        try:
                            entity_infos = future.result()
                            for info in entity_infos:
                                all_entity_info.append(info)
                                entity_info_map[info['id']] = info
                        except Exception as e:
                            logger.warning(f"处理实体 '{entity}' 结果失败: {e}")

                query_time = time.time() - query_start
                logger.info(f"实体信息查询完成，耗时: {query_time:.2f}秒，找到 {len(all_entity_info)} 个实体")
            
            # 如果没有找到任何实体信息，给出更友好的回复
            if not all_entity_info:
                return {
                    'answer': f"抱歉，我无法在知识库中找到与'{', '.join(entities)}'相关的实体信息。请尝试其他名称或检查拼写是否正确。",
                    'entities': entities,
                    'kg_data': {'nodes': [], 'lines': []},
                    'process_time': time.time() - start_time
                }
            
            # 3. 并行获取实体关系
            logger.info(f"开始并行查询 {len(all_entity_info)} 个实体的关系...")
            relation_start = time.time()

            def query_entity_relations(info, db=worker_db):
                """查询单个实体的关系"""
                try:
                    return self.get_entity_relationships(info['id'], db)
                except Exception as e:
                    logger.warning(f"查询实体 '{info['name']}' 关系失败: {e}")
                    return []

            with ThreadPoolExecutor(max_workers=min(4, len(all_entity_info))) as executor:
                future_to_info = {
                    executor.submit(query_entity_relations, info): info
                    for info in all_entity_info
                }

                for future in as_completed(future_to_info):
                    info = future_to_info[future]
                    try:
                        relationships = future.result()
                        all_relationships.extend(relationships)
                    except Exception as e:
                        logger.warning(f"处理实体 '{info['name']}' 关系结果失败: {e}")

            relation_time = time.time() - relation_start
            logger.info(f"实体关系查询完成，耗时: {relation_time:.2f}秒，找到 {len(all_relationships)} 条关系")
            
            # 4. 并行搜索实体之间的路径
            if len(all_entity_info) >= 2 and not (dynasty_scope or event_detail_query or participant_event_query):
                logger.info(f"开始并行搜索实体间路径...")
                path_start = time.time()

                # 构建实体对列表
                entity_pairs = []
                processed_entity_pairs = set()

                for i in range(len(all_entity_info)):
                    for j in range(i+1, len(all_entity_info)):
                        entity1_id = all_entity_info[i]['id']
                        entity2_id = all_entity_info[j]['id']

                        # 跳过相同的实体ID
                        if entity1_id == entity2_id:
                            continue

                        # 创建一个排序后的实体ID对作为键，确保不重复查询
                        entity_pair = tuple(sorted([entity1_id, entity2_id]))
                        if entity_pair in processed_entity_pairs:
                            continue

                        processed_entity_pairs.add(entity_pair)
                        entity_pairs.append((entity1_id, entity2_id))

                def search_path_for_pair(pair, db=worker_db):
                    """搜索一对实体之间的路径"""
                    entity1_id, entity2_id = pair
                    try:
                        return self.search_paths_between_entities(entity1_id, entity2_id, db)
                    except Exception as e:
                        logger.warning(f"搜索路径 {entity1_id} -> {entity2_id} 失败: {e}")
                        return []

                # 使用线程池并行搜索路径
                if entity_pairs:
                    with ThreadPoolExecutor(max_workers=min(4, len(entity_pairs))) as executor:
                        future_to_pair = {
                            executor.submit(search_path_for_pair, pair): pair
                            for pair in entity_pairs
                        }

                        for future in as_completed(future_to_pair):
                            pair = future_to_pair[future]
                            try:
                                paths = future.result()
                                all_paths.extend(paths)
                            except Exception as e:
                                logger.warning(f"处理路径 {pair} 结果失败: {e}")

                path_time = time.time() - path_start
                logger.info(f"路径搜索完成，耗时: {path_time:.2f}秒，找到 {len(all_paths)} 条路径")
            
            # 5. 应用推理规则
            inferred_relationships = self.apply_inference_rules(all_relationships)
            
            # 原始关系与推理关系分开处理
            original_relationships = all_relationships.copy()
            all_relationships.extend(inferred_relationships)
            
            # 6. 使用大模型生成回答
            answer_entities = entities
            question_type = "general"
            answer_metadata = {}
            if dynasty_scope:
                question_type = "dynasty_event_list"
                answer_metadata["dynasty_scope"] = dynasty_scope
                answer_entities = [
                    f"{dynasty_scope['display']}（仅限朝代属于 {', '.join(dynasty_scope['dynasties'])} 的战争事件）"
                ]
            elif event_detail_query:
                question_type = "event_detail"
            elif participant_event_query:
                question_type = "participant_event_list"

            answer = self.generate_response_with_llm(
                question=processed_question,  # 使用处理后的问题
                entities=answer_entities,
                entity_info=all_entity_info,
                relationships=all_relationships,
                paths=all_paths,
                question_type=question_type,
                metadata=answer_metadata
            )
            
            # 7. 构建知识图谱可视化数据（只使用原始关系，不含推理关系）
            kg_data = self._convert_to_visual_data(all_entity_info, original_relationships, all_paths)
            
            # 8. 格式化关系数据为三元组格式（包含原始和推理关系，但推理关系明确标注）
            context = self._format_relations_for_context(original_relationships, inferred_relationships)
            
            process_time = time.time() - start_time
            logger.info(f"问题处理完成，总耗时: {process_time:.2f}秒")
            
            return {
                'answer': answer,
                'entities': entities,
                'query_entities': query_entities or entities,
                'kg_data': kg_data,
                'context': context,  # 添加格式化后的关系上下文
                'process_time': process_time
            }
            
        except Exception as e:
            logger.info(f"处理问题时出错: {str(e)}")
            import traceback
            traceback.print_exc()
            
            return {
                'answer': f"抱歉，处理您的问题时遇到了技术问题: {str(e)}",
                'entities': entities if 'entities' in locals() else [],
                'kg_data': {'nodes': [], 'lines': []},
                'error': str(e),
                'process_time': time.time() - start_time
            }
    
    def _convert_to_visual_data(self, entities, relationships, paths):
        """将实体、关系和路径转换为可视化数据格式"""
        nodes = []
        lines = []
        node_ids = set()
        line_pairs = set()

        def _clean_node_properties(properties):
            cleaned = {}
            for key, value in (properties or {}).items():
                if key in {'id', 'name', 'type'}:
                    continue
                if value in (None, ''):
                    continue
                if isinstance(value, (str, int, float, bool)):
                    cleaned[key] = value
            return cleaned

        def _upsert_node(node_id, name, node_type, properties=None):
            cleaned_properties = _clean_node_properties(properties)

            if node_id not in node_ids:
                node_data = {
                    'id': node_id,
                    'name': name,
                    'type': node_type
                }
                node_data.update(cleaned_properties)
                nodes.append(node_data)
                node_ids.add(node_id)
                return

            for node in nodes:
                if node.get('id') != node_id:
                    continue
                if name and not node.get('name'):
                    node['name'] = name
                if node_type and not node.get('type'):
                    node['type'] = node_type
                for key, value in cleaned_properties.items():
                    if not node.get(key):
                        node[key] = value
                return

        for entity in entities:
            _upsert_node(
                entity['id'],
                entity.get('name', ''),
                entity.get('type', ''),
                entity.get('properties', {})
            )

        for path_data in paths:
            path = path_data.get('path', [])
            last_node = None

            for i, node in enumerate(path):
                node_id = node.get('id')
                _upsert_node(
                    node_id,
                    node.get('name', '未命名'),
                    node.get('type', ''),
                    {
                        k: v for k, v in node.items()
                        if k not in {'id', 'name', 'type', 'relation', 'relation_direction', 'relation_type', 'relation_properties'}
                    }
                )

                if i > 0 and last_node:
                    relation_type = node.get('relation_type', '')
                    from_id = last_node.get('id')
                    to_id = node_id
                    relation_key = (min(from_id, to_id), max(from_id, to_id), relation_type)

                    if relation_key not in line_pairs:
                        line_data = {
                            'from': from_id,
                            'to': to_id,
                            'text': relation_type,
                            'relation_direction': node.get('relation_direction', 'outgoing')
                        }
                        lines.append(line_data)
                        line_pairs.add(relation_key)

                last_node = node

        relation_ids = set()

        for rel in relationships:
            from_id = rel['source']['id']
            to_id = rel['target']['id']
            relation_type = rel['relation']

            _upsert_node(
                from_id,
                rel['source']['name'],
                rel['source']['type'],
                rel['source'].get('properties', {})
            )
            _upsert_node(
                to_id,
                rel['target']['name'],
                rel['target']['type'],
                rel['target'].get('properties', {})
            )

            relation_id = f"{from_id}_{to_id}_{relation_type}"

            if relation_id not in relation_ids:
                relation_ids.add(relation_id)

                line_data = {
                    'from': from_id,
                    'to': to_id,
                    'text': relation_type
                }

                if 'properties' in rel:
                    for k, v in rel['properties'].items():
                        line_data[k] = v

                if 'direction' in rel:
                    line_data['direction'] = rel['direction']

                if rel.get('properties', {}).get('inferred', False):
                    line_data['inferred'] = True
                elif rel.get('inferred', False):
                    line_data['inferred'] = True

                lines.append(line_data)

        unique_lines = []
        processed_pairs = set()

        for line in lines:
            from_id = line['from']
            to_id = line['to']
            relation_type = line['text']
            entity_pair = tuple(sorted([from_id, to_id]))
            relation_key = (*entity_pair, relation_type)

            if relation_key in processed_pairs and not line.get('inferred', False):
                continue

            processed_pairs.add(relation_key)
            unique_lines.append(line)

        return {
            'nodes': nodes,
            'lines': unique_lines
        }

    def _format_relations_for_context(self, relations: List[Dict[str, Any]], inferred_relations: List[Dict[str, Any]]) -> str:
        """将关系信息格式化为易读的上下文信息"""
        text_parts = []
        
        # 添加简洁的标题
        text_parts.append("📚 知识图谱参考信息")
        text_parts.append("")
        
        # 收集所有实体信息
        all_entities = set()
        entity_info_map = {}
        
        def collect_entity_info(rel_list):
            for rel in rel_list:
                source = rel.get("source", {})
                target = rel.get("target", {})
                
                for entity in [source, target]:
                    name = entity.get("name", "")
                    entity_type = entity.get("type", "")
                    if name and name not in entity_info_map:
                        entity_info_map[name] = entity_type
                        all_entities.add(name)
        
        collect_entity_info(relations)
        collect_entity_info(inferred_relations)
        
        # 输出实体列表
        if all_entities:
            text_parts.append("【涉及实体】")
            for name in sorted(all_entities):
                entity_type = entity_info_map.get(name, "")
                type_icon = {"Event": "⚔️", "Person": "👤", "Organization": "🏛️", "Place": "📍"}.get(entity_type, "•")
                text_parts.append(f"  {type_icon} {name} ({entity_type})")
            text_parts.append("")
        
        # 输出直接关系
        if relations:
            # 过滤掉可能混入的推理关系
            direct_relations = [r for r in relations if not r.get("properties", {}).get("inferred", False)]
            
            if direct_relations:
                text_parts.append("【直接关系】(来自知识图谱)")
                text_parts.append("")
                
                for i, relation in enumerate(direct_relations[:15], 1):  # 限制数量避免过长
                    source = relation.get("source", {}).get("name", "未知")
                    target = relation.get("target", {}).get("name", "未知")
                    relation_type = relation.get("relation", "相关")
                    
                    # 简化关系描述
                    text_parts.append(f"{i}. {source} →【{relation_type}】→ {target}")
                
                if len(direct_relations) > 15:
                    text_parts.append(f"  ... 还有 {len(direct_relations) - 15} 条关系")
                
                text_parts.append("")
        
        # 输出推理关系
        if inferred_relations:
            text_parts.append("【推理关系】(基于规则推导)")
            text_parts.append("")
            
            for i, relation in enumerate(inferred_relations[:10], 1):  # 限制数量
                source = relation.get("source", {}).get("name", "未知")
                target = relation.get("target", {}).get("name", "未知")
                relation_type = relation.get("relation", "相关")
                derived_from = relation.get("properties", {}).get("derived_from", "")
                
                # 简化显示
                text_parts.append(f"{i}. {source} →【{relation_type}】→ {target}")
                if derived_from:
                    text_parts.append(f"   (基于: {derived_from})")
            
            if len(inferred_relations) > 10:
                text_parts.append(f"  ... 还有 {len(inferred_relations) - 10} 条推理关系")
            
            text_parts.append("")
        
        if not relations and not inferred_relations:
            return "未找到相关实体关系信息"
        
        return "\n".join(text_parts) 
