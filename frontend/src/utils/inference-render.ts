/** 问答页（inference/index.vue）的渲染工具（S3-5 第一步，2026-09-25）。
 *
 * 从那个 3400 行的页面里搬出来的纯函数：Markdown 渲染与净化、知识图谱上下文的
 * HTML 拼装、JSON 转表格、思考过程渲染。它们与 Vue 状态无关，原先埋在页面里、
 * 完全没法单测；现在单独成模块，tests/unit/inference-render.test.ts 是它们的底线。
 *
 * 安全约定（与页面里的注释一致）：模型输出与图谱数据都属不可信内容，
 * 因此 MarkdownIt 开 html: false，所有进入 v-html 的 HTML 一律过 DOMPurify。
 */

import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';
import DOMPurify from 'dompurify';

import { nodeDisplayName } from './knowledge';

export function escapeHtml(text: string): string {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function highlightCode(str: string, lang: string): string {
  if (lang && hljs.getLanguage(lang)) {
    try {
      return '<pre class="hljs"><code>' +
             hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
             '</code></pre>';
    } catch (__) {}
  }

  return '<pre class="hljs"><code>' + escapeHtml(str) + '</code></pre>';
}


// 创建Markdown渲染器实例
// html: false —— 不直通 raw HTML。模型输出与图谱数据都属于不可信内容，
// 它们拼出的 HTML 若直通 v-html 就是存储型 XSS 的入口。
export const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight: highlightCode,
});

// 所有进入 v-html 的 HTML 统一在这里净化（DOMPurify 兜底，去掉脚本与事件属性）
export function sanitizeHtml(html: string): string {
  if (!html) return '';
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}

// Markdown 文本 -> 可安全交给 v-html 的 HTML
export function renderMarkdown(text: string): string {
  if (!text) return '';
  return sanitizeHtml(md.render(text));
}


// 渲染知识图谱上下文为Markdown
// 图谱数据来自接口，属不可信内容：统一在出口做 HTML 净化
// 节点展示名：与 utils/knowledge 的 nodeDisplayName 同义。
// 页面里原本也有一份同名实现（只给图谱抽屉用），抽模块时它被带进了这里的调用点，
// 因此在这里定义一次、页面从模块导入，避免两处定义漂移。
export const getGraphNodeDisplayName = (node: Record<string, any>) => nodeDisplayName(node || {});

export function renderKgContextMarkdown(context: string): string {
  return sanitizeHtml(buildKgContextHtml(context));
}

export function buildKgContextHtml(context: string): string {
  if (!context) return '';

  // 尝试解析为JSON并转换为表格
  try {
    const jsonData = JSON.parse(context);
    if (typeof jsonData === 'object') {
      // 检查是否包含nodes和lines（知识图谱数据结构）
      if (jsonData.nodes && Array.isArray(jsonData.nodes)) {
        return renderJsonTableMarkdown(jsonData);
      }

      // 一般JSON对象转表格
      return jsonObjectToMarkdownTable(jsonData);
    }
  } catch (e) {
    // 解析失败，作为普通文本处理
  }

  // 处理新的文本格式 - 使用更美观的渲染
  return renderFormattedKgContext(context);
}

