/**
 * 生成 iCAN 参赛《应用方案》Word 文档。
 *
 * 设计依据：docx 技能 references/design-system.md 的 R1 封面配方 + WR-2 配色
 * （深绿 #2A4A3A + 暖金 #C89F62 + 米白 #F4F1E9，与作品本身「墨绿 + 赭金」的视觉
 * 识别一致，也贴合中国历史题材）。
 */
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, PageNumber, PageBreak,
  BorderStyle, WidthType, ShadingType, VerticalAlign, TableLayoutType,
  SectionType, NumberFormat, ImageRun, TableOfContents,
} = require("docx");

// ─────────────────────────── 配色与常量 ───────────────────────────
const P = {
  bg: "F4F1E9",          // 封面底色（暖米白）
  primary: "2A4A3A",     // 深墨绿：标题
  body: "1F2A24",        // 正文近黑
  secondary: "6E7268",   // 次级灰
  accent: "C89F62",      // 暖金：强调
  surface: "F0EDE5",     // 表格斑马纹
  line: "D5D0C8",        // 分隔线
};
const c = (hex) => hex.replace("#", "");
const SHOT = path.join(__dirname, "..", "screenshots");
const HEAD_FONT = { ascii: "Times New Roman", eastAsia: "SimHei" };
const BODY_FONT = { ascii: "Times New Roman", eastAsia: "SimSun" };

const NB = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const noBorders = { top: NB, bottom: NB, left: NB, right: NB };
const allNoBorders = { top: NB, bottom: NB, left: NB, right: NB, insideHorizontal: NB, insideVertical: NB };

// ─────────────────────── 封面标题排版工具（R1 配方要求） ───────────────────────
function splitTitleLines(title, charsPerLine) {
  if (title.length <= charsPerLine) return [title];
  const breakAfter = new Set([
    ..."，。、；：！？", ..."的与和及之在于为", ..."-_—–·/", ..." \t",
  ]);
  const lines = [];
  let remaining = title;
  while (remaining.length > charsPerLine) {
    let breakAt = -1;
    for (let i = charsPerLine; i >= Math.floor(charsPerLine * 0.6); i--) {
      if (i < remaining.length && breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
    }
    if (breakAt === -1) {
      const limit = Math.min(remaining.length, Math.ceil(charsPerLine * 1.3));
      for (let i = charsPerLine + 1; i < limit; i++) {
        if (breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
      }
    }
    if (breakAt === -1) {
      breakAt = charsPerLine;
      const prev = remaining[breakAt - 1], next = remaining[breakAt];
      if (prev && next && !breakAfter.has(prev) && !breakAfter.has(next) &&
          /[\u4e00-\u9fff]/.test(prev) && /[\u4e00-\u9fff]/.test(next)) {
        breakAt -= 1;
      }
    }
    lines.push(remaining.slice(0, breakAt).trim());
    remaining = remaining.slice(breakAt).trim();
  }
  if (remaining) lines.push(remaining);
  if (lines.length > 1 && lines[lines.length - 1].length <= 2) {
    const last = lines.pop();
    lines[lines.length - 1] += last;
  }
  return lines;
}

function calcTitleLayout(title, maxWidthTwips, preferredPt = 40, minPt = 24) {
  const charsPerLine = (pt) => Math.floor(maxWidthTwips / (pt * 20));
  let titlePt = preferredPt, lines;
  while (titlePt >= minPt) {
    const cpl = charsPerLine(titlePt);
    if (cpl < 2) { titlePt -= 2; continue; }
    lines = splitTitleLines(title, cpl);
    if (lines.length <= 3) break;
    titlePt -= 2;
  }
  if (!lines || lines.length > 3) { titlePt = minPt; lines = splitTitleLines(title, charsPerLine(minPt)); }
  return { titlePt, titleLines: lines };
}

function calcCoverSpacing(params) {
  const {
    titleLineCount = 1, titlePt = 36, hasSubtitle = false,
    hasEnglishLabel = false, metaLineCount = 0, fixedHeight = 800,
    pageHeight = 16838, marginTop = 0, marginBottom = 0,
  } = params;
  const SAFETY = 1200;
  const usableHeight = pageHeight - marginTop - marginBottom - SAFETY;
  const titleHeight = titleLineCount * (titlePt * 23 + 200);
  const subtitleHeight = hasSubtitle ? (12 * 23 + 600) : 0;
  const englishLabelHeight = hasEnglishLabel ? (9 * 23 + 600) : 0;
  const metaHeight = metaLineCount * (10 * 23 + 100);
  const implicitParaHeight = 3 * 300;
  const contentHeight = titleHeight + subtitleHeight + englishLabelHeight + metaHeight + fixedHeight + implicitParaHeight;
  const safeRemaining = Math.max(usableHeight - contentHeight, 400);
  const FOOTER_MIN = 800;
  const rawTop = Math.floor(safeRemaining * 0.45);
  const rawBottom = Math.floor(safeRemaining * 0.45);
  const bottomSpacing = Math.max(rawBottom, FOOTER_MIN);
  const topSpacing = Math.max(rawTop - Math.max(0, FOOTER_MIN - rawBottom), 400);
  const midSpacing = Math.max(safeRemaining - topSpacing - bottomSpacing, 0);
  return { topSpacing, midSpacing, bottomSpacing };
}

// ─────────────────────────── 封面（R1 纯段落左对齐） ───────────────────────────
function buildCover(cfg) {
  const padL = 1200, padR = 800;
  const { titlePt, titleLines } = calcTitleLayout(cfg.title, 11906 - padL - padR - 300, 40, 26);
  const titleSize = titlePt * 2;
  const spacing = calcCoverSpacing({
    titleLineCount: titleLines.length, titlePt,
    hasSubtitle: !!cfg.subtitle, hasEnglishLabel: !!cfg.englishLabel,
    metaLineCount: cfg.metaLines.length, fixedHeight: 400,
  });

  const accentLeft = { style: BorderStyle.SINGLE, size: 8, color: c(P.accent), space: 12 };
  const children = [];

  children.push(new Paragraph({ spacing: { before: spacing.topSpacing } }));

  children.push(new Paragraph({
    indent: { left: padL, right: padR }, spacing: { after: 500 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: c(P.accent), space: 8 } },
    children: [new TextRun({
      text: cfg.englishLabel.split("").join("  "),
      size: 18, color: c(P.accent), font: { ascii: "Calibri", eastAsia: "SimHei" }, characterSpacing: 24,
    })],
  }));

  titleLines.forEach((line, i) => {
    children.push(new Paragraph({
      indent: { left: padL },
      spacing: { after: i < titleLines.length - 1 ? 100 : 300, line: Math.ceil(titlePt * 23), lineRule: "atLeast" },
      children: [new TextRun({ text: line, size: titleSize, bold: true, color: c(P.primary), font: { eastAsia: "SimHei", ascii: "Arial" } })],
    }));
  });

  children.push(new Paragraph({
    indent: { left: padL }, spacing: { after: 800 },
    children: [new TextRun({ text: cfg.subtitle, size: 24, color: c(P.secondary), font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
  }));

  for (const line of cfg.metaLines) {
    children.push(new Paragraph({
      indent: { left: padL + 200 }, spacing: { after: 80 },
      border: { left: accentLeft },
      children: [new TextRun({ text: line, size: 24, color: c(P.secondary), font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
    }));
  }

  children.push(new Paragraph({ spacing: { before: spacing.bottomSpacing } }));

  children.push(new Paragraph({
    indent: { left: padL, right: padR },
    border: { top: { style: BorderStyle.SINGLE, size: 2, color: c(P.accent), space: 8 } },
    spacing: { before: 200 },
    children: [
      new TextRun({ text: "2026 年 iCAN 大学生创新创业大赛 · AI 应用创新挑战赛", size: 16, color: "909090", font: { ascii: "Arial", eastAsia: "Microsoft YaHei" } }),
      new TextRun({ text: "            " }),
      new TextRun({ text: "软件赛道", size: 16, color: "909090", font: { ascii: "Arial", eastAsia: "Microsoft YaHei" } }),
    ],
  }));

  return [new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    borders: allNoBorders,
    rows: [new TableRow({
      height: { value: 16838, rule: "exact" },
      children: [new TableCell({
        shading: { type: ShadingType.CLEAR, fill: c(P.bg) }, borders: noBorders,
        verticalAlign: VerticalAlign.TOP,
        children,
      })],
    })],
  })];
}

// ─────────────────────────── 组件构造器 ───────────────────────────
function h1(text, opts = {}) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    keepNext: true,
    spacing: { before: 240, after: 120, line: 312 },
    children: [new TextRun({ text, bold: true, color: c(P.primary), size: 32, font: HEAD_FONT })],
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    keepNext: true,
    spacing: { before: 170, after: 80, line: 312 },
    children: [new TextRun({ text, bold: true, color: c(P.primary), size: 28, font: HEAD_FONT })],
  });
}
function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    keepNext: true,
    spacing: { before: 130, after: 60, line: 312 },
    children: [new TextRun({ text, bold: true, color: c(P.body), size: 24, font: HEAD_FONT })],
  });
}
function p(text, opts = {}) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: opts.noIndent ? undefined : { firstLine: 480 },
    spacing: { line: 312, after: opts.after === undefined ? 24 : opts.after },
    children: [new TextRun({ text, size: 24, color: c(P.body), font: BODY_FONT })],
  });
}
/** 带加粗前缀的正文段（用于「标签：说明」式条目） */
function pl(label, text) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { firstLine: 480 },
    spacing: { line: 312, after: 24 },
    children: [
      new TextRun({ text: label, size: 24, bold: true, color: c(P.primary), font: BODY_FONT }),
      new TextRun({ text, size: 24, color: c(P.body), font: BODY_FONT }),
    ],
  });
}
function bullet(text) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { left: 480, hanging: 240 },
    spacing: { line: 312, after: 30 },
    children: [new TextRun({ text: "· " + text, size: 24, color: c(P.body), font: BODY_FONT })],
  });
}
function caption(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 10, after: 90, line: 312 },
    children: [new TextRun({ text, size: 21, color: c(P.secondary), font: BODY_FONT })],
  });
}
function tblCaption(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 80, after: 20, line: 312 },
    keepNext: true,
    children: [new TextRun({ text, size: 21, bold: true, color: c(P.body), font: BODY_FONT })],
  });
}

