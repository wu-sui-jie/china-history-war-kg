from __future__ import annotations

# json_to_excel.py
"""
JSON转Excel转换器
适配新的事件模型（拆分Scale字段、新增relations等）"""

import json
import pandas as pd
from pathlib import Path
from typing import Dict, List, Any


class JsonToExcelConverter:
    """JSON转Excel转换器"""

    def __init__(self):
        pass

    def _load_json(self, json_path: Path) -> Dict:
        """加载JSON文件"""
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _replace_null(self, value):
        """
        将None/null值替换为字符串"null"
        确保Excel中显示"null"而不是空单元格
        """
        if value is None:
            return "null"
        return value

    def _process_dict_nulls(self, data: dict) -> dict:
        """
        处理字典中的所有值，将None替换为"null"
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, dict):
                result[key] = self._process_dict_nulls(value)
            elif isinstance(value, list):
                result[key] = [self._process_dict_nulls(item) if isinstance(item, dict) else self._replace_null(item)
                               for item in value]
            else:
                result[key] = self._replace_null(value)
        return result

    def _process_list_nulls(self, data_list: List[dict]) -> List[dict]:
        """
        处理列表中的所有字典，将None替换为"null"
        """
        return [self._process_dict_nulls(item) for item in data_list]

    def convert_all(self, json_dir: Path, base_name: str):
        """
        转换所有JSON文件为Excel
        """
        excel_dir = json_dir / "excel"
        excel_dir.mkdir(exist_ok=True)

        # 1. 地点表
        self._convert_places(json_dir / "1_places.json", excel_dir / f"{base_name}_地点.xlsx")

        # 2. 人物表
        self._convert_persons(json_dir / "2_persons.json", excel_dir / f"{base_name}_人物.xlsx")

        # 3. 组织表
        self._convert_organizations(json_dir / "3_organizations.json", excel_dir / f"{base_name}_组织.xlsx")

        # 4. 事件-地点关系表
        self._convert_event_place_relations(json_dir / "4_event_place_relations.json",
                                            excel_dir / f"{base_name}_事件地点关系.xlsx")

        # 5. 事件-人物关系表
        self._convert_event_person_relations(json_dir / "5_event_person_relations.json",
                                             excel_dir / f"{base_name}_事件人物关系.xlsx")

        # 6. 事件-组织关系表
        self._convert_event_org_relations(json_dir / "6_event_organization_relations.json",
                                          excel_dir / f"{base_name}_事件组织关系.xlsx")

        # 7. 事件-事件关系表
        self._convert_event_event_relations(json_dir / "7_event_event_relations.json",
                                            excel_dir / f"{base_name}_事件事件关系.xlsx")

        # 8. 事件表（适配新模型）
        self._convert_events(json_dir / "8_events.json", excel_dir / f"{base_name}_事件.xlsx")

        # 9. 全部整合（多个sheet）
        self._convert_all_in_one(json_dir / "9_final_all.json", excel_dir / f"{base_name}_全部数据.xlsx")

    def _convert_places(self, json_path: Path, excel_path: Path):
        """转换地点表"""
        data = self._load_json(json_path)
        places = data.get("places", [])

        # 处理null值
        places = self._process_list_nulls(places)

        # 按照模板格式映射字段
        rows = []
        for place in places:
            row = {
                "地点名称（geo_name）": place.get("geo_name", "null"),
                "现代名（modern_name)": place.get("modern_name", "null"),
                "朝代（DynastyName)": place.get("DynastyName", "null"),
                "省（Province）": place.get("Province", "null"),
                "市（City）": place.get("City", "null"),
                "县/区（District)": place.get("District_County", "null"),
                "具体位置（Specific_Location）": place.get("Specific_location", "null"),
                "原文片段（source_text）": place.get("source_text", "null")
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # 确保空DataFrame也有正确列名
        if df.empty:
            df = pd.DataFrame(columns=[
                "地点名称（geo_name）", "现代名（modern_name)", "朝代（DynastyName)",
                "省（Province）", "市（City）", "县/区（District)", "具体位置（Specific_Location）",
                "原文片段（source_text）"
            ])

        # 填充NaN为"null"
        df = df.fillna("null")

        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  地点表: {len(rows)} 条记录 -> {excel_path.name}")

    def _convert_persons(self, json_path: Path, excel_path: Path):
        """转换人物表"""
        data = self._load_json(json_path)
        persons = data.get("persons", [])

        # 处理null值
        persons = self._process_list_nulls(persons)

        rows = []
        for person in persons:
            row = {
                "姓名（PersonName）": person.get("PersonName", "null"),
                "所属朝代（DynastyName）": person.get("DynastyName", "null"),
                "所属组织（OrgName）": person.get("OrgName", "null"),
                "角色（Role）": person.get("Role", "null"),
                "备注（Note）": person.get("Note", "null"),
                "原文片段（source_text）": person.get("source_text", "null")
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # 确保空DataFrame也有正确列名
        if df.empty:
            df = pd.DataFrame(columns=[
                "姓名（PersonName）", "所属朝代（DynastyName）", "所属组织（OrgName）",
                "角色（Role）", "备注（Note）", "原文片段（source_text）"
            ])

        # 填充NaN为"null"
        df = df.fillna("null")

        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  人物表: {len(rows)} 条记录 -> {excel_path.name}")

    def _convert_organizations(self, json_path: Path, excel_path: Path):
        """转换组织表"""
        data = self._load_json(json_path)
        orgs = data.get("organizations", [])

        # 处理null值
        orgs = self._process_list_nulls(orgs)

        rows = []
        for org in orgs:
            row = {
                "组织名称（OrgName）": org.get("OrgName", "null"),
                "组织类型（OrgType）": org.get("OrgType", "null"),
                "所属朝代（DynastyName）": org.get("DynastyName", "null"),
                "原文片段（source_text）": org.get("source_text", "null")
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # 确保空DataFrame也有正确列名
        if df.empty:
            df = pd.DataFrame(columns=[
                "组织名称（OrgName）", "组织类型（OrgType）", "所属朝代（DynastyName）",
                "原文片段（source_text）"
            ])

        # 填充NaN为"null"
        df = df.fillna("null")

        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  组织表: {len(rows)} 条记录 -> {excel_path.name}")

    def _convert_event_place_relations(self, json_path: Path, excel_path: Path):
        """转换事件-地点关系表"""
        data = self._load_json(json_path)
        relations = data.get("event_place_relations", [])

        # 处理null值
        relations = self._process_list_nulls(relations)

        rows = []
        for rel in relations:
            row = {
                "事件名（EventName）": rel.get("EventName", "null"),
                "关系（relation）": rel.get("relation", "null"),
                "地点名（modern_name)": rel.get("modern_name", "null"),
                "原文证据（evidence）": rel.get("evidence", "null")
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # 确保空DataFrame也有正确列名
        if df.empty:
            df = pd.DataFrame(columns=[
                "事件名（EventName）", "关系（relation）", "地点名（modern_name)",
                "原文证据（evidence）"
            ])

        # 填充NaN为"null"
        df = df.fillna("null")

        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  事件-地点关系表: {len(rows)} 条记录 -> {excel_path.name}")

    def _convert_event_person_relations(self, json_path: Path, excel_path: Path):
        """转换事件-人物关系表"""
        data = self._load_json(json_path)
        relations = data.get("event_person_relations", [])

        # 处理null值
        relations = self._process_list_nulls(relations)

        rows = []
        for rel in relations:
            row = {
                "事件名（EventName）": rel.get("EventName", "null"),
                "关系（relation）": rel.get("relation", "null"),
                "人物名（PersonName）": rel.get("PersonName", "null"),
                "原文证据（evidence）": rel.get("evidence", "null")
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # 确保空DataFrame也有正确列名
        if df.empty:
            df = pd.DataFrame(columns=[
                "事件名（EventName）", "关系（relation）", "人物名（PersonName）",
                "原文证据（evidence）"
            ])

        # 填充NaN为"null"
        df = df.fillna("null")

        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  事件-人物关系表: {len(rows)} 条记录 -> {excel_path.name}")

    def _convert_event_org_relations(self, json_path: Path, excel_path: Path):
        """转换事件-组织关系表"""
        data = self._load_json(json_path)
        relations = data.get("event_organization_relations", [])

        # 处理null值
        relations = self._process_list_nulls(relations)

        rows = []
        for rel in relations:
            row = {
                "事件名（EventName）": rel.get("EventName", "null"),
                "关系（relation）": rel.get("relation", "null"),
                "组织名（OrgName）": rel.get("OrgName", "null"),
                "原文证据（evidence）": rel.get("evidence", "null")
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # 确保空DataFrame也有正确列名
        if df.empty:
            df = pd.DataFrame(columns=[
                "事件名（EventName）", "关系（relation）", "组织名（OrgName）",
                "原文证据（evidence）"
            ])

        # 填充NaN为"null"
        df = df.fillna("null")

        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  事件-组织关系表: {len(rows)} 条记录 -> {excel_path.name}")

    def _convert_event_event_relations(self, json_path: Path, excel_path: Path):
        """转换事件-事件关系表"""
        data = self._load_json(json_path)
        relations = data.get("event_event_relations", [])

        # 处理null值
        relations = self._process_list_nulls(relations)

        rows = []
        for rel in relations:
            row = {
                "事件A名称（EventName_A）": rel.get("EventName_A", "null"),
                "关系（relation）": rel.get("relation", "null"),
                "事件B名称（EventName_B）": rel.get("EventName_B", "null"),
                "原文证据（evidence）": rel.get("evidence", "null")
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # 确保空DataFrame也有正确列名
        if df.empty:
            df = pd.DataFrame(columns=[
                "事件A名称（EventName_A）", "关系（relation）", "事件B名称（EventName_B）",
                "原文证据（evidence）"
            ])

        # 填充NaN为"null"
        df = df.fillna("null")

        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  事件-事件关系表: {len(rows)} 条记录 -> {excel_path.name}")

    def _convert_events(self, json_path: Path, excel_path: Path):
        """转换事件表（适配新模型）"""
        data = self._load_json(json_path)
        events = data.get("events", [])

        # 处理null值
        events = self._process_list_nulls(events)

        rows = []
        for event in events:
            # 处理relations字段（转换为字符串）
            relations = event.get("relations", [])
            if relations and relations != "null":
                relations_str = "; ".join([
                    f"{r.get('type', '')}→{r.get('to', '')}({r.get('evidence', '')})"
                    for r in relations if isinstance(r, dict)
                ])
            else:
                relations_str = "null"

            row = {
                "事件名称（EventName）": event.get("EventName", "null"),
                "事件类型（EventType）": event.get("EventType", "null"),
                "开始时间（StartDate）": event.get("StartDate", "null"),
                "结束时间（EndDate)": event.get("EndDate", "null"),
                "朝代（DynastyName）": event.get("DynastyName", "null"),
                "地点（Place）": event.get("Place", "null"),
                "主动方（Aggressor）": event.get("Aggressor", "null"),
                "防守方（Defender）": event.get("Defender", "null"),
                "盟友/支援方（Allies）": event.get("Allies", "null"),
                "结果（Result）": event.get("Result", "null"),
                "指挥官（Commanders）": event.get("Commanders", "null"),
                "关键人物（KeyPersons）": event.get("KeyPersons", "null"),
                "行为（Action）": event.get("Action", "null"),
                "兵力规模（TroopSize）": event.get("TroopSize", "null"),
                "时间跨度（Duration）": event.get("Duration", "null"),
                "地理范围（GeographicScope）": event.get("GeographicScope", "null"),
                "伤亡人数（Casualties）": event.get("Casualties", "null"),
                "来源文献（source）": event.get("source", "null"),
                "影响（Impact）": event.get("Impact", "null"),
                "事件关系（relations）": relations_str,
                "原文片段（source_text）": event.get("source_text", "null"),
                "备注（Remark）": event.get("Remark", "null")
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # 确保空DataFrame也有正确列名
        if df.empty:
            df = pd.DataFrame(columns=[
                "事件名称（EventName）", "事件类型（EventType）", "开始时间（StartDate）",
                "结束时间（EndDate)", "朝代（DynastyName）", "地点（Place）",
                "主动方（Aggressor）", "防守方（Defender）", "盟友/支援方（Allies）",
                "结果（Result）", "指挥官（Commanders）", "关键人物（KeyPersons）",
                "行为（Action）", "兵力规模（TroopSize）", "时间跨度（Duration）",
                "地理范围（GeographicScope）", "伤亡人数（Casualties）",
                "来源文献（source）", "影响（Impact）", "事件关系（relations）",
                "原文片段（source_text）", "备注（Remark）"
            ])

        # 填充NaN为"null"
        df = df.fillna("null")

        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  事件表: {len(rows)} 条记录 -> {excel_path.name}")

    def _convert_all_in_one(self, json_path: Path, excel_path: Path):
        """转换全部数据（多sheet）"""
        data = self._load_json(json_path)

        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            # Sheet 1: 地点
            places = data.get("entities", {}).get("places", [])
            if places:
                places = self._process_list_nulls(places)
                df_places = pd.DataFrame([
                    {
                        "地点名称（geo_name）": p.get("geo_name", "null"),
                        "现代名（modern_name)": p.get("modern_name", "null"),
                        "朝代（DynastyName)": p.get("DynastyName", "null"),
                        "省（Province）": p.get("Province", "null"),
                        "市（City）": p.get("City", "null"),
                        "县/区（District)": p.get("District_County", "null"),
                        "具体位置（Specific_Location）": p.get("Specific_location", "null"),
                        "原文片段（source_text）": p.get("source_text", "null")
                    }
                    for p in places
                ])
                df_places = df_places.fillna("null")
                df_places.to_excel(writer, sheet_name='地点', index=False)
            else:
                pd.DataFrame(columns=[
                    "地点名称（geo_name）", "现代名（modern_name)", "朝代（DynastyName)",
                    "省（Province）", "市（City）", "县/区（District)", 
                    "具体位置（Specific_Location）", "原文片段（source_text）"
                ]).to_excel(writer, sheet_name='地点', index=False)

            # Sheet 2: 人物
            persons = data.get("entities", {}).get("persons", [])
            if persons:
                persons = self._process_list_nulls(persons)
                df_persons = pd.DataFrame([
                    {
                        "姓名（PersonName）": p.get("PersonName", "null"),
                        "所属朝代（DynastyName）": p.get("DynastyName", "null"),
                        "所属组织（OrgName）": p.get("OrgName", "null"),
                        "角色（Role）": p.get("Role", "null"),
                        "备注（Note）": p.get("Note", "null"),
                        "原文片段（source_text）": p.get("source_text", "null")
                    }
                    for p in persons
                ])
                df_persons = df_persons.fillna("null")
                df_persons.to_excel(writer, sheet_name='人物', index=False)
            else:
                pd.DataFrame(columns=[
                    "姓名（PersonName）", "所属朝代（DynastyName）", "所属组织（OrgName）",
                    "角色（Role）", "备注（Note）", "原文片段（source_text）"
                ]).to_excel(writer, sheet_name='人物', index=False)

            # Sheet 3: 组织
            orgs = data.get("entities", {}).get("organizations", [])
            if orgs:
                orgs = self._process_list_nulls(orgs)
                df_orgs = pd.DataFrame([
                    {
                        "组织名称（OrgName）": o.get("OrgName", "null"),
                        "组织类型（OrgType）": o.get("OrgType", "null"),
                        "所属朝代（DynastyName）": o.get("DynastyName", "null"),
                        "原文片段（source_text）": o.get("source_text", "null")
                    }
                    for o in orgs
                ])
                df_orgs = df_orgs.fillna("null")
                df_orgs.to_excel(writer, sheet_name='组织', index=False)
            else:
                pd.DataFrame(columns=[
                    "组织名称（OrgName）", "组织类型（OrgType）", "所属朝代（DynastyName）",
                    "原文片段（source_text）"
                ]).to_excel(writer, sheet_name='组织', index=False)

            # Sheet 4: 事件（适配新模型）
            events = data.get("events", {}).get("events", [])
            if events:
                events = self._process_list_nulls(events)
                df_events = pd.DataFrame([
                    {
                        "事件名称（EventName）": e.get("EventName", "null"),
                        "事件类型（EventType）": e.get("EventType", "null"),
                        "开始时间（StartDate）": e.get("StartDate", "null"),
                        "结束时间（EndDate)": e.get("EndDate", "null"),
                        "朝代（DynastyName）": e.get("DynastyName", "null"),
                        "地点（Place）": e.get("Place", "null"),
                        "主动方（Aggressor）": e.get("Aggressor", "null"),
                        "防守方（Defender）": e.get("Defender", "null"),
                        "盟友/支援方（Allies）": e.get("Allies", "null"),
                        "结果（Result）": e.get("Result", "null"),
                        "指挥官（Commanders）": e.get("Commanders", "null"),
                        "关键人物（KeyPersons）": e.get("KeyPersons", "null"),
                        "行为（Action）": e.get("Action", "null"),
                        "兵力规模（TroopSize）": e.get("TroopSize", "null"),
                        "时间跨度（Duration）": e.get("Duration", "null"),
                        "地理范围（GeographicScope）": e.get("GeographicScope", "null"),
                        "伤亡人数（Casualties）": e.get("Casualties", "null"),
                        "来源文献（source）": e.get("source", "null"),
                        "影响（Impact）": e.get("Impact", "null"),
                        "原文片段（source_text）": e.get("source_text", "null"),
                        "备注（Remark）": e.get("Remark", "null")
                    }
                    for e in events
                ])
                df_events = df_events.fillna("null")
                df_events.to_excel(writer, sheet_name='事件', index=False)
            else:
                pd.DataFrame(columns=[
                    "事件名称（EventName）", "事件类型（EventType）", "开始时间（StartDate）",
                    "结束时间（EndDate)", "朝代（DynastyName）", "地点（Place）",
                    "主动方（Aggressor）", "防守方（Defender）", "盟友/支援方（Allies）",
                    "结果（Result）", "指挥官（Commanders）", "关键人物（KeyPersons）",
                    "行为（Action）", "兵力规模（TroopSize）", "时间跨度（Duration）",
                    "地理范围（GeographicScope）", "伤亡人数（Casualties）",
                    "来源文献（source）", "影响（Impact）", 
                    "原文片段（source_text）", "备注（Remark）"
                ]).to_excel(writer, sheet_name='事件', index=False)

            # Sheet 5: 事件-地点关系
            place_rels = data.get("relations", {}).get("event_place_relations", [])
            if place_rels:
                place_rels = self._process_list_nulls(place_rels)
                df_place_rels = pd.DataFrame([
                    {
                        "事件名（EventName）": r.get("EventName", "null"),
                        "关系（relation）": r.get("relation", "null"),
                        "地点名（modern_name)": r.get("modern_name", "null"),
                        "原文证据（evidence）": r.get("evidence", "null")
                    }
                    for r in place_rels
                ])
                df_place_rels = df_place_rels.fillna("null")
                df_place_rels.to_excel(writer, sheet_name='事件地点关系', index=False)
            else:
                pd.DataFrame(columns=[
                    "事件名（EventName）", "关系（relation）", "地点名（modern_name)",
                    "原文证据（evidence）"
                ]).to_excel(writer, sheet_name='事件地点关系', index=False)

            # Sheet 6: 事件-人物关系
            person_rels = data.get("relations", {}).get("event_person_relations", [])
            if person_rels:
                person_rels = self._process_list_nulls(person_rels)
                df_person_rels = pd.DataFrame([
                    {
                        "事件名（EventName）": r.get("EventName", "null"),
                        "关系（relation）": r.get("relation", "null"),
                        "人物名（PersonName）": r.get("PersonName", "null"),
                        "原文证据（evidence）": r.get("evidence", "null")
                    }
                    for r in person_rels
                ])
                df_person_rels = df_person_rels.fillna("null")
                df_person_rels.to_excel(writer, sheet_name='事件人物关系', index=False)
            else:
                pd.DataFrame(columns=[
                    "事件名（EventName）", "关系（relation）", "人物名（PersonName）",
                    "原文证据（evidence）"
                ]).to_excel(writer, sheet_name='事件人物关系', index=False)

            # Sheet 7: 事件-组织关系
            org_rels = data.get("relations", {}).get("event_organization_relations", [])
            if org_rels:
                org_rels = self._process_list_nulls(org_rels)
                df_org_rels = pd.DataFrame([
                    {
                        "事件名（EventName）": r.get("EventName", "null"),
                        "关系（relation）": r.get("relation", "null"),
                        "组织名（OrgName）": r.get("OrgName", "null"),
                        "原文证据（evidence）": r.get("evidence", "null")
                    }
                    for r in org_rels
                ])
                df_org_rels = df_org_rels.fillna("null")
                df_org_rels.to_excel(writer, sheet_name='事件组织关系', index=False)
            else:
                pd.DataFrame(columns=[
                    "事件名（EventName）", "关系（relation）", "组织名（OrgName）",
                    "原文证据（evidence）"
                ]).to_excel(writer, sheet_name='事件组织关系', index=False)

            # Sheet 8: 事件-事件关系
            event_rels = data.get("relations", {}).get("event_event_relations", [])
            if event_rels:
                event_rels = self._process_list_nulls(event_rels)
                df_event_rels = pd.DataFrame([
                    {
                        "事件A名称（EventName_A）": r.get("EventName_A", "null"),
                        "关系（relation）": r.get("relation", "null"),
                        "事件B名称（EventName_B）": r.get("EventName_B", "null"),
                        "原文证据（evidence）": r.get("evidence", "null")
                    }
                    for r in event_rels
                ])
                df_event_rels = df_event_rels.fillna("null")
                df_event_rels.to_excel(writer, sheet_name='事件事件关系', index=False)
            else:
                pd.DataFrame(columns=[
                    "事件A名称（EventName_A）", "关系（relation）", 
                    "事件B名称（EventName_B）", "原文证据（evidence）"
                ]).to_excel(writer, sheet_name='事件事件关系', index=False)

        print(f"  全部数据（多sheet）: {excel_path.name}")
