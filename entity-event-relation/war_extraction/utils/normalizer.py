"""
Shared normalization helpers for extraction, evaluation, and import.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict

#: 以本文件位置锚定项目根（entity-event-relation/）——与 llm_client / cache_manager 一致。
#: 默认 config_dir 必须是绝对路径：用相对当前工作目录的 "config" 会在换个工作目录启动时
#: "静默加载不到别名表与关系映射"，而这件事完全没有提示。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 默认配置目录（项目根下的 config/）
DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


class Normalizer:
    """
    别名与关系归一的唯一来源：抽取、评估、导入共用同一套词表（`config/aliases.json`
    与 `config/relation_types.json`），不然同一条数据在不同环节会被归成不同名字。

    另有一套代码内的规范化规则：事件名的方向词收敛、实体名的括号/空白剥离、
    事件去重用的 `EVENT_NAME_ALIASES`、以及供前端选值的 `FRONTEND_RELATION_TYPES`。
    """

    EVENT_NAME_ALIASES = {
        "神农伐斧之战": "神农斧隧之战",
        "神农伐斧隧之战": "神农斧隧之战",
        "武丁征下旨": "武丁征伐下旨",
        "周文王攻崇": "周文王东征灭崇之战",
        "周文王东征灭崇": "周文王东征灭崇之战",
        "牧野之战": "周武王灭商牧野之战",
        "周昭王攻荆楚": "周昭王南征荆楚",
        "周昭王攻荆楚之战": "周昭王南征荆楚",
        "黄帝蚩尤涿鹿之战": "黄帝、蚩尤涿鹿之战",
        "黄帝炎帝阪泉之战": "黄帝、炎帝阪泉之战",
        "寒淀攻灭斟灌氏和斟寻氏": "寒浞攻灭斟灌氏和斟寻氏",
        "寒足攻灭斟灌氏和斟寻氏": "寒浞攻灭斟灌氏和斟寻氏",
    }

    ENTITY_ALIASES = {
        "神农氏": "神农",
        "夏桀": "桀",
        "商纣王": "纣王",
        "舜的部落": "舜部落",
        "尧部落联盟": "尧部落",
        "东夷的军队": "东夷",
        "三苗部族": "三苗",
        "三苗族": "三苗",
        "三苗部落": "三苗",
        "三苗部落（修蛇部落）": "三苗",
        "三苗族（以修蛇为图腾的部落）": "三苗",
        "蚩尤、九黎族": "九黎",
        # 原书里"孙膑"写作"孙滨"6 处、写作"孙膑"9 处（人工标注只用"孙膑"），
        # 两者 fuzz.ratio 只有 50，低于实体匹配阈值 70，配不上——所以按别名归一
        # （与"寒淀→寒浞"同一机制），让它在抽取阶段就收敛成标注用字。
        # 这类"错字变体"必须走归一：整条丢掉连预测都不会产生，方向正好反了。
        "孙滨": "孙膑",
    }

    CANONICAL_EVENT_RELATION_TYPES = {
        "因果关系", "顺承关系", "并列关系", "包含关系", "条件关系",
    }

    RELATION_ALIASES = {
        "因果": "因果关系",
        "因果关系": "因果关系",
        "导致": "因果关系",
        "引发": "因果关系",
        "顺承": "顺承关系",
        "顺承关系": "顺承关系",
        "先后": "顺承关系",
        "前后相继": "顺承关系",
        "并列": "并列关系",
        "并发": "并列关系",
        "并列关系": "并列关系",
        "并发关系": "并列关系",
        "包含": "包含关系",
        "包含关系": "包含关系",
        "条件": "条件关系",
        "条件关系": "条件关系",
    }

    FRONTEND_RELATION_TYPES = {
        "因果关系", "顺承关系", "并列关系", "包含关系", "条件关系",
        "主战场", "次要战场", "出发地", "目的地", "途经地", "驻防地", "指挥所", "补给地", "战略要地", "议和地点",
        "发起方", "防守方", "支援方", "同盟方", "投降方", "被俘方", "议和方", "调停方", "参战方",
        "统帅", "将领", "谋士", "使者", "君主", "参与者", "俘虏", "阵亡", "投降", "叛变", "可汗",
    }

    def __init__(self, config_dir: str = None):
        self.config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
        self.aliases = self._load_json("aliases.json")
        self.relation_map = self._build_relation_map(self._load_json("relation_types.json"))

    def _load_json(self, filename: str) -> Dict:
        path = self.config_dir / filename
        if not path.exists():
            # 不静默返回 {}：别名表没加载会让归一化悄悄失效（实体/关系匹配不到一起），
            # 而日志里一点痕迹都没有，指标却已经变了。
            print(f"  [配置缺失] {path} 不存在：{filename} 相关的别名/映射本次按空表处理，"
                  f"抽取与评估结果都会受影响")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _build_relation_map(self, data: Dict) -> Dict[str, str]:
        mapping = {}
        for standard, variants in data.items():
            mapping[standard] = standard
            for variant in variants:
                mapping[variant] = standard
        return mapping

    def _strip_wrappers(self, value: str) -> str:
        value = (value or "").strip()
        value = re.sub(r"[（(].*?[）)]", "", value)
        value = re.sub(r"\s+", "", value)
        return value

    def standardize_event_name(self, value: str) -> str:
        value = self._strip_wrappers(value)
        value = self.EVENT_NAME_ALIASES.get(value, value)
        value = value.replace("神农伐斧", "神农斧隧")
        value = value.replace("征下旨", "征伐下旨")

        # 分层归一后会出现"武丁南南征荆楚"这类重复方向词，这里收敛掉
        if "荆楚" in value:
            if any(token in value for token in ["南攻荆楚", "攻荆楚", "征荆楚"]):
                value = re.sub(r".*?(南攻荆楚|攻荆楚之战|攻荆楚|征荆楚之战|征荆楚)", "周昭王南征荆楚" if value.startswith("周昭王") else "南征荆楚", value)
                if not value.startswith("周昭王") and not value.startswith("武丁"):
                    value = value.replace("南征荆楚", "南征荆楚")
            value = value.replace("南南征荆楚", "南征荆楚")
            value = value.replace("周昭王周昭王南征荆楚", "周昭王南征荆楚")
            if value == "南征荆楚":
                value = "武丁南征荆楚"

        if value.startswith("武丁") and "荆楚" in value:
            value = "武丁南征荆楚"
        if value.startswith("周昭王") and "荆楚" in value:
            value = "周昭王南征荆楚"
        if value.startswith("周文王") and "崇" in value:
            value = "周文王东征灭崇之战"

        return value.strip()

    def normalize_event_name(self, value: str) -> str:
        value = self.standardize_event_name(value)
        value = re.sub(r"[，。、“”‘’：；,.;!?]", "", value)
        for token in [
            "之战",
            "战争",
            "战役",
            "征伐",
            "征讨",
            "东征",
            "西征",
            "南征",
            "北伐",
            "进攻",
            "攻击",
            "灭商",
            "灭夏",
            "复国战争",
            "复国",
        ]:
            value = value.replace(token, "")
        return value.strip()

    def standardize_person_name(self, value: str) -> str:
        value = self._strip_wrappers(value)
        value = self.ENTITY_ALIASES.get(value, value)
        if value.endswith("氏") and value in {"神农氏"}:
            value = value[:-1]
        return value.strip()

    def standardize_org_name(self, value: str) -> str:
        value = self._strip_wrappers(value)
        value = self.ENTITY_ALIASES.get(value, value)
        if value in {"三个宗族集团", "夷族"}:
            return ""
        if value.startswith(("三苗", "东夷", "犬戎", "鬼方", "羌方", "西戎", "徐夷", "淮夷", "荆蛮", "九黎")):
            for suffix in ["的军队", "部落联盟", "部落", "部族", "族"]:
                value = value.replace(suffix, "")
        if value == "东夷偃姓":
            value = "东夷"
        if value == "九黎族":
            value = "九黎"
        return value.strip()

    def normalize_entity_name(self, value: str) -> str:
        value = (value or "").strip()
        value = self.aliases.get(value, value)
        value = self.ENTITY_ALIASES.get(value, value)
        value = self._strip_wrappers(value)
        return value

    def normalize_relation(self, value: str) -> str:
        value = (value or "").strip()
        value = self.RELATION_ALIASES.get(value, value)
        if value in self.CANONICAL_EVENT_RELATION_TYPES:
            return value
        if value in self.FRONTEND_RELATION_TYPES:
            return value
        value = self.relation_map.get(value, value)
        value = self.RELATION_ALIASES.get(value, value)
        if value in self.CANONICAL_EVENT_RELATION_TYPES:
            return value
        if value.endswith("关系"):
            value = value[:-2]
        value = self.RELATION_ALIASES.get(value, value)
        if value in self.CANONICAL_EVENT_RELATION_TYPES or value in self.FRONTEND_RELATION_TYPES:
            return value
        return self.relation_map.get(value, value)

    def is_placeholder_value(self, value: str) -> bool:
        value = (value or "").strip()
        if not value:
            return True
        compact = re.sub(r"\s+", "", value)
        placeholder_values = {
            "未明确",
            "不详",
            "未知",
            "无",
            "暂无",
            "待考",
            "佚失",
            "缺失",
            "null",
            "none",
            "n/a",
            "na",
            "未提及",
        }
        return compact.lower() in placeholder_values

    def is_noisy_place_name(self, value: str) -> bool:
        value = (value or "").strip()
        if self.is_placeholder_value(value):
            return True
        compact = re.sub(r"\s+", "", value)
        if len(compact) < 2:
            return True

        direct_matches = {
            "今",
            "当时",
            "当地",
            "境内",
            "中原",
            "东方",
            "西方",
            "南方",
            "北方",
            "中部",
            "东部",
            "西部",
            "南部",
            "北部",
            "一带",
            "地区",
        }
        if compact in direct_matches:
            return True

        noise_patterns = [
            r"^今.+至.+$",
            r"^今.+一带$",
            r"^今.+地区$",
            r"^今.+附近$",
            r"^今.+流域$",
            r".+[东西南北中]部$",
            r".+[东西南北中]方$",
            r".+地区$",
            r".+一带$",
            r".+流域$",
            r".+沿岸$",
            r".+境内$",
            r".+之间$",
            r".+[至到].+$",
            r".*[（(].*[)）].*",
            r".*等\d+方国$",
            r".*等\d+国$",
            r".*等\d+部落$",
        ]
        return any(re.search(pattern, compact) for pattern in noise_patterns)