const cellMargins = { top: 18, bottom: 18, left: 105, right: 105 };

function headerRow(cells, widths, keepNext) {
  return new TableRow({
    tableHeader: true, cantSplit: true,
    children: cells.map((text, i) => new TableCell({
      width: { size: widths[i], type: WidthType.PERCENTAGE },
      shading: { type: ShadingType.CLEAR, fill: c(P.primary) },
      margins: cellMargins,
      verticalAlign: VerticalAlign.CENTER,
      children: [new Paragraph({
        alignment: AlignmentType.CENTER, spacing: { line: 312 }, keepNext: !!keepNext,
        children: [new TextRun({ text, size: 21, bold: true, color: "FFFFFF", font: BODY_FONT })],
      })],
    })),
  });
}

function dataRow(cells, widths, index, keepNext) {
  return new TableRow({
    cantSplit: true,
    children: cells.map((text, i) => new TableCell({
      width: { size: widths[i], type: WidthType.PERCENTAGE },
      shading: index % 2 === 0
        ? { type: ShadingType.CLEAR, fill: c(P.surface) }
        : { type: ShadingType.CLEAR, fill: "FFFFFF" },
      margins: cellMargins,
      children: [new Paragraph({
        alignment: i === 0 ? AlignmentType.LEFT : AlignmentType.LEFT,
        spacing: { line: 312 }, keepNext: !!keepNext,
        children: [new TextRun({ text, size: 21, color: c(P.body), font: BODY_FONT })],
      })],
    })),
  });
}

/** 通用数据表：header 为表头数组，rows 为二维数组 */
function table(header, rows, widths, noSplit) {
  const w = widths || header.map(() => Math.round(100 / header.length));
  // noSplit=true：除最后一行外全部 keepNext，使整张小表不被分页切开
  const keep = (i) => noSplit && i < rows.length - 1;
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    borders: {
      top: { style: BorderStyle.SINGLE, size: 4, color: c(P.accent) },
      bottom: { style: BorderStyle.SINGLE, size: 4, color: c(P.accent) },
      left: NB, right: NB,
      insideHorizontal: { style: BorderStyle.SINGLE, size: 1, color: c(P.line) },
      insideVertical: NB,
    },
    rows: [headerRow(header, w, !!noSplit), ...rows.map((r, i) => dataRow(r, w, i, keep(i)))],
  });
}