// 渲染格式化的知识图谱上下文
export function renderFormattedKgContext(context: string): string {
  if (!context) return '';
  
  
  // 检查是否是旧格式的简单文本（不包含【】章节标记）
  if (!context.includes('【') || !context.includes('】')) {
    // 使用 pre 标签保持格式，或者直接用 markdown 渲染
    return `<pre style="white-space: pre-wrap; font-family: inherit; line-height: 1.6;">${context}</pre>`;
  }
  
  // 按行分割
  const lines = context.split('\n');
  let html = '<div class="kg-context-formatted">';
  
  let currentSection = '';
  let sectionContent: string[] = [];
  
  const flushSection = () => {
    if (!currentSection) return;
    
    
    if (currentSection === '涉及实体') {
      html += '<div class="kg-section kg-entities">';
      html += '<div class="kg-section-title">📚 涉及实体</div>';
      html += '<div class="kg-entity-list">';
      sectionContent.forEach((line, idx) => {
        // 解析实体行: "⚔️ 实体名 (类型)" 
        // 使用简单的字符串处理代替正则
        const parenIdx = line.lastIndexOf('(');
        if (parenIdx > 0 && line.endsWith(')')) {
          const beforeParen = line.substring(0, parenIdx).trim();
          const type = line.substring(parenIdx + 1, line.length - 1);
          // 提取图标（第一个字符/emoji）和名称
          const firstSpaceIdx = beforeParen.indexOf(' ');
          if (firstSpaceIdx > 0) {
            const icon = beforeParen.substring(0, firstSpaceIdx);
            const name = beforeParen.substring(firstSpaceIdx + 1).trim();
            html += `<span class="kg-entity-tag" data-type="${type}">${icon} ${name}</span>`;
          }
        } else {
        }
      });
      html += '</div></div>';
    } else if (currentSection.includes('关系')) {
      const isInferred = currentSection.includes('推理');
      html += `<div class="kg-section kg-relations ${isInferred ? 'inferred' : 'direct'}">`;
      html += `<div class="kg-section-title">${isInferred ? '🔍 推理关系' : '🔗 直接关系'}</div>`;
      html += '<div class="kg-relation-list">';
      sectionContent.forEach((line, idx) => {
        // 解析关系行: "1. 源 →【关系】→ 目标"
        // 使用字符串分割代替正则，更健壮
        const arrow1Idx = line.indexOf('→【');
        const arrow2Idx = line.indexOf('】→');
        if (arrow1Idx > 0 && arrow2Idx > arrow1Idx) {
          // 提取序号和源实体
          const beforeFirstArrow = line.substring(0, arrow1Idx).trim();
          const dotIdx = beforeFirstArrow.indexOf('.');
          if (dotIdx > 0) {
            const num = beforeFirstArrow.substring(0, dotIdx).trim();
            const source = beforeFirstArrow.substring(dotIdx + 1).trim();
            // 提取关系类型
            const relation = line.substring(arrow1Idx + 2, arrow2Idx).trim();
            // 提取目标实体
            const target = line.substring(arrow2Idx + 2).trim();
            
            html += `
              <div class="kg-relation-item">
                <span class="kg-relation-num">${num}</span>
                <span class="kg-relation-source">${source}</span>
                <span class="kg-relation-arrow">→</span>
                <span class="kg-relation-type">${relation}</span>
                <span class="kg-relation-arrow">→</span>
                <span class="kg-relation-target">${target}</span>
              </div>
            `;
          }
        } else if (line.startsWith('(') && line.endsWith(')')) {
          // 基于关系的说明
          html += `<div class="kg-relation-note">${line}</div>`;
        } else if (line.includes('...')) {
          // 省略说明
          html += `<div class="kg-relation-more">${line}</div>`;
        } else {
        }
      });
      html += '</div></div>';
    }
    
    sectionContent = [];
  };
  
  lines.forEach(line => {
    const trimmedLine = line.trim();
    // 匹配章节标题: 【章节名】 或 【章节名】(说明)
    const sectionMatch = trimmedLine.match(/^【(.+?)】(.*)$/);
    if (sectionMatch) {
      flushSection();
      currentSection = sectionMatch[1];
    } else if (trimmedLine && !trimmedLine.startsWith('📚')) {
      sectionContent.push(trimmedLine);
    }
  });
  
  flushSection();
  html += '</div>';
  
  
  // 如果生成的HTML只有外壳（没有实际内容），回退到原始文本
  if (html.length < 50 || !html.includes('kg-section')) {
    return `<pre style="white-space: pre-wrap; font-family: inherit; line-height: 1.6;">${context}</pre>`;
  }
  
  return html;
}

