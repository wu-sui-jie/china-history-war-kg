"""
长文本智能分段模块
解决大模型上下文长度限制问题，确保语义完整性
"""
from __future__ import annotations

import re
from typing import List, Tuple
from src.config import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP


class TextSplitter:
    """
    智能文本分段器
    按字符数切分文本，优先在句子边界处分割，保留重叠上下文
    """

    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP):
        """
        初始化分段参数

        Args:
            chunk_size: 每段最大字符数（约 600-700 汉字），平衡效率与完整性
            overlap: 段间重叠字符数，保留上下文避免信息割裂
        """
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, text: str) -> List[Tuple[int, int, str]]:
        """
        将长文本分割为多个重叠的片段

        Args:
            text: 原始长文本

        Returns:
            片段列表，每个元素为 (起始索引, 结束索引, 片段文本)
        """
        # 如果文本未超过阈值，直接返回整体
        if len(text) <= self.chunk_size:
            return [(0, len(text), text)]

        chunks = []
        start = 0

        while start < len(text):
            # 计算当前片段的结束位置（不超过文本末尾）
            end = min(start + self.chunk_size, len(text))

            # 如果不是最后一段，尝试在句子边界处切分
            if end < len(text):
                # 查找最近的句号、问号、感叹号等句末标点
                sentence_end = max(
                    text.rfind("。", start, end),
                    text.rfind("？", start, end),
                    text.rfind("！", start, end)
                )
                # 确保切分点不会导致片段过短（至少达到 chunk_size 的 70%）
                if sentence_end > start + self.chunk_size * 0.7:
                    end = sentence_end + 1  # 包含句末标点

            # 提取当前片段并记录起止索引
            chunk = text[start:end]
            chunks.append((start, end, chunk))

            # 计算下一个起始位置（考虑重叠，避免信息丢失）
            start = end - self.overlap

            # 安全机制：防止因 overlap 过大导致死循环
            if start + self.overlap >= len(text):
                break

        return chunks
