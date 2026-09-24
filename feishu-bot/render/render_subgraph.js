/**
 * 子图 SSR 出图（开发文档 5.6）。
 *
 * 用法：node render_subgraph.js <输入 JSON 路径>
 *   输入：{"nodes": [{id,type,name,dynasty}], "edges": [{source,target,relation}],
 *          "out": "<输出 PNG 路径>", "width": 1200, "height": 900}
 *   输出（stdout 一行 JSON）：{"ok": true, "bytes": <PNG 字节数>} 或 {"ok": false, "error": "..."}
 *
 * **stdout 只回 ok/bytes，不回文件路径**：路径可能含中文（Windows 用户目录），
 * 而子进程 stdout 的编码取决于调用方的 locale——Python 侧若按本地编码（如 cp936）
 * 解读 UTF-8 字节，JSON 就会解析失败，被误判成"渲染失败"（实测踩过）。
 * 输出路径由 Python 侧自己持有，这里不必再回传。
 *
 * **布局约束（先落这条，否则出图必糊）**：前端 SubGraphView 用的是 ECharts 力导向
 * 布局（layout: 'force'），而 SSR 是一次性同步渲染、力导向的异步迭代不会执行——
 * 直接拿同一份 option 出图会得到节点重叠的乱图。因此这里先用 d3-force 预跑迭代
 * 把 x/y 写进节点，再以 layout: 'none' 渲染。
 *
 * 配色对齐前端 ENTITY_TYPE_COLORS（RAG/frontend/src/types/contract.ts），
 * 布局只保证信息结构一致，不承诺与网页视觉一致（需求文档待决项 1）。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const echarts = require('echarts');
const { Resvg } = require('@resvg/resvg-js');
const d3 = require('d3-force');

const ENTITY_TYPE_COLORS = {
  '事件': '#b4532a',
  '人物': '#0f766e',
  '组织': '#1d4ed8',
  '地点': '#7c3aed',
};
const DEFAULT_COLOR = '#64748b';
const FORCE_ITERATIONS = 300;
const WIDTH = 1200;
const HEIGHT = 900;

function readInput() {
  const file = process.argv[2];
  if (!file) throw new Error('缺少输入 JSON 路径参数');
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/** 用 d3-force 预计算坐标（SSR 无法执行 ECharts 的力导向迭代）。 */
function layout(nodes, edges) {
  const simNodes = nodes.map((n, i) => ({ id: n.id, index: i }));
  const indexById = new Map(simNodes.map((n) => [n.id, n.index]));
  const links = edges
    .filter((e) => indexById.has(e.source) && indexById.has(e.target))
    .map((e) => ({ source: indexById.get(e.source), target: indexById.get(e.target) }));

  const simulation = d3.forceSimulation(simNodes)
    .force('link', d3.forceLink(links).distance(140).strength(0.6))
    .force('charge', d3.forceManyBody().strength(-340))
    .force('center', d3.forceCenter(WIDTH / 2, HEIGHT / 2))
    .force('collide', d3.forceCollide(46))
    .stop();

  // 同步跑满迭代次数：tick 次数必须显式给足，否则节点会挤在初始位置附近
  for (let i = 0; i < FORCE_ITERATIONS; i += 1) simulation.tick();

  return simNodes.map((sn, i) => ({
    ...nodes[i],
    x: Number.isFinite(sn.x) ? sn.x : WIDTH / 2,
    y: Number.isFinite(sn.y) ? sn.y : HEIGHT / 2,
  }));
}

function buildOption(nodes, edges) {
  const data = nodes.map((n) => ({
    id: n.id,
    name: n.name || n.id,
    x: n.x,
    y: n.y,
    symbolSize: n.type === '事件' ? 34 : 28,
    itemStyle: { color: ENTITY_TYPE_COLORS[n.type] || DEFAULT_COLOR },
  }));
  const links = edges.map((e) => ({
    source: e.source,
    target: e.target,
    relation: e.relation || '',
  }));

  return {
    animation: false,
    backgroundColor: '#ffffff',
    series: [{
      type: 'graph',
      // layout: 'none' 才能用我们预计算好的 x/y（力导向在 SSR 下不会迭代）
      layout: 'none',
      data,
      links,
      roam: false,
      draggable: false,
      label: { show: true, position: 'right', fontSize: 12, color: '#334155' },
      lineStyle: { color: '#94a3b8', width: 1.5, curveness: 0.12 },
      edgeLabel: {
        show: true,
        fontSize: 10,
        color: '#64748b',
        formatter: (p) => (p.data && p.data.relation) || '',
      },
    }],
  };
}

function render() {
  const input = readInput();
  const nodes = Array.isArray(input.nodes) ? input.nodes : [];
  const edges = Array.isArray(input.edges) ? input.edges : [];
  if (!nodes.length) throw new Error('nodes 为空，无可渲染内容');

  const laidOut = layout(nodes, edges);
  const chart = echarts.init(null, null, {
    renderer: 'svg',
    ssr: true,
    width: input.width || WIDTH,
    height: input.height || HEIGHT,
  });
  chart.setOption(buildOption(laidOut, edges));
  const svg = chart.renderToSVGString();
  chart.dispose();

  const png = new Resvg(svg, {
    fitTo: { mode: 'width', value: input.width || WIDTH },
    font: { loadSystemFonts: true },
  }).render().asPng();

  const out = input.out || path.join(process.cwd(), 'subgraph.png');
  fs.writeFileSync(out, png);
  return png.length;
}

try {
  const bytes = render();
  process.stdout.write(JSON.stringify({ ok: true, bytes }) + '\n');
} catch (err) {
  // 只回可读信息，不回路径（见文件头：避免把中文路径塞进 stdout）
  const message = (err && err.message) ? String(err.message) : String(err);
  process.stdout.write(JSON.stringify({ ok: false, error: message }) + '\n');
  process.exitCode = 1;
}
