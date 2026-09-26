/** 旧问答助手（`/knowledge/inference`）的领域类型。
 *
 * 这些类型以 `any` / 内联字面量的形式散在 `views/inference/index.vue` 里时无法复用，
 * 页面拆子组件后 props/emits 需要显式类型，于是收敛到这里。
 */

/** 图谱节点：字段随实体类型不同（朝代/时间/地点/交战方…），前端只做展示，故保留索引签名。 */
export interface KgGraphNode {
  id?: string | number;
  name?: string;
  type?: string;
  category?: string;
  [key: string]: unknown;
}

/** 图谱连线（`from` / `to` 指向节点的 id）。 */
export interface KgGraphLine {
  from?: string | number;
  to?: string | number;
  text?: string;
  relation_type?: string;
  /** 规则推理推出来的边：问答子图里不展示 */
  inferred?: boolean;
}

/** 传给 `KgGraph` 的数据（问答消息里的知识图谱快照）。 */
export interface KgGraphData {
  nodes: KgGraphNode[];
  lines: KgGraphLine[];
}

/** 点击图谱节点后抽屉里展示的节点详情：字段同样是实体类型决定的，保留索引签名。 */
export interface KgNodeDetail {
  type?: string;
  relations?: unknown;
  [key: string]: unknown;
}

/** 一条问答消息。 */
export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  time: number;
  /** 知识图谱上下文（后端给的关系文本，可能是 JSON 字符串） */
  kgContext?: string;
  /** 思考过程（逐条 Markdown） */
  thinking?: string[];
  /** 回答是否基于图谱；false 时界面提示"由通用知识生成" */
  fromKg: boolean;
  /** 问题里识别到的实体名，用于"问题中的实体"卡片 */
  entities?: string[];
  /** 后端原样返回的图谱数据（优先于解析 kgContext） */
  kg_data?: KgGraphData;
}

/** 一个会话（左侧列表的一项）。 */
export interface ChatSession {
  id: number;
  title: string;
  messages: ChatMessage[];
  lastTime: number;
}