// ─────────────────────────── 图片 ───────────────────────────
function pngSize(file) {
  const buf = fs.readFileSync(file);
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}
/** 按目标宽度（px，96dpi）等比缩放插图；maxHeightPx>0 时以高度封顶（竖版截图用） */
function figure(file, cap, targetWidthPx = 540, maxHeightPx = 0) {
  const { width, height } = pngSize(file);
  let w = targetWidthPx;
  let h = Math.round((height / width) * w);
  if (maxHeightPx > 0 && h > maxHeightPx) {
    h = maxHeightPx;
    w = Math.round((width / height) * h);
  }
  return [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      // 插图段落用单倍行距：默认继承的 1.3 倍会按图片高度再乘 1.3，白白多预留三成高度
      spacing: { before: 70, after: 10, line: 240, lineRule: "auto" },
      children: [new ImageRun({
        type: "png",
        data: fs.readFileSync(file),
        transformation: { width: w, height: h },
      })],
    }),
    caption(cap),
  ];
}

// ─────────────────────────── 正文内容 ───────────────────────────
const body = [];
const add = (...items) => items.forEach((i) => body.push(i));

// ══════ 一、项目背景与痛点分析 ══════
add(
  h1("一、项目背景与痛点分析"),
  h2("1.1 场景背景"),
  p("中国历代战争史是理解中华文明演进的关键线索。战争是朝代更替最直接的推动力，也集中体现了政治制度、经济基础、地理条件与军事技术的综合作用。以《中国历代战争简史》这一类通史文献为例，它把上起上古、下至近代的一千余场战争压缩进三十余万字的线性叙述中，信息密度极高，却只提供一种阅读维度——从头读到尾。"),
  p("与此同时，人工智能已具备从这类非结构化史籍中抽取结构化知识并提供自然语言问答的能力。本项目要回答的问题因此是：能否把一部线性史书，转化为可检索、可推理、可溯源的知识服务系统。"),

  h2("1.2 现存痛点"),
  pl("痛点一：史料线性组织，关系知识隐式存在。", "一场战役的起因、经过、结果、参战势力、主将、发生地点分散在不同章节；而「战役 A 与战役 B 究竟是顺承关系还是因果关系」这类知识，原文里根本没有显式表述，需要读者自行推断。要在几十万字里横向串联十条线索，人工成本极高。"),
  pl("痛点二：通用大模型存在史实幻觉，且不可溯源。", "直接向通用大模型提问「淝水之战的晋军主帅是谁」，答案可能似是而非，且无法给出可核验的出处。在历史教学、学术研究这类对准确性有硬要求的场景中，不可溯源的答案是负价值——它比「不知道」更危险。"),
  pl("痛点三：知识图谱与问答系统相互割裂。", "知识图谱擅长结构化关系展示，但交互门槛高，用户只能「看」不能「问」；问答系统交互自然，但用户「问」出来的答案看不到依据。两者互不相通，用户要么看得见关系却问不出来，要么问得出答案却无从验证。"),
  pl("痛点四：数据质量缺乏工程化保障。", "历史数据天然存在实体别名（古今地名、人物异名）、时间异常（如「结束时间早于开始时间」）、孤立节点等问题。这些缺陷靠人工肉眼几乎无法发现，却会直接污染问答结果的正确性。"),

  h2("1.3 项目定位"),
  p("「干戈纪略」是一套以中国历代战争史为对象的知识图谱与智能问答系统。它不做史书的电子化搬运，而是完成三件事：把史籍中的隐性关系显性化为可计算的知识图谱；用「图谱 + 文本」双通道检索增强生成，让每一次回答都带可点开的引用证据；用工程化门禁把数据质量与系统安全钉在可验收的标准上。")
);

// ══════ 二、需求分析与目标用户 ══════
add(
  h1("二、需求分析与目标用户"),
  h2("2.1 目标用户群体"),
  p("项目面向五类用户，各自的核心诉求差异明显，这也是系统同时保留「图谱漫游」与「自然语言问答」两条入口的原因："),
  tblCaption("表 2-1　目标用户与核心需求"),
  table(
    ["用户群体", "典型场景", "核心需求"],
    [
      ["历史专业学生与研究者", "论文选题、史料检索", "快速定位战役要素，引用可溯源"],
      ["高校与中学历史教师", "备课、课堂演示", "时间轴与地图可视化，史实准确"],
      ["历史爱好者", "自主探索、跨朝代比较", "自然语言提问，图谱漫游"],
      ["内容创作者", "自媒体、出版素材整理", "结构化导出，关系脉络梳理"],
      ["文博与文旅机构", "展陈、导览内容建设", "地图点位、知识卡片"],
    ],
    [24, 28, 48]
  ),

  h2("2.2 功能需求"),
  p("围绕上述用户群体，系统拆解出八项功能需求（FR）。其中 FR3、FR4、FR5 是 AI 能力的主战场，也是本项目的差异化所在："),
  tblCaption("表 2-2　功能需求清单"),
  table(
    ["编号", "功能需求", "说明"],
    [
      ["FR1", "知识图谱可视化", "按事件-事件、事件-组织、事件-人物、事件-地点四个维度展示关系网络"],
      ["FR2", "实体与关系管理", "四类实体的增删改查与属性编辑，写库同时同步图谱"],
      ["FR3", "智能问答", "图谱与文本双通道检索，回答带引用编号，可点开查看依据"],
      ["FR4", "知识面板", "实体卡、图谱子图、时间线、地图四类联动视图"],
      ["FR5", "文本实体识别", "用户粘贴任意战争史文本，自动抽取实体、事件与关系"],
      ["FR6", "时间轴与地图", "按朝代浏览战争进程，按经纬度定位战争地点与行军路线"],
      ["FR7", "数据运营与质检", "数据集版本、图谱质检工作台、修复队列"],
      ["FR8", "IM 机器人入口", "在飞书内直接问答，无需打开浏览器"],
    ],
    [12, 26, 62]
  ),

  h2("2.3 非功能性需求"),
  tblCaption("表 2-3　非功能性需求与验收口径"),
  table(
    ["维度", "要求", "验收口径"],
    [
      ["性能", "单次问答端到端响应可接受", "整链经公网 nginx 实测 1.8 秒"],
      ["可溯源", "所有回答必须可核验", "回答内嵌引用编号，与证据条目一一对应"],
      ["准确性", "不确定时宁可拒答", "无证据时触发拒答，不生成无依据内容"],
      ["安全", "鉴权与凭证可撤销", "无令牌 401、无权限 403，停用账号后旧令牌失效"],
      ["可复现", "数据与结果可复算", "快照、索引、发布制品全链哈希可校验"],
      ["可维护", "改动不回归", "四模块共 1351 条自动化用例，CI 十个检查作业"],
    ],
    [16, 36, 48]
  )
);