// 将JSON对象转换为Markdown表格
export function jsonObjectToMarkdownTable(json: any): string {
  // 处理非对象或空对象
  if (!json || typeof json !== 'object' || Array.isArray(json) && json.length === 0) {
    return renderMarkdown('*无有效数据*');
  }

  // 处理数组
  if (Array.isArray(json)) {
    // 提取所有可能的键
    const allKeys = new Set<string>();
    json.forEach(item => {
      if (item && typeof item === 'object') {
        Object.keys(item).forEach(key => allKeys.add(key));
      }
    });

    const keys = Array.from(allKeys);
    if (keys.length === 0) {
      // 数组包含的不是对象
      let tableContent = '数组内容：\n\n';
      json.forEach((item, index) => {
        tableContent += `${index+1}. ${String(item)}\n`;
      });
      return renderMarkdown(tableContent);
    }

    // 构建表头
    let table = '| # | ' + keys.join(' | ') + ' |\n';
    table += '|' + '---|'.repeat(keys.length + 1) + '\n';

    // 构建表行
    json.forEach((item, index) => {
      table += `| ${index+1} |`;
      keys.forEach(key => {
        const value = item[key];
        if (value === undefined || value === null) {
          table += ' - |';
        } else if (typeof value === 'object') {
          table += ` ${JSON.stringify(value).substr(0, 20)}... |`;
        } else {
          table += ` ${String(value)} |`;
        }
      });
      table += '\n';
    });

    return renderMarkdown(table);
  }

  // 处理普通对象
  let table = '| 属性 | 值 |\n|---|---|\n';

  Object.entries(json).forEach(([key, value]) => {
    if (value === null || value === undefined) {
      table += `| ${key} | - |\n`;
    } else if (typeof value === 'object') {
      if (Array.isArray(value) && value.length > 0) {
        table += `| ${key} | ${value.length}个项目 |\n`;
      } else {
        table += `| ${key} | ${JSON.stringify(value).substr(0, 30)}... |\n`;
      }
    } else {
      table += `| ${key} | ${String(value)} |\n`;
    }
  });

  return renderMarkdown(table);
}

