"""
核心基础设施模块
提供LLM客户端、文本处理、缓存等基础服务
"""

from war_extraction.core.llm_client import DeepSeekClient
from war_extraction.core.text_cleaner import clean_text, chapter_headings, paragraph_index
from war_extraction.core.text_splitter import TextSplitter
from war_extraction.core.cache_manager import CacheManager

__all__ = [
    'DeepSeekClient',
    'TextSplitter',
    'CacheManager',
    # 输入清洗与章节定位（分段器优先在章节/段落边界切分，用的是这两个）
    'clean_text',
    'chapter_headings',
    'paragraph_index',
]