// ══════ 三、开发工具与技术选型 ══════
add(
  h1("三、开发工具与技术选型"),
  h2("3.1 开发工具"),
  tblCaption("表 3-1　开发工具链"),
  table(
    ["类别", "工具", "用途"],
    [
      ["代码编辑", "Visual Studio Code、PyCharm", "日常开发与调试"],
      ["版本控制", "Git、GitHub", "源码管理、协作与代码审查"],
      ["后端测试", "pytest", "四个 Python 模块的自动化用例"],
      ["前端测试", "Vitest、Playwright", "单元/组件测试与浏览器端到端测试"],
      ["静态检查", "Ruff、TypeScript 类型检查", "代码规范与类型安全"],
      ["持续集成", "GitHub Actions", "十个作业覆盖四个模块与部署配置"],
      ["部署运维", "nginx、systemd、Shell 脚本", "生产环境反向代理与服务托管"],
      ["浏览器自动化", "Playwright", "界面回归与冒烟测试"],
    ],
    [18, 32, 50]
  ),

  h2("3.2 技术栈"),
  tblCaption("表 3-2　技术栈与选型说明"),
  table(
    ["层次", "技术", "选型说明"],
    [
      ["统一语言", "Python 3.11、TypeScript", "四个模块统一同一 Python 版本，减少环境漂移"],
      ["后端框架", "Flask、FastAPI", "知识库服务与问答服务各自独立部署，故障不互相传导"],
      ["关系存储", "SQLite（WAL 模式）", "作为主数据库，负责增删改查与事务一致性"],
      ["图存储", "Neo4j 5.x", "作为派生副本，承担图谱可视化与多跳查询"],
      ["向量检索", "Chroma + text-embedding-v4", "1024 维中文向量，支撑语义召回"],
      ["关键词检索", "SQLite FTS5 + BM25", "与向量通道融合，向量不可用时自动降级"],
      ["大模型", "在线大模型 + 本地 Ollama", "在线模型负责生成，本地模型负责离线兜底"],
      ["前端", "Vue 3 + TypeScript + layui-vue", "管理台与知识图谱界面"],
      ["可视化", "ECharts + 地图组件", "关系网络图、朝代分布、地图点位"],
      ["地理编码", "高德地理编码服务", "古今地名到经纬度的转换"],
    ],
    [16, 30, 54]
  ),

  h2("3.3 关键选型理由"),
  pl("为什么是「图谱 + 文本」而不是单纯向量检索。", "向量检索擅长语义相近，但对「官渡之战的交战双方是谁」这类需要精确事实的问题，它只能给出语义上相关的段落，无法保证答案就在其中；知识图谱恰好相反，它对结构化关系的查询是精确的。两者互补，因此本项目让两条通道并行检索再融合。"),
  pl("为什么把 SQLite 与 Neo4j 分成主从。", "关键判断是：图谱数据可以从主库完整重建，反之则不行。这个判断决定了同步策略——宁可让图谱短暂落后，也要保证主库写入的原子性。")
);

