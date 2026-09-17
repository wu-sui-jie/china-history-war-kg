# -*- coding: utf-8 -*-
"""按审核结论生成两个回填表（不修改任何输入文件）。

- bank_review_filled.csv：只填 reviewed 列（通过 / 留空）
- scores_review_filled.csv：只填 answer_correctness / citation_correctness / notes / reviewer
"""
from pathlib import Path

import pandas as pd

REVIEW = Path(__file__).resolve().parent
REVIEWER = "AI代理审核-ZCode(deepseek-v4-flash)"

# ---------- 第一轮：题库审核 ----------
# 留空（存疑/保留）的行：A 类四题，按 REVIEW_GUIDE §2.3 建议方案① 保留原题作缺陷复现样本
BANK_HOLD = {"B01", "E01", "R05", "T03"}

bank = pd.read_csv(REVIEW / "bank_review.csv", encoding="utf-8-sig", dtype=str).fillna("")
orig_cols = list(bank.columns)
orig_ids = list(bank["id"])

bank["reviewed"] = ["通过" if i not in BANK_HOLD else "" for i in bank["id"]]
assert list(bank["id"]) == orig_ids
assert list(bank.columns) == orig_cols
bank.to_csv(REVIEW / "bank_review_filled.csv", index=False, encoding="utf-8-sig")

# ---------- 第二轮：答案/引用评分 ----------
scores = pd.read_csv(REVIEW / "scores_review_20260913_143352.csv", encoding="utf-8-sig", dtype=str).fillna("")
s_cols = list(scores.columns)
s_ids = list(scores["qid"])

