"""
实体提取器模块

功能: 使用大模型从文本中提取实体
  - extract_entities(text): 提取战争事件、人物、组织、地点
  - _build_prompt(text): 构建提取提示词
  - _parse_entities_from_response(): 解析模型响应

模型: deepseek-r1:7b (通过ollama本地服务)
"""
import os
import json
import re
import ollama
import time
from collections import OrderedDict

from common_utils import lru_get as _cache_get
from dynasty_data import EXTRACTOR_DYNASTIES as DYNASTIES
from common_utils import lru_set as _cache_set

from logging_util import get_logger

logger = get_logger(__name__)


class Extractor:
    """
    基于大模型的地名实体提取器
    使用Ollama提供的大模型能力和deepseek-r1模型提取所有地名实体
    """

    # 常见战争事件后缀
    WAR_EVENT_SUFFIXES = ['之战', '战役', '起义', '叛乱', '围城', '会战', '大战', '征讨', '远征']

    # 常见历史人物称号
    PERSON_TITLES = ['帝', '王', '将军', '太守', '丞相', '元帅', '侯', '公', '君', '主', '帅', '将']

    # 常见组织/政权关键词
    ORG_KEYWORDS = ['国', '朝', '军', '部', '盟', '部族', '王朝', '政权', '军队', '势力']

    # 常见地点关键词
    PLACE_KEYWORDS = ['城', '关', '州', '郡', '县', '府', '道', '路', '山', '河', '江', '湖', '海']

    # 常见朝代名称
    def __init__(self, model_name="deepseek-r1:7b", known_entities=None):
        """初始化提取器

        Args:
            model_name: 大模型名称
            known_entities: 已知实体列表（从知识图谱加载）
        """
        self.model_name = model_name
        self._entity_cache = OrderedDict()
        self._known_entities = set(known_entities or [])
        logger.info(f"已初始化大模型地名实体提取器，使用模型: {model_name}")
        if self._known_entities:
            logger.info(f"已加载 {len(self._known_entities)} 个已知实体")

    def load_known_entities(self, neo4j_db):
        """从知识图谱加载已知实体列表

        Args:
            neo4j_db: Neo4j数据库实例
        """
        try:
            query = """
            MATCH (n)
            RETURN DISTINCT n.name as name
            LIMIT 5000
            """
            results = neo4j_db.graph.run(query).data()
            self._known_entities = {row['name'] for row in results if row.get('name')}
            logger.info(f"从知识图谱加载了 {len(self._known_entities)} 个已知实体")
        except Exception as e:
            logger.warning(f"加载已知实体失败: {e}")

    def _extract_by_rules(self, text):
        """基于规则的快速实体提取

        Args:
            text: 输入文本

        Returns:
            list: 提取的实体列表
        """
        entities = set()

        # 1. 匹配战争事件名称（包含特定后缀）
        for suffix in self.WAR_EVENT_SUFFIXES:
            # 匹配 "XXX之战" 格式
            pattern = f'[一-龥]{{2,}}{re.escape(suffix)}'
            matches = re.findall(pattern, text)
            entities.update(matches)

        # 2. 匹配朝代名称
        for dynasty in self.DYNASTIES:
            if dynasty in text:
                entities.add(dynasty)

        # 3. 匹配已知实体（从知识图谱加载）
        for known_entity in self._known_entities:
            if len(known_entity) >= 2 and known_entity in text:
                entities.add(known_entity)

        # 4. 匹配带称号的人物（如"曹操"、"刘备"等已知实体已在步骤3中匹配）
        # 这里匹配 "X将军"、"X帝" 等格式
        for title in self.PERSON_TITLES:
            pattern = f'[一-龥]{{1,4}}{re.escape(title)}'
            matches = re.findall(pattern, text)
            # 过滤掉纯称号（如"将军"、"帝王"）
            for match in matches:
                if len(match) > len(title):
                    entities.add(match)

        # 5. 匹配地名（带特定后缀）
        for keyword in self.PLACE_KEYWORDS:
            pattern = f'[一-龥]{{2,}}{re.escape(keyword)}'
            matches = re.findall(pattern, text)
            entities.update(matches)

        return list(entities)

    def extract_entities(self, text):
        """
        重点：从文本中提取所有实体（包括战争事件名称、人物、组织、地点）

        优化策略：
        1. 先尝试规则匹配（快速，不调用模型）
        2. 如果规则匹配到足够实体（>=2个），直接返回
        3. 否则调用大模型提取

        Args:
            text: 输入文本

        Returns:
            list: 提取的实体列表
        """
        if not text or len(text.strip()) == 0:
            logger.info("输入文本为空，无法提取实体")
            return []

        # 记录文本的开头部分作为示例
        text_preview = text[:100] + "..." if len(text) > 100 else text
        logger.info(f"准备提取文本中的地名实体，文本长度: {len(text)} 字符，文本开头: '{text_preview}'")

        # 检查缓存
        cache_key = text.strip()
        cached_entities = _cache_get(self._entity_cache, cache_key)
        if cached_entities is not None:
            logger.info(f"命中实体抽取缓存: {cached_entities}")
            return cached_entities

        # ========== 优化：先尝试规则匹配 ==========
        rule_start_time = time.time()
        rule_entities = self._extract_by_rules(text)
        rule_time = time.time() - rule_start_time
        logger.info(f"规则匹配完成，耗时: {rule_time:.4f}秒，匹配到 {len(rule_entities)} 个实体")

        # 如果规则匹配到足够实体（>=2个），直接返回
        if len(rule_entities) >= 2:
            logger.info(f"规则匹配到足够实体，跳过模型调用: {rule_entities}")
            result = self._post_process_entities(rule_entities)
            _cache_set(self._entity_cache, cache_key, result)
            return result

        # ========== 规则匹配不足，调用大模型 ==========
        logger.info(f"规则匹配实体不足，调用大模型进行提取...")

        # 构建提示词
        prompt = self._build_prompt(text)

        try:
            # 记录总体开始时间
            total_start_time = time.time()

            # 记录模型调用开始时间
            model_start_time = time.time()
            logger.info(f"调用 {self.model_name} 模型进行实体提取...")
            # 调用大模型进行实体提取
            response = ollama.chat(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一位中国历史战争事件信息抽取专家，"
                                    "擅长识别战争事件、人物、组织和地名实体。"
                                   "不要遗漏任何实体。返回的实体应当是具体的实体名称，而不是泛指的概念。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                stream=False,
                keep_alive="10m"
            )

            # 记录模型调用耗时
            model_time = time.time() - model_start_time
            logger.info(f"模型响应成功，耗时: {model_time:.2f}秒，"
                  f"响应长度: {len(response['message']['content'])} 字符，开始解析实体...")

            # 记录解析开始时间
            parse_start_time = time.time()

            # 从响应中解析实体
            extracted_entities = self._parse_entities_from_response(response['message']['content'])

            # 记录解析耗时
            parse_time = time.time() - parse_start_time

            # 如果解析到了实体，记录一些示例
            if extracted_entities:
                entity_examples = ', '.join(extracted_entities[:5])
                entity_examples += "..." if len(extracted_entities) > 5 else ""
                logger.info(f"解析得到 {len(extracted_entities)} 个初步实体，解析耗时: {parse_time:.2f}秒，示例: {entity_examples}")
            else:
                logger.info(f"解析完成但未找到任何实体，解析耗时: {parse_time:.2f}秒，请检查文本内容或模型响应")

            # 记录后处理开始时间
            postprocess_start_time = time.time()

            # 过滤和排序结果
            result = self._post_process_entities(extracted_entities)

            # 记录后处理耗时
            postprocess_time = time.time() - postprocess_start_time

            # 计算总体耗时
            total_time = time.time() - total_start_time

            # 记录详细的性能统计
            logger.info(f"地名实体提取完成，总耗时: {total_time:.2f}秒")
            logger.info(f"性能指标 - 模型调用: {model_time:.2f}秒 ({model_time/total_time:.1%}), 解析: {parse_time:.2f}秒 ({parse_time/total_time:.1%}), 后处理: {postprocess_time:.2f}秒 ({postprocess_time/total_time:.1%})")

            # 记录结果统计信息
            if result:
                result_examples = ', '.join(result[:5])
                result_examples += "..." if len(result) > 5 else ""
                avg_entity_length = sum(len(entity) for entity in result) / max(1, len(result))
                logger.info(f"实体统计 - 初始解析: {len(extracted_entities)}个, 最终有效: {len(result)}个, 过滤率: {(1 - len(result)/max(1, len(extracted_entities))):.2%}")
                logger.info(f"实体质量 - 平均长度: {avg_entity_length:.1f}字符, 最终实体示例: {result_examples}")
                logger.info(f"提取比率 - 每千字符实体数: {(len(result) * 1000 / max(1, len(text))):.2f}")
            else:
                logger.info(f"警告: 后处理后没有剩余有效实体，请检查过滤条件或原始提取结果")

            _cache_set(self._entity_cache, cache_key, result)
            return result

        except Exception as e:
            error_type = type(e).__name__
            error_details = str(e)
            logger.warning(f"大模型实体提取失败: {error_type} - {error_details}")
            import traceback
            logger.warning(f"错误追踪: {traceback.format_exc()}")
            # 如果大模型调用失败，返回空列表
            return []

    def _build_prompt(self, text):
        """构建提示词，引导模型提取所有实体"""
        prompt = f"""请从下面文本中提取所有实体：

{text}

需要抽取的实体类型包括：
1. 战争事件名称
2. 人物姓名
3. 国家 / 朝代 / 军队 / 政权 / 组织
4. 地点（古地名）

要求：
- 不要遗漏重要实体，提取所有实体

请以JSON格式返回提取结果，格式如下:
```json
{{
  "entities": ["赤壁之战","曹操","刘备","东吴","长江", ...]
}}
```

只返回JSON结果，不要有其他解释。
"""

        return prompt

    def _parse_entities_from_response(self, response_text):
        """从模型响应中解析实体列表"""
        try:
            # 尝试从响应中提取JSON部分
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1

            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                try:
                    data = json.loads(json_str)
                    if 'entities' in data and isinstance(data['entities'], list):
                        logger.info(f"成功通过JSON格式解析，找到实体数量: {len(data['entities'])}")
                        return data['entities']
                except Exception as json_err:
                    logger.warning(f"JSON解析失败: {str(json_err)}")

            # 如果没找到JSON或解析失败，尝试其他解析方法
            # 查找列表形式
            if '[' in response_text and ']' in response_text:
                list_start = response_text.find('[')
                list_end = response_text.rfind(']') + 1
                if list_start >= 0 and list_end > list_start:
                    list_str = response_text[list_start:list_end]
                    try:
                        entities = json.loads(list_str)
                        if isinstance(entities, list):
                            logger.info(f"通过列表格式解析，找到实体数量: {len(entities)}")
                            return entities
                    except Exception as list_err:
                        logger.warning(f"列表解析失败: {str(list_err)}")

            logger.warning("标准解析失败，尝试按行分割进行解析")
            # 回退方案：按行分割并清理
            lines = response_text.split('\n')
            entities = []
            for line in lines:
                line = line.strip()
                # 移除行首的数字、点、破折号等前缀
                line = line.lstrip('0123456789.- "\'')
                line = line.strip()
                if line and len(line) >= 2:
                    entities.append(line)

            logger.info(f"通过行分割解析，找到实体数量: {len(entities)}")
            return entities

        except Exception as e:
            logger.warning(f"解析模型响应失败: {str(e)}")
            logger.info(f"原始响应: {response_text}")
            return []

    def _post_process_entities(self, entities):
        """对提取的实体进行后处理，包括去重、过滤和排序"""
        if not entities:
            return []

        # 去重
        unique_entities = list(set(entities))
        logger.info(f"实体去重: {len(entities)} -> {len(unique_entities)}个")

        # 过滤明显不是地名的实体和太短的实体
        filtered_entities = [entity for entity in unique_entities
                            if len(entity) >= 2 and not self._should_filter(entity)]

        logger.info(f"实体过滤: {len(unique_entities)} -> {len(filtered_entities)}个")
        if len(unique_entities) > len(filtered_entities):
            filtered_out = set(unique_entities) - set(filtered_entities)
            logger.info(f"被过滤掉的实体: {', '.join(filtered_out)}")

        # 按长度排序（优先考虑较长的地名，通常更具体）
        filtered_entities.sort(key=len, reverse=True)

        # 返回所有有效实体，不限制数量
        return filtered_entities

    def _should_filter(self, entity):
        """检查实体是否应该被过滤"""
        # 过滤常见的非事件 / 非人物 / 非组织 / 非地点词汇
        non_entity_words = [

            # ===== 疑问词 / 语气词 =====
            "什么", "哪些", "如何", "为何", "为什么", "怎么", "怎样", "哪里", "谁",
            "何时", "多少", "几时", "是否", "能否", "可否",

            # ===== 泛指提问表达 =====
            "情况", "原因", "结果", "过程", "影响", "背景", "意义",
            "经过", "结局", "发展", "变化", "评价", "作用",

            # ===== 常见功能词 =====
            "是什么", "有什么", "有哪些", "怎么样", "怎么办", "属于", "位于",
            "发生", "发生在", "参与", "进行", "展开", "爆发", "结束",

            # ===== 抽象历史概念 =====
            "历史", "时期", "时代", "阶段", "当时", "后来", "之前", "之后",
            "古代", "近代", "现代", "当年", "早期", "晚期",

            # ===== 泛指地理概念（非具体地名） =====
            "地区", "地方", "区域", "位置", "地点",
            "城市", "省份", "国家", "行政区划",
            "边境", "边界", "境内", "境外",
            "内地", "中原", "南方", "北方",

            # ===== 模糊行政称谓 =====
            "县城", "府城", "郡县", "州县", "辖区",
            "首府", "都城", "京城", "国都",

            # ===== 自然地理泛词 =====
            "山川", "河流", "江河", "湖泊", "平原",
            "高原", "山地", "沙漠", "草原",

            # ===== 战争泛词（不是具体事件名）=====
            "战争", "战斗", "战役", "冲突", "交战",
            "进攻", "防守", "围攻", "突围",
            "作战", "交锋", "会战",

            # ===== 组织泛称 =====
            "军队", "部队", "兵力", "兵马", "士兵",
            "敌军", "友军", "联军",
            "朝廷", "政府", "官府", "朝廷军",

            # ===== 人物泛称 =====
            "将军", "士兵", "皇帝", "国王", "大臣",
            "统帅", "主帅", "元帅", "将领",
            "君主", "帝王",

            # ===== 系统 / 无效词 =====
            "问题", "内容", "资料", "信息", "文本",
            "描述", "介绍", "分析", "说明"
        ]

        # 检查是否是非地名词汇
        if entity in non_entity_words:
            return True
        
        # 过滤纯数字
        if entity.isdigit():
            return True
        
        # 过滤只有一个字符的实体
        if len(entity) <= 1:
            return True
        
        return False 