// ══════ 四、技术方案与系统实现 ══════
add(
  h1("四、技术方案与系统实现"),
  h2("4.1 总体架构"),
  p("系统由四个可独立部署的部分组成。浏览器只访问统一入口，其余服务均不直接对外暴露："),
  tblCaption("表 4-1　系统组成与运行时"),
  table(
    ["组成部分", "职责", "运行时"],
    [
      ["知识库系统", "图谱可视化、节点关系管理、数据运营、旧版问答", "Python 3.11 + Node"],
      ["RAG 问答系统", "图谱与文本双通道检索增强问答，自带前端", "Python 3.11"],
      ["知识抽取流水线", "从战争史文献离线抽取实体、事件与关系", "Python 3.11（离线）"],
      ["飞书机器人", "把问答能力接入 IM，长连接无公网回调", "Python 3.11 + Node"],
    ],
    [22, 56, 22]
  ),
  pl("数据流向。", "原书文本经知识抽取流水线产出结构化结果，导入 SQLite 主库，再同步至 Neo4j 图谱；RAG 问答系统不直接连这两个库，而是读取由主库导出的治理快照与检索索引。这条单向依赖保证了问答侧的数据永远来自一次可复现的发布，而不是运行中的库。"),

  h2("4.2 数据层：双数据库与事务型 outbox"),
  p("节点的新增、修改、删除会同时影响 SQLite 与 Neo4j，这是一次典型的跨存储双写。系统没有采用「先写库、再调图谱、失败就报错」的朴素做法，而是引入事务型 outbox："),
  bullet("业务数据变更与图谱同步待办在同一次 SQLite 提交中落库，要么都成功，要么都不存在；"),
  bullet("每个节点在图谱上有稳定键（节点类型 + 主键），避免按名称合并导致同名实体被误合并；"),
  bullet("同步失败按 30 秒、2 分钟、10 分钟、1 小时退避重试，超过上限标记为放弃并留痕；"),
  bullet("定时器每分钟扫描并重放待办，因此临时故障不会导致图谱长期不一致。"),
  p("在对账口径上，系统按去重后的实体名计数，而不是按行数——早期按行数对比时，同名实体场景下必然误报数千条不一致，这个教训被固化进了用例。"),

  h2("4.3 检索层：图谱与文本双通道"),
  p("这是系统的核心。用户提问后，两套检索引擎并行工作，再由融合层统一裁决："),
  pl("图谱通道。", "把问题中的实体识别出来后，在图谱上做多跳路径检索与最短路径分析，取出与之相关的事件、人物、组织与地点关系，并按相关性打分保留前四条路径。这使得系统能回答「A 与 B 是什么关系」这类需要沿边行走的问题。"),
  pl("文本通道。", "同时走两条路：关键词侧用全文索引做布尔检索与 BM25 排序，语义侧用向量检索做相似度召回；两者用倒数排序融合（RRF）算法合并成一个排序列表。当向量服务不可用时，系统自动降级为纯关键词检索，并在健康检查中如实反映降级状态，而不是悄悄给出更差的结果。"),
  pl("融合与去重。", "两条通道产出的证据被归一为统一结构，去重后分配引用编号。图谱证据会注明它来自哪条边（例如「涿鹿之战—主帅—黄帝」），文本证据会注明来自哪个文档的哪一段。"),

  h2("4.4 生成层：三层防幻觉机制"),
  p("这是本项目认为最值得强调的设计。三层机制不可拆开单独使用："),
  tblCaption("表 4-2　三层防幻觉机制"),
  table(
    ["层次", "机制", "作用"],
    [
      ["第一层", "无证据则拒答", "检索阶段没有拿到任何证据时直接拒答，不允许模型凭记忆作答"],
      ["第二层", "证据带编号", "所有证据在进入提示词前分配引用编号，模型只能引用这些编号"],
      ["第三层", "答案校验", "生成结果中的引用编号必须落在已分配范围内，否则视为不可信"],
    ],
    [14, 24, 62]
  ),
  p("在实际运行中，这套机制还会主动暴露分歧。例如在「涿鹿之战」的回答里，系统发现事件卡把黄帝记为「发起方」、蚩尤记为「防守方」，而同一来源的原文叙述却是蚩尤族「乘势北进涿鹿」、攻击黄帝族。系统没有选择其一，而是把两种表述并列呈现并注明角度不同——这种「承认史料内部张力」的行为，正是可溯源设计带来的直接收益。"),

  h2("4.5 知识获取：三阶段抽取流水线"),
  p("系统的知识不是人工录入的，而是由离线流水线从战争史文献中自动抽取。流水线分三个阶段递进："),
  bullet("实体抽取：识别人物、地点、组织，并处理古今地名映射（同一地点的历史名与现代名归一）；"),
  bullet("事件抽取：判定事件类型、拆分子事件、补全起因经过结果等要素；"),
  bullet("关系抽取：产出事件-事件、事件-组织、事件-人物、事件-地点四类关系三元组。"),
  p("工程上，长文本按约 1800 字切段、段间重叠 200 字以避免边界信息丢失；每段结果带缓存，缓存键包含文本指纹与提示词版本，其中提示词版本由提示词源码哈希机械派生——改一个字符即全量失效，不依赖人工记得升级版本号。代价是：只要改动提示词，既有分段缓存就会全部失效，下一次抽取必须重新调用大模型——这正是该机制的设计意图，让版本失效不依赖人记得手动升级。"),

  h2("4.6 推理增强：规则引擎与推理边固化"),
  p("史书不会明确写出「A 战役是 B 战役的起因」，但这类关系对用户的横向理解极为关键。系统用规则引擎补齐这一层："),
  bullet("规则库共 20 条，其中 17 条是反向关系规则——图谱中边的方向是「事件指向地点或人物」，而用户提问的方向常常相反，需要显式反向推理；"),
  bullet("另有 3 条复合规则，支撑两跳因果关系、两跳顺承关系与三跳包含关系；"),
  bullet("规则推理的产物（共 11833 条推理边）在离线阶段固化进数据快照，并带溯源标记，可被检索、引用与提示词使用；"),
  bullet("固化过程可重复：同一份输入重复构建，产物的哈希值完全一致。"),
  p("把推理放在离线固化而不是在线计算，是出于两点考虑：在线推理会显著拉高单次问答时延；而离线固化后的推理边可以被引用编号指向，用户能看到「这条关系是推理得出的」，而不是把它当作史实。"),

  h2("4.7 工程质量与安全基线"),
  p("一个面向真实用户的系统，可靠性来自可被验证的约束，而不是来自「应该没问题」。本项目建立了如下机制："),
  tblCaption("表 4-3　工程质量与安全机制"),
  table(
    ["方面", "具体做法"],
    [
      ["自动化测试", "四个模块共 1351 条用例，覆盖后端接口、前端组件、抽取算法与机器人流程"],
      ["持续集成", "十个 CI 作业，含密钥扫描、部署配置校验、文档数字一致性核对"],
      ["鉴权体系", "三级角色（管理员 / 编辑 / 访客），前端菜单与后端接口使用同一套权限口径"],
      ["凭证撤销", "改密码、停用、删号会同时使旧登录令牌立即失效，而非等待过期"],
      ["登录防护", "同一来源地址与同一账号两把尺子限流，计数在单条数据库语句内原子完成"],
      ["图谱查询安全", "所有图查询语句参数化，节点类型经白名单校验，杜绝注入"],
      ["数据发布可复现", "快照、索引、评测、发布各环节产物均带哈希，构成完整溯源链"],
    ],
    [20, 80]
  )
);

