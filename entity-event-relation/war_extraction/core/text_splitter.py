"""
长文本智能分段模块
解决大模型上下文长度限制问题，确保语义完整性
"""
from __future__ import annotations

from typing import List, Tuple
from war_extraction.config import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP
from war_extraction.core.text_cleaner import chapter_headings, paragraph_index

__all__ = ["TextSplitter", "paragraph_index"]


class TextSplitter:
    """
    智能文本分段器
    按字符数切分文本，优先在**章节标题 → 段落边界 → 句子边界**处分割，保留重叠上下文。

    **为什么要按这个优先级切。** 原先只在句末标点处切，切点不够时**按字符硬切**
    （`.7 * chunk_size` 那道判断）。而每一段是独立送进模型的，硬切会把一章的开头
    切到上一段末尾，"上半句 + 下半句"分属两段，两边都读不成完整意思。
    书籍文本本来就有章节标题与段落空行这两级现成的语义边界，优先用它们更省事也更准。
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

    def _pick_break(self, text: str, start: int, end: int, chapter_positions: List[int]) -> int:
        """
        在 start~end 之间选切分点，优先级：章节标题 → 段落空行 → 句末标点。

        只有切点落在"本段至少 55% 长度"之后才采用，否则保持原定的字符位置——
        否则一个紧挨着段首的章节标题会让每段都短得离谱。
        """
        floor = start + int(self.chunk_size * 0.55)

        for position in reversed(chapter_positions):
            if floor < position <= end:
                return position

        paragraph_break = text.rfind("\n\n", start, end)
        if paragraph_break > floor:
            return paragraph_break + 2

        sentence_end = max(
            text.rfind("。", start, end),
            text.rfind("？", start, end),
            text.rfind("！", start, end),
        )
        # 句末标点这一级保留原先更严的门槛（70%），避免退化成"段段偏短"
        if sentence_end > start + self.chunk_size * 0.7:
            return sentence_end + 1

        return end

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

        # 章节标题位置只算一次：每段都要拿它选切点
        chapter_positions = [position for position, _title in chapter_headings(text)]

        chunks = []
        start = 0

        while start < len(text):
            # 计算当前片段的结束位置（不超过文本末尾）
            end = min(start + self.chunk_size, len(text))

            # 如果不是最后一段，按"章节 → 段落 → 句子"的优先级找切点
            if end < len(text):
                end = self._pick_break(text, start, end, chapter_positions)

            # 提取当前片段并记录起止索引
            chunk = text[start:end]
            chunks.append((start, end, chunk))

            # 计算下一个起始位置（考虑重叠，避免信息丢失）
            start = end - self.overlap

            # 安全机制：防止因 overlap 过大导致死循环
            if start + self.overlap >= len(text):
                break

        return chunks