// 渲染知识图谱数据为Markdown表格
export function renderJsonTableMarkdown(jsonData: any): string {
  let result = '';

  // 添加节点表格
  if (jsonData.nodes && jsonData.nodes.length > 0) {
    result += '## 实体节点\n\n';
    result += '| # | ID | 名称 | 类型 |\n';
    result += '|---|---|---|---|\n';

    jsonData.nodes.forEach((node: any, index: number) => {
      const id = node.id || '-';
      const name = getGraphNodeDisplayName(node);
      const type = node.type || node.category || '-';
      result += `| ${index+1} | ${id} | ${name} | ${type} |\n`;
    });

    result += '\n\n';
  }

  // 添加关系表格
  if (jsonData.lines && jsonData.lines.length > 0) {
    result += '## 实体关系\n\n';
    result += '| # | 源实体 | 关系 | 目标实体 | 类型 |\n';
    result += '|---|---|---|---|---|\n';

    // 创建节点ID到名称的映射
    const nodeMap = new Map();
    if (jsonData.nodes) {
      jsonData.nodes.forEach((node: any) => {
        if (node.id !== undefined) {
          nodeMap.set(String(node.id), getGraphNodeDisplayName(node));
        }
      });
    }

    // 添加调试信息

    jsonData.lines.forEach((line: any, index: number) => {
      // 源和目标节点ID
      const fromId = line.from !== undefined ? line.from :
                    (line.source !== undefined ? line.source : '-');
      const toId = line.to !== undefined ? line.to :
                  (line.target !== undefined ? line.target : '-');

      // 尝试获取关系文本
      let relationText = '-';
      if (line.text !== undefined && line.text !== '') {
        relationText = line.text;
      } else if (line.relation !== undefined && line.relation !== '') {
        relationText = line.relation;
      } else if (line.label !== undefined && line.label !== '') {
        relationText = line.label;
      } else if (line.name !== undefined && line.name !== '') {
        relationText = line.name;
      }

      // 关系类型（是否为推理关系）
      let relationType = '数据库关系';
      if (line.inferred === true) {
        relationType = '推理关系';
      } else if (line.derived_from) {
        relationType = '推理关系';
      } else if (line.rule_id) {
        relationType = '推理关系';
      }

      // 获取实体名称
      const sourceName = nodeMap.get(String(fromId)) || String(fromId);
      const targetName = nodeMap.get(String(toId)) || String(toId);

      result += `| ${index+1} | ${sourceName} | ${relationText} | ${targetName} | ${relationType} |\n`;
    });

    // 添加完整属性信息表格
    result += '\n\n## 关系详细属性\n\n';
    result += '| # | 关系 | 源 → 目标 | 属性信息 |\n';
    result += '|---|---|---|---|\n';

    jsonData.lines.forEach((line: any, index: number) => {
      // 获取源和目标ID
      const fromId = line.from !== undefined ? line.from :
                   (line.source !== undefined ? line.source : '-');
      const toId = line.to !== undefined ? line.to :
                 (line.target !== undefined ? line.target : '-');

      // 获取关系文本
      let relationText = line.text || line.relation || line.label || '-';

      // 获取实体名称
      const sourceName = nodeMap.get(String(fromId)) || String(fromId);
      const targetName = nodeMap.get(String(toId)) || String(toId);

      // 收集所有属性
      const propPairs = [];
      for (const [key, value] of Object.entries(line)) {
        // 跳过基本属性
        if (['from', 'to', 'source', 'target', 'text', 'relation', 'label'].includes(key)) {
          continue;
        }

        // 格式化值
        let formattedValue = value;
        if (typeof value === 'object') {
          formattedValue = JSON.stringify(value).substring(0, 50);
          if (JSON.stringify(value).length > 50) {
            formattedValue += '...';
          }
        }

        propPairs.push(`${key}: ${formattedValue}`);
      }

      const props = propPairs.length > 0 ? propPairs.join('<br>') : '-';
      result += `| ${index+1} | ${relationText} | ${sourceName} → ${targetName} | ${props} |\n`;
    });
  } else {
    result += '## 实体关系\n\n没有关系数据\n\n';
  }

  return renderMarkdown(result);
}

// 思考过程渲染函数
export function renderThinkingContent(thinkingText: string): string {
  if (!thinkingText) return '';

  // 先转义原始文本再套格式化标签：思考过程同样是模型输出，
  // 其中出现 <script> / onerror 之类内容时不能当 HTML 执行。
  let formattedText = md.utils.escapeHtml(thinkingText);

  // 处理标题格式（例如：1. **问题理解**:）
  formattedText = formattedText.replace(/(\d+\.\s*\*\*[^*]+\*\*:)/g, '<h4>$1</h4>');

  // 处理子标题和实体标记（例如：- **平江府**:）
  formattedText = formattedText.replace(/(-\s*\*\*[^*]+\*\*:)/g, '<h5>$1</h5>');

  // 处理普通加粗文本
  formattedText = formattedText.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // 处理列表项（以 - 开头的行）
  formattedText = formattedText.replace(/^-\s+([^<].*)/gm, '<div class="list-item">• $1</div>');

  // 将换行符转换为<br>标签
  formattedText = formattedText.replace(/\n/g, '<br>');

  // 修复在替换后可能出现的多余<br>标签
  formattedText = formattedText.replace(/<\/h4><br>/g, '</h4>');
  formattedText = formattedText.replace(/<\/h5><br>/g, '</h5>');
  formattedText = formattedText.replace(/<\/div><br>/g, '</div>');

  // 为代码块添加样式
  formattedText = formattedText.replace(/```([\s\S]*?)```/g, '<pre class="thinking-code"><code>$1</code></pre>');

  return sanitizeHtml(formattedText);
}