// ══════ 五、作品功能说明 ══════
add(
  h1("五、作品功能说明"),
  p("本章逐一说明作品的八项功能，其中 5.3 至 5.5 是 AI 能力集中体现的部分。"),

  h2("5.1 首页仪表盘"),
  p("仪表盘呈现系统的整体数据态势：实体总量、关系总量、孤立节点、时间异常，以及按朝代排列的事件分布。其中最右侧的「质检快照」把数据质量问题直接前置到首页——让使用者第一眼就知道当前数据的可信程度。"),
  ...figure(path.join(SHOT, "02-首页仪表盘.png"), "图 5-1　首页仪表盘：数据总量、朝代分布与质检快照"),

  h2("5.2 战争关系图"),
  p("这是知识图谱的核心可视化界面。系统按四个维度组织图谱：关联战争（事件-事件）、参战势力（事件-组织）、相关人物（事件-人物）、发生地点（事件-地点）。四个页面由同一份实现按维度参数化渲染，因此新增一个维度只需增加一份配置。"),
  p("下图展示的是事件-事件维度：节点是战争事件，边上的标签是关系类型（顺承关系、因果关系、并列关系）。用户可以从任意一场战役出发，沿边展开它的前因后果，这正是线性史书无法提供的阅读方式。"),
  ...figure(path.join(SHOT, "05-战争关系图.png"), "图 5-2　战争关系图：事件-事件关系网络与关系类型标注"),

  h2("5.3 RAG 智能问答（AI 核心）"),
  p("这是本作品的核心功能，也是 AI 技术发挥作用最集中的地方。用户以自然语言提问，系统返回一段结构化回答，回答中每个事实点后面都跟着方括号引用编号；右侧知识面板同步列出全部证据条目。"),
  ...figure(path.join(SHOT, "06-RAG问答-回答与引用证据.png"), "图 5-3　RAG 智能问答：回答内嵌引用编号，右侧列出十条可展开的证据"),
  p("以提问「介绍一下涿鹿之战」为例，可以观察到 AI 在这条链路中承担的完整工作："),
  bullet("理解问题类型：系统把问题划分为实体介绍、关系型、背景型、对比型、时间线型等类别，并据此调整提示词策略；"),
  bullet("双通道检索：图谱通道取出「涿鹿之战—主帅—黄帝」「涿鹿之战—主帅—蚩尤」「涿鹿之战—参战方—黄帝族」等关系边；文本通道从原文中召回战争经过的叙述段落；"),
  bullet("证据融合与编号：两条通道的证据合并去重后，分配 [1] 至 [10] 共十条引用；"),
  bullet("组织回答：按交战双方、起因与经过、结果与影响分段输出，并在发现史料表述冲突时主动并列呈现；"),
  bullet("标注出处：每条引用注明类型（图谱边 / 事件卡 / 关系证据 / 原文），点击即可展开核对。"),
  p("回答下方的引用条目区进一步区分了证据来源，例如 [1] 至 [5] 标注为「图谱」，指向具体的三元组；[6] 标注为「事件卡」，指向结构化的事件条目；[7] 与 [9] 标注为「关系证据」；[10] 标注为「原文」，指向《中国历代战争简史》的原文段落。这种分层引用让用户能清楚地区分「这是从关系网络推出的事实」与「这是原文里的直接叙述」。"),

  h2("5.4 知识面板：四类联动视图"),
  p("问答不止于文字。右侧知识面板提供四类视图，与当前回答实时联动："),
  tblCaption("表 5-1　知识面板的四类视图"),
  table(
    ["视图", "内容", "价值"],
    [
      ["引用证据", "本次回答引用的全部证据条目，可逐条展开", "答案可核验"],
      ["实体卡", "回答涉及实体的结构化档案", "从答案延伸到实体"],
      ["图谱子图", "以问题实体为中心的关系子图，可点击节点继续提问", "关系可视化"],
      ["时间线 / 地点", "相关事件的时间序列与地理分布", "时空维度补充"],
    ],
    [20, 46, 34]
  ),
  p("下图是「图谱子图」视图：系统以「涿鹿之战」为中心实时渲染出黄帝、蚩尤、九黎、三苗、阪泉之战等关联节点，边上标签标明关系类型；下方列出可继续追问的实体，点击即可把对话推进到下一个实体，构成「问答驱动图谱漫游」的闭环。"),
  ...figure(path.join(SHOT, "07-知识面板-图谱子图.png"), "图 5-4　知识面板的图谱子图视图：以问题实体为中心的关系网络", 540, 480),

  h2("5.5 文本实体识别（AI 能力）"),
  p("该功能允许用户粘贴任意一段战争史文本，系统自动抽取其中的实体、事件与关系，并结构化展示。它把知识抽取流水线从离线批处理变成了一次可交互的即席调用，也展示了系统「从非结构化文本到结构化知识」的完整能力。"),
  ...figure(path.join(SHOT, "08-文本实体识别.png"), "图 5-5　文本实体识别：粘贴史料文本，由大模型抽取实体与事件"),

  h2("5.6 历史时间轴与地图视图"),
  p("时间轴按朝代序列呈现战争进程，支持按事件名称、朝代、参与人物或组织、事件类型多维筛选，并可直接筛出时间异常的事件。地图视图则按真实经纬度展示战争地点、事件与行军路线，统计可定位事件、单点事件与可视路线数量，并对缺失坐标与低置信坐标的地点单独计数。"),
  ...figure(path.join(SHOT, "04-历史地图视图.png"), "图 5-6　历史地图视图：按经纬度落点展示战争地点、事件与路线"),

  h2("5.7 数据运营与图谱质检"),
  p("数据运营模块把「数据可信」这件事变成可操作的流程。质检工作台汇总重复节点、孤立节点、缺失字段、时间异常、坐标缺失与坐标低置信六类问题，并生成修复队列，按优先级排列待修复项。其中「同步不一致」一项专门监控 SQLite 主库与 Neo4j 图谱的对账结果。"),
  ...figure(path.join(SHOT, "09-图谱质检.png"), "图 5-7　图谱质检工作台：六类质量问题与修复队列"),
  h2("5.8 飞书知识问答机器人"),
  p("除浏览器入口外，系统还把问答能力接入了飞书。用户可以在群聊或私聊中直接提问，机器人以卡片形式返回回答，并附带关系子图。技术上采用长连接方式，无需公网回调地址与 IP 白名单，因此部署成本极低。该入口为可选组件，不启动它不影响其他功能。"),

  h2("5.9 功能与赛题评审维度对照"),
  tblCaption("表 5-2　作品功能对评审维度的支撑"),
  table(
    ["评审维度", "本作品的对应支撑"],
    [
      ["创新性", "双通道检索融合、三层防幻觉、规则推理边离线固化、推理结果可引用"],
      ["技术实现", "双数据库主从架构、事务型 outbox、三阶段抽取流水线、1351 条自动化用例"],
      ["实用价值", "直接服务历史教学与研究场景，解决通用大模型不可溯源的硬伤"],
      ["用户体验", "问答与图谱漫游闭环，引用可点开核验，四类知识面板联动"],
      ["展示效果", "图谱、时间轴、地图、质检四类可视化，界面风格统一"],
    ],
    [22, 78]
  )
);

