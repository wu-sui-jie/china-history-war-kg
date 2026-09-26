"""生成黄金问答集草稿（数据 grounded，非凭空编造）。

用法：python scripts/gen_draft_bank.py [--version 20260904_v2]

作用：
- 从知识库快照中选取锚点实体/事件，生成覆盖 main / long_rewrite / filter_loss /
  refusal 四类套件的题库草稿；
- gold_notes 与 source_ref 均来自快照中的事件卡字段/实体 id，供人工审核时对照；
- 题库人工审核后直接编辑 data/eval/<version>/questions.jsonl（本脚本只负责首次草稿）。

产出：data/eval/<version>/questions.jsonl + questions.meta.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.bank import GoldQuestion, QuestionBank, save_bank  # noqa: E402

DOC1 = "doc01_中国历代战争简史"

# 人物锚点题与拒答题：草稿阶段无法确定答案在哪本书，expected_docs 留空待人工标注
_NO_DOC_IDS = {"M11", "M12", "M13", "M14", "R06"}

# 每条：(id, suite, category, question, filters, answerable, expected, notes, ref)
_SEEDS = [
    # ---- main：single_entity / relation / event_event / comparison / timeline / background ----
    ("M01", "main", "single_entity", "介绍一下长平之战。", {}, True,
     ["长平之战", "白起", "赵括", "廉颇"],
     "前260年秦军攻赵。赵将廉颇固守长平，秦军离间使赵王以赵括代廉颇；秦秘密以白起为上将军。秦军获胜，赵括战死，赵军投降被坑杀。",
     "event_0166"),
    ("M02", "main", "single_entity", "介绍一下赤壁之战。", {}, True,
     ["赤壁之战", "曹操", "周瑜"],
     "208年曹操南下与孙权、刘备争夺荆州，展开赤壁大战。孙刘联军获胜，曹操败退，奠定三国鼎立的基础。",
     "event_0189"),
    ("M03", "main", "single_entity", "介绍一下巨鹿之战。", {}, True,
     ["巨鹿之战", "项羽", "章邯"],
     "前207年项羽率楚军及诸侯联军破釜沉舟大败秦军，王离被俘，章邯投降，秦军主力被歼。",
     "event_0196"),
    ("M04", "main", "single_entity", "介绍一下淝水之战。", {}, True,
     ["淝水之战", "苻坚", "谢玄"],
     "383年前秦苻坚攻晋，东晋谢玄等以少胜多，前秦大败衰败，北方再次分裂，东晋得以偏安。",
     "event_0383"),
    ("M05", "main", "single_entity", "介绍一下涿鹿之战。", {}, True,
     ["涿鹿之战", "黄帝", "蚩尤"],
     "约四五千年前黄帝族与蚩尤九黎族战争，黄帝族获胜擒杀蚩尤，成为华夏族共同祖先。",
     "event_0005"),
    ("M06", "main", "single_entity", "介绍一下鸣条之战。", {}, True,
     ["鸣条之战", "商汤", "夏桀"],
     "夏朝末年商汤在鸣条与夏桀决战获胜，夏朝灭亡，商朝建立。",
     "event_0002"),
    ("M07", "main", "single_entity", "介绍一下官渡之战。", {}, True,
     ["官渡之战", "曹操", "袁绍"],
     "200年袁绍攻曹操，曹操以少胜多获胜，袁绍败退，奠定统一北方的基础。",
     "event_0333"),
    ("M08", "main", "single_entity", "介绍一下城濮之战。", {}, True,
     ["城濮之战", "晋文公"],
     "前632年晋齐秦宋联军在城濮大败楚陈蔡联军，晋文公一战定霸。",
     "event_0062"),
    ("M09", "main", "single_entity", "介绍一下昆阳之战。", {}, True,
     ["昆阳之战", "王莽", "刘秀"],
     "公元23年刘秀率汉军（绿林军）在昆阳以少胜多大败王莽军，为推翻王莽政权奠定基础。",
     "event_0270"),
    ("M10", "main", "single_entity", "介绍一下阪泉之战。", {}, True,
     ["阪泉之战", "黄帝", "炎帝"],
     "公元前26世纪黄帝与炎帝族战于阪泉三战三捷，炎帝族臣服，两族融合成华夏族主干。",
     "event_0006"),
    ("M11", "main", "single_entity", "介绍一下项羽。", {}, True,
     ["项羽", "巨鹿之战"],
     "秦末西楚统帅；巨鹿之战中破釜沉舟大败秦军、秦军主力被歼；后为楚汉战争一方，垓下之战阵亡。",
     "person_0303"),
    ("M12", "main", "single_entity", "介绍一下白起。", {}, True,
     ["白起", "长平之战"],
     "战国秦国统帅；长平之战中任统帅大破赵军（赵军惨败、赵国元气大伤）。",
     "person_0238"),
    ("M13", "main", "single_entity", "介绍一下韩信。", {}, True,
     ["韩信", "楚汉战争"],
     "秦末汉初汉军统帅；楚汉战争时期统率还定三秦之战、围攻废丘等战役。",
     "person_0352"),
    ("M14", "main", "single_entity", "介绍一下诸葛亮。", {}, True,
     ["诸葛亮", "刘备", "赤壁之战"],
     "三国蜀汉谋士；赤壁之战中的谋士/参与者；后参与刘备入蜀之战。",
     "person_0314"),
    ("R01", "main", "relation", "长平之战中赵军的统帅是谁？", {}, True,
     ["长平之战", "廉颇", "赵括"],
     "前期廉颇固守不战，赵王中离间计后以赵括代廉颇，赵括战死。",
     "relations#event_person_relations:638,relations#event_person_relations:639"),
    ("R02", "main", "relation", "赤壁之战中孙刘联军的主帅是谁？", {}, True,
     ["赤壁之战", "周瑜"],
     "赤壁之战孙刘联军主帅为周瑜。",
     "relations#event_person_relations:730"),
    ("R03", "main", "relation", "淝水之战的晋军主帅是谁？", {}, True,
     ["淝水之战", "谢玄"],
     "淝水之战东晋主帅为谢玄。",
     "relations#event_person_relations:2213"),
    ("R04", "main", "relation", "官渡之战的交战双方是谁？", {}, True,
     ["官渡之战", "曹操", "袁绍"],
     "袁绍率军南攻，曹操在官渡与袁绍决战并获胜。",
     "event_0333"),
    ("R05", "main", "relation", "巨鹿之战的楚军主帅是谁？", {}, True,
     ["巨鹿之战", "项羽"],
     "巨鹿之战项羽率楚军及诸侯联军大败秦军。",
     "relations#event_person_relations:772"),
    ("R06", "main", "relation", "项羽指挥过哪些重要的战役？", {}, True,
     ["项羽", "巨鹿之战"],
     "项羽相关战役：楚汉战争、巨鹿之战、项羽击齐之战等。",
     "person_0303"),
    ("E01", "main", "event_event", "鸣条之战与商朝的建立有什么关系？", {}, True,
     ["鸣条之战", "商汤", "夏朝"],
     "鸣条之战商汤推翻夏桀，夏朝灭亡，随后建立商朝。",
     "event_0002"),
    ("E02", "main", "comparison", "官渡之战和赤壁之战有什么相似之处？", {}, True,
     ["官渡之战", "赤壁之战", "曹操"],
     "两战曹操均为一方并深刻改变格局：官渡之战曹操以少胜多、奠定统一北方基础；赤壁之战孙刘联军获胜、奠定三国鼎立基础。",
     "event_0333,event_0189,person_0307"),
    ("T01", "main", "timeline", "长平之战发生在什么时候？", {}, True,
     ["长平之战", "前260年"],
     "长平之战发生在前260年（战国）。",
     "event_0166"),
    ("T02", "main", "timeline", "赤壁之战发生在哪一年？", {}, True,
     ["赤壁之战", "208年"],
     "赤壁之战发生于公元208年（东汉末年）。",
     "event_0189"),
    ("T03", "main", "timeline", "巨鹿之战发生于什么朝代？", {}, True,
     ["巨鹿之战", "秦朝"],
     "巨鹿之战发生于秦朝（前207年）。",
     "event_0196"),
    ("B01", "main", "background", "秦为什么会发动长平之战？", {}, True,
     ["长平之战", "上党"],
     "白起败韩迫韩割上党郡求和，上党郡守不愿降秦而将上党17县献赵，秦遂大举攻赵。",
     "event_0166"),
    ("B02", "main", "background", "赤壁之战产生了怎样的影响？", {}, True,
     ["赤壁之战", "三国鼎立"],
     "赤壁之战孙刘联军获胜，奠定三国鼎立的基础。",
     "event_0189"),
    ("B03", "main", "background", "淝水之战为什么以东晋胜利而告终？", {}, True,
     ["淝水之战", "东晋", "前秦", "急于求成"],
     "前秦苻坚急于求成、指挥失当且受骗采取敌前退却，东晋以少胜多。",
     "event_0383"),
    # ---- long_rewrite：长改写问题的相关文本归因 ----
    ("L01", "long_rewrite", "background", "长平之战的主要经过和结果是什么？", {}, True,
     ["长平之战", "赵括", "白起"],
     "要点：秦军获胜、赵括战死、赵军大败（白起任统帅）。口径：长改写样例，AND 优先几乎必然"
     "命中 0，观察 OR 兜底后相关文本是否进入回答/引用。",
     "event_0166"),
    ("L02", "long_rewrite", "background", "赤壁之战中曹操战败的主要原因是什么？", {}, True,
     ["赤壁之战", "曹操"],
     "要点：孙刘联军获胜、曹操败退。口径：长改写样例，观察长句下文本召回与回答覆盖。",
     "event_0189"),
    ("L03", "long_rewrite", "background", "淝水之战中东晋以少胜多打败前秦的经过是怎样的？", {}, True,
     ["淝水之战", "东晋", "前秦"],
     "要点：东晋获胜、前秦大败（前秦以多败少）。口径：长改写样例，观察 OR 兜底。",
     "event_0383"),
    ("L04", "long_rewrite", "background", "官渡之战曹操为什么能以少胜多击败袁绍？", {}, True,
     ["官渡之战", "曹操", "袁绍"],
     "要点：曹操以少胜多、击败袁绍（奠定统一北方基础）。口径：长改写样例。",
     "event_0333"),
    # ---- filter_loss：事件类型筛选的原文召回损耗 ----
    ("F01", "filter_loss", "background", "长平之战的交战过程是怎样的？",
     {"event_type": ["战略要地守卫"]}, True, ["长平之战", "赵括"],
     "要点：长平之战过程（赵括代廉颇、赵军大败）。口径：开 event_type 筛选后无该元数据的 "
     "raw/evidence 片段整批剔除，量化文本召回损耗。",
     "event_0166"),
    ("F02", "filter_loss", "background", "淝水之战的交战经过是怎样的？",
     {"event_type": ["战争"]}, True, ["淝水之战", "前秦"],
     "要点：淝水之战经过（东晋获胜、前秦大败）。口径：同上，观察开筛选前后文本召回差异。",
     "event_0383"),
    ("F03", "filter_loss", "background", "城濮之战晋军是如何取得胜利的？",
     {"event_type": ["诸侯联盟"]}, True, ["城濮之战", "晋文公"],
     "要点：晋军退避三舍、后发制人，晋文公一战定霸。口径：同上。",
     "event_0062"),
    ("F04", "filter_loss", "background", "涿鹿之战的过程是怎样的？",
     {"event_type": ["统一战争"]}, True, ["涿鹿之战", "黄帝"],
     "要点：黄帝族获胜、擒杀蚩尤。口径：同上。",
     "event_0005"),
    # ---- refusal：应拒答问题 ----
    ("X01", "refusal", "other", "赤壁之战结束后曹操去了哪里度假？", {}, False,
     [], "知识库不含度假类信息，应拒答或说明无法回答。", "event_0189"),
    ("X02", "refusal", "other", "长平之战中赵军使用过坦克吗？", {}, False,
     [], "时代不符的虚构问题，应拒答或说明无依据。", "event_0166"),
    ("X03", "refusal", "other", "项羽的邮箱地址是什么？", {}, False,
     [], "不存在的属性，应拒答。", "person_0303"),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="20260904_v2")
    p.add_argument("--force", action="store_true",
                   help="题库已存在时强制用草稿覆盖（会丢失人工审核字段，慎用）")
    args = p.parse_args()
    version = args.version
    out = Path("data/eval") / version / "questions.jsonl"
    if out.exists() and not args.force:
        # 题库可能已含人工审核结论（reviewed/reviewer/人工修改），草稿生成不得静默覆盖
        print(f"题库已存在: {out}")
        print("该文件可能含人工审核结论；如确需用草稿重建请加 --force（会覆盖审核字段）。")
        return 2

    items = []
    for row in _SEEDS:
        (qid, suite, category, question, filters, answerable,
         expected, notes, ref) = row
        docs = [] if (not answerable or qid in _NO_DOC_IDS) else [DOC1]
        items.append(GoldQuestion(
            id=qid, suite=suite, category=category, question=question,
            filters=filters, answerable=answerable,
            expected_entities=expected, expected_docs=docs,
            gold_notes=notes, source_ref=ref, annotation_version="draft-0",
        ))
    bank = QuestionBank(version=version, annotation_version="draft-0", items=items)
    check = bank.validate()
    print(f"题库条数: {len(items)}；套件分布: {check['summary']['suites']}")
    if check["errors"]:
        import json
        print("结构问题:")
        print(json.dumps(check["errors"], ensure_ascii=False, indent=1))
        return 1
    save_bank(bank, out)
    print(f"已生成: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