R = {
    # qid: (answer_correctness, citation_correctness, notes)
    "M01": ("partial", "supported", "回答给出长平之战地名关系与「白起=统帅」，但未给出经过与结果（廉颇固守、赵括代廉颇、秦军获胜、赵括战死）；输出为离线摘要清单，末尾截断"),
    "M02": ("partial", "unsupported", "仅地名关系＋曹操南征荆州之战卡（208年7月）；未给出赤壁之战结果与影响，引用中也无赤壁之战事件卡"),
    "M03": ("incorrect", "supported", "只有地名关系与一段楚汉战争原文，未触及巨鹿之战经过与结果（破釜沉舟、王离被俘、章邯投降）；相关原文在引用中但未进入回答"),
    "M04": ("partial", "supported", "含「前秦苻坚百万大军的进攻宣告失败」，未给出383年与影响；引用含淝水之战卡与证据片段"),
    "M05": ("partial", "supported", "给出黄帝/蚩尤统帅等人物关系，未给出「黄帝族获胜、擒杀蚩尤、成为华夏族共同祖先」"),
    "M06": ("partial", "supported", "仅凭章节综述片段「商灭夏鸣条之战」沾到要点，未给出夏桀战败、夏亡商立的具体内容"),
    "M07": ("incorrect", "supported", "只有地名关系与曹操攻占射犬之战卡（199年），未给出200年袁绍南攻、曹操以少胜多等要点；官渡之战关键原文在引用中"),
    "M08": ("partial", "supported", "给出城濮之战人物关系（晋文公/先轸统帅），未给出「大败楚陈蔡联军、一战定霸」"),
    "M09": ("partial", "supported", "含昆阳之战卡（公元23年、王莽军/汉军），未给出刘秀以少胜多、为推翻王莽政权奠基"),
    "M10": ("partial", "supported", "含公元前26世纪、三战三捷片段与阪泉之战卡；「炎帝族臣服、两族融合」因截断未出现"),
    "M11": ("partial", "supported", "列出项羽为统帅的9个战役（含巨鹿之战、楚汉战争），未给出垓下之战阵亡与破釜沉舟细节"),
    "M12": ("partial", "supported", "列出白起为统帅的多个战役，但完全未提及长平之战（gold 关键要点，且为标注词）"),
    "M13": ("partial", "supported", "给出还定三秦之战、围攻废丘等统帅关系；未出现标注词「楚汉战争」及身份表述"),
    "M14": ("partial", "supported", "覆盖「赤壁之战谋士/参与者」「刘备入蜀之战」两项要点；未给出「三国蜀汉」身份表述，最接近 correct"),
    "R01": ("partial", "supported", "给出廉颇（将领）等关系，未直接回答赵军统帅是谁、漏赵括；引用含「长平之战—将领→赵括」关系行"),
    "R02": ("incorrect", "unsupported", "未给出任何主帅信息；引用中周瑜仅作为江陵之战发起方出现，无孙刘联军主帅证据"),
    "R03": ("incorrect", "supported", "未给出晋军主帅；引用证据（谢玄遣部将刘牢之袭秦营）实际支撑「谢玄指挥晋军」，但未被用于回答"),
    "R04": ("incorrect", "unsupported", "未回答交战双方；引用为官渡之战地名关系与周边事件卡，无一条给出双方"),
    "R05": ("incorrect", "unsupported", "F02 朝代别名误判导致拒答（answerable=True）"),
    "R06": ("partial", "supported", "已列出楚汉战争、巨鹿之战、项羽击齐之战等（覆盖 gold 三项），但列表被离线摘要模式截断未列全"),
    "E01": ("incorrect", "unsupported", "F02 朝代别名误判导致拒答（answerable=True）"),
    "E02": ("partial", "supported", "给出「曹操同为两战统帅」这一相似点；未给出官渡以少胜多/奠定北方、赤壁奠定三国鼎立"),
    "T01": ("incorrect", "unsupported", "未给出长平之战纪年（前260年）；引用含白起攻韩上党之战卡（前263年），容易误读"),
    "T02": ("partial", "supported", "回答与引用含208年（曹操南征荆州之战卡 208年7月），但未直接给出赤壁之战自身纪年"),
    "T03": ("incorrect", "unsupported", "F02 朝代别名误判导致拒答（answerable=True）"),
    "B01": ("incorrect", "unsupported", "F02 朝代别名误判导致拒答（answerable=True）"),
    "B02": ("incorrect", "unsupported", "未给出影响；引用18条中仅4条与赤壁之战相关，无一条涉及「奠定三国鼎立」"),
    "B03": ("incorrect", "supported", "未回答「为什么」（急于求成/指挥失当/敌前退却均未出现）；引用含淝水之战卡，其原文含原因，导出时被截断"),
    "L01": ("incorrect", "supported", "仅地名关系＋白起攻韩上党之战卡，未给出经过与结果；引用含赵括被围原文"),
    "L02": ("incorrect", "unsupported", "未给出曹操战败原因；引用仅4条赤壁地名关系，无赤壁之战结果/原因证据"),
    "L03": ("partial", "supported", "给出「东晋获胜、前秦大败」（事件卡＋原文片段），但以少胜多的经过不完整"),
    "L04": ("partial", "supported", "给出白马斩颜良、荀或「以弱敌强/相持待变/出奇制胜」等取胜要素，未给出「以少胜多、奠定统一北方」结论"),
    "F01": ("incorrect", "unsupported", "filters-on 下引用仅5条（4条地名关系＋1条无关事件卡），未给出交战过程"),
    "F02": ("partial", "supported", "含淝水之战卡（结果句被截断为「东晋获胜，前…」），交战经过不完整"),
    "F03": ("partial", "supported", "给出城濮之战双方、晋文公统帅关系与晋攻曹/卫预备战役；未给出退避三舍、后发制人、一战定霸"),
    "F04": ("partial", "supported", "含涿鹿之战卡（约四五千年前、黄帝族/蚩尤族），结果句被截断"),
    "X01": ("incorrect", "unrelated", "应拒答却作答：问「度假」，知识库不含该类信息"),
    "X02": ("incorrect", "unrelated", "应拒答却作答：问「坦克」，时代不符的虚构问题"),
    "X03": ("incorrect", "unrelated", "应拒答却作答：问「邮箱」，知识库不存在该属性"),
}

scores["answer_correctness"] = [R[q][0] for q in scores["qid"]]
scores["citation_correctness"] = [R[q][1] for q in scores["qid"]]
scores["notes"] = [R[q][2] for q in scores["qid"]]
scores["reviewer"] = REVIEWER

# 硬性约束自检
assert list(scores["qid"]) == s_ids
assert list(scores.columns) == s_cols
assert set(scores["answer_correctness"]) <= {"correct", "partial", "incorrect", "unknown_answer"}
assert set(scores["citation_correctness"]) <= {"supported", "unrelated", "unsupported"}
for _, r in scores.iterrows():
    if r["answer_correctness"] != "correct" or r["citation_correctness"] != "supported":
        assert str(r["notes"]).strip(), f"{r['qid']} 缺 notes"
scores.to_csv(REVIEW / "scores_review_filled.csv", index=False, encoding="utf-8-sig")

print("bank rows:", len(bank), "通过:", (bank['reviewed'] == '通过').sum(), "留空:", (bank['reviewed'] == '').sum())
print("scores rows:", len(scores))
print(scores["answer_correctness"].value_counts().to_dict())
print(scores["citation_correctness"].value_counts().to_dict())