// ══════ 六、使用说明 ══════
add(
  h1("六、使用说明"),
  h2("6.1 运行环境"),
  tblCaption("表 6-1　运行环境要求"),
  table(
    ["组件", "要求", "说明"],
    [
      ["Python", "3.11", "四个模块统一版本"],
      ["Node.js", "≥ 18", "前端构建与机器人子图出图"],
      ["Neo4j", "5.x（需 JDK 17 或更高）", "图谱可视化与图谱问答"],
      ["数据库", "SQLite（内置）", "主数据存储，无需额外安装"],
      ["大模型", "在线 API 或本地 Ollama", "在线模型用于生成，本地模型可作离线兜底"],
    ],
    [18, 30, 52]
  ),

  h2("6.2 在线演示与访问方式"),
  p("作品已完成生产环境部署，可直接通过浏览器访问演示。访问路径与登录方式如下："),
  tblCaption("表 6-2　演示环境访问路径"),
  table(
    ["入口", "访问方式", "说明"],
    [
      ["管理台首页", "http://47.117.100.163/", "登录后进入首页仪表盘"],
      ["RAG 智能问答", "菜单「知识图谱 → RAG 智能问答」", "核心 AI 问答入口，独立服务提供"],
      ["飞书机器人", "飞书内搜索机器人并直接提问", "可选入口，长连接无需公网回调"],
    ],
    [22, 46, 32]
  ),
  new Paragraph({
    alignment: AlignmentType.JUSTIFIED, indent: { firstLine: 480 }, keepNext: true,
    spacing: { line: 312, after: 24 },
    children: [new TextRun({ text: "演示环境提供以下两个账号，均已实测可直接登录使用：", size: 24, color: c(P.body), font: BODY_FONT })],
  }),
  tblCaption("表 6-3　演示账号"),
  table(
    ["账号", "口令", "角色", "可用功能"],
    [
      ["123456", "123456", "编辑者", "浏览图谱、使用智能问答，并可维护节点数据"],
      ["abcd", "123456", "普通用户", "浏览图谱、使用智能问答"],
    ],
    [15, 15, 18, 52],
    true
  ),
  p("需要说明的是，演示环境已关闭自助注册：系统不再开放匿名建号入口，新账号只能由管理员在后台开通。这样做的原因是公网环境下任何人都能建号，意味着任何人都能拿到访客身份调用接口、消耗模型配额。因此请直接使用上表提供的账号登录，无需也无法自行注册。"),
  p("为保证演示环境稳定，问答接口对同一来源地址设有访问频率限制；若短时间连续提问过多，请稍候重试。"),

  h2("6.3 操作流程"),
  pl("第一步：登录。", "打开系统地址，输入账号与密码登录。系统按角色分配可见菜单——访客只能浏览图谱与问答，编辑可维护节点数据，管理员额外拥有用户管理权限。"),
  pl("第二步：进入问答。", "在左侧菜单选择「知识图谱 → RAG 智能问答」。页面顶部会显示服务状态与当前数据版本，确认服务在线后即可提问。"),
  pl("第三步：提问。", "在底部输入框输入问题后回车（或点击发送）。若一时没有思路，可直接点击页面提供的示例题——示例按问题类型分组，涵盖实体介绍、关系型、背景型、对比型、时间线型五类。"),
  pl("第四步：查看引用。", "回答生成后，正文中的方括号数字即为引用编号。把鼠标移到编号上可看到证据摘要，右侧知识面板的「引用证据」页签列出全部条目，点击可逐条展开核对。"),
  pl("第五步：沿图谱漫游。", "切换到「图谱子图」页签，可以看到以问题实体为中心的关系网络。点击图中任意节点，或点击图下方的实体标签，即可就这个新实体继续提问。"),

  h2("6.4 交互指南"),
  tblCaption("表 6-4　常用交互速查"),
  table(
    ["操作", "方式", "说明"],
    [
      ["发送问题", "回车键 / 点击发送按钮", "Shift + 回车可换行"],
      ["新建会话", "点击「新建会话」", "每个会话独立保存，可随时切回历史会话"],
      ["筛选范围", "顶部朝代 / 战争类型选择器", "限定检索范围，可提高特定朝代问题的精度"],
      ["查看证据 / 追问实体", "点击引用编号或图谱子图节点", "展开核对依据；以上下文持续追问"],
      ["导出回答", "点击「导出」", "把当前回答与引用导出留存"],
      ["图谱漫游 / 数据维护", "菜单「知识图谱」「数据运营」", "四维度展开关系网络；数据集版本与质检工作台"],
    ],
    [18, 34, 48]
  ),
  p("问答支持连续追问，且会继承当前会话的上下文。例如先问「涿鹿之战是什么」，再问「它的主帅是谁」，系统能正确理解为仍在讨论涿鹿之战。会话历史保存在本地，并按登录账号隔离——不同账号在同一台电脑上登录时，不会看到彼此的提问记录。")
);

// ══════ 七、应用前景与商业模式 ══════
add(
  h1("七、应用前景与商业模式"),
  h2("7.1 应用场景"),
  p("项目的核心能力——把垂直领域文献转成可溯源的知识问答——并不局限于战争史。它的可迁移性来自架构本身：只要替换语料与抽取配置，同一套「抽取流水线 + 双数据库 + 双通道检索 + 引用溯源」的骨架就能服务其他文献密集型领域。"),
  tblCaption("表 7-1　可迁移的应用场景"),
  table(
    ["场景", "语料形态", "迁移工作量"],
    [
      ["历史教学与研学", "通史、断代史教材", "替换语料与词表，骨架复用"],
      ["文博展陈与导览", "文物档案、地方志", "增加地理与时间维度配置"],
      ["非遗与传统文化推广", "口述史、传承谱系", "关系类型表调整"],
      ["红色教育与党史学习", "党史文献、人物传记", "词表与实体类型调整"],
      ["企业知识库问答", "产品文档、工单记录", "抽取提示词与关系类型重定义"],
    ],
    [24, 34, 42]
  ),

  h2("7.2 推广路径"),
  pl("第一步：以高校历史院系与中学为切入口。", "先以免费试用方式进入课堂与备课场景，通过教师的真实使用反馈验证问答准确率与引用完整性。这一阶段的重点是证明「可溯源」带来的教学价值。"),
  pl("第二步：与文博机构合作建设展陈内容。", "博物馆与文旅单位有大量文献与展陈数字化需求，而它们的痛点与历史教学同源——内容要准、要能溯源、要能回答观众提问。"),
  pl("第三步：沉淀为可配置的垂直知识底座。", "把抽取配置、关系类型表、质检规则做成可替换的配置项，形成一套面向文献型领域的知识底座产品。"),

  h2("7.3 商业模式"),
  tblCaption("表 7-2　商业模式设计"),
  table(
    ["模式", "对象", "内容"],
    [
      ["订阅制 SaaS", "高校、中学、文化机构", "按年订阅问答与可视化服务，含数据托管与运维"],
      ["私有化部署", "对数据安全有要求的机构", "一次性授权加年度维护，部署到机构内网"],
      ["知识底座定制", "出版、文旅、企业", "按语料与领域定制抽取配置与关系体系，按项目计费"],
      ["内容增值服务", "内容创作者、出版机构", "结构化知识包与关系图谱授权，用于图书与课程开发"],
    ],
    [22, 28, 50]
  ),
  p("成本结构上，系统的主要可变成本来自大模型调用。本项目已通过两项设计显著压低这一成本：其一，知识抽取是一次性离线投入，产物可长期复用，不随用户量线性增长；其二，问答侧的检索在本地完成，只有最终生成环节调用大模型，且无证据时直接拒答，避免了无效调用。私有化部署方案还能进一步接入机构自有模型，把可变成本转为固定成本。"),

  h2("7.4 社会价值"),
  p("从受众看，本项目降低了普通人接触专业史料的门槛——不必通读三十万字，也能获得准确且可核验的答案；也降低了研究者的检索成本——不必人工横向串联线索，就能看到跨朝代的关系脉络。从方法论看，它证明了在垂直领域里「让 AI 承认不知道」比「让 AI 什么都答」更有价值，三层防幻觉机制与引用溯源体系为文化类 AI 应用提供了一套可复用的可信度设计范式。")
);

// ══════ 八、项目进度与完成情况 ══════
add(
  h1("八、项目进度与完成情况"),
  h2("8.1 研发进度"),
  p("研发分五个阶段推进。第一阶段完成知识抽取流水线、数据导入与图谱同步、图谱可视化；第二阶段完成 RAG 双通道检索问答、引用溯源与知识面板；第三阶段完成数据运营与质检、飞书机器人、用户与权限体系；第四阶段完成安全基线加固、自动化测试与持续集成、生产部署。以上四个阶段均已完成。第五阶段正在进行，重点是在真实教学场景中试用、人工复核问答准确率，并推进领域迁移。"),

  h2("8.2 当前完成度与实测结果"),
  tblCaption("表 8-1　关键指标实测"),
  table(
    ["指标", "结果"],
    [
      ["数据规模", "实体 9925 个（地点 5316 / 人物 2492 / 组织 1067）、战争事件 1050 场、关系 17700 条"],
      ["推理增强", "离线固化推理边 11833 条"],
      ["检索索引", "文本块 9544 个（原文 934 / 事件卡 1107 / 证据 7503），向量维度 1024"],
      ["自动化测试", "四个模块合计 1351 条用例，持续集成十个作业全部通过"],
      ["问答性能", "整链经公网反向代理实测 1.8 秒，单次回答引用条目 18 条"],
      ["生产部署", "已完成服务器部署，服务自检 18 项全部通过"],
    ],
    [20, 80]
  ),
);

// ─────────────────────────── 组装文档 ───────────────────────────
const coverConfig = {
  title: "干戈纪略",
  subtitle: "中国历史战争知识图谱与智能问答系统",
  englishLabel: "HISTORICAL WAR KNOWLEDGE GRAPH",
  metaLines: [
    "作品名称：干戈纪略 —— 中国历史战争知识图谱与智能问答系统",
    "参赛赛道：软件赛道",
    "参赛组别：高校组",
    "团队名称：干戈纪略",
    "参赛人：吴遂杰",
    "指导教师：龚兰兰",
    "所在院校：苏州城市学院",
    "完成日期：2026 年 9 月",
  ],
};

const doc = new Document({
  creator: "干戈纪略团队",
  title: "干戈纪略 —— 中国历史战争知识图谱与智能问答系统（应用方案）",
  description: "2026 年 iCAN 大学生创新创业大赛 AI 应用创新挑战赛参赛作品应用方案",
  styles: {
    default: {
      document: {
        run: { font: BODY_FONT, size: 24, color: c(P.body) },
        paragraph: { spacing: { line: 312 } },
      },
      heading1: {
        run: { font: HEAD_FONT, size: 32, bold: true, color: c(P.primary) },
      },
      heading2: {
        run: { font: HEAD_FONT, size: 28, bold: true, color: c(P.primary) },
      },
      heading3: {
        run: { font: HEAD_FONT, size: 24, bold: true, color: c(P.body) },
      },
    },
  },
  features: { updateFields: true },
  sections: [
    // 1. 封面：无页码
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 0, bottom: 0, left: 0, right: 0 },
        },
      },
      children: buildCover(coverConfig),
    },
    // 2. 目录：罗马数字页码
    {
      properties: {
        type: SectionType.NEXT_PAGE,
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, bottom: 1440, left: 1701, right: 1417 },
          pageNumbers: { start: 1, formatType: NumberFormat.UPPER_ROMAN },
        },
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: c(P.secondary) })],
          })],
        }),
      },
      children: [
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 200, after: 300, line: 480 },
          children: [new TextRun({ text: "目　　录", size: 36, bold: true, color: c(P.primary), font: HEAD_FONT })],
        }),
        new TableOfContents("目录", { hyperlink: true, headingStyleRange: "1-1" }),
        new Paragraph({ children: [new PageBreak()] }),
      ],
    },
    // 3. 正文：阿拉伯数字页码，从 1 开始
    {
      properties: {
        type: SectionType.NEXT_PAGE,
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, bottom: 1440, left: 1701, right: 1417 },
          pageNumbers: { start: 1, formatType: NumberFormat.DECIMAL },
        },
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: c(P.secondary) })],
          })],
        }),
      },
      children: body.filter(Boolean),
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  const out = path.join(__dirname, "..", "干戈纪略-应用方案.docx");
  fs.writeFileSync(out, buf);
  console.log("已生成：" + out + "（" + buf.length + " 字节）");
});
