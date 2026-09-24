from __future__ import annotations

# -*- coding: utf-8 -*-
"""
导出待编码地点
从 SQLite 数据库中导出没有坐标的地点
"""

import sqlite3
import json
import os
from typing import List, Dict

from .db_path import resolve_db_path
from datetime import datetime


def export_unmapped_places(db_path: str = None, output_dir: str = None) -> str:
    """
    导出没有坐标的地点到 JSON 文件

    Args:
        db_path: 数据库路径，默认为 backend/database
        output_dir: 输出目录，默认为当前目录

    Returns:
        输出文件路径
    """
    # 默认数据库路径
    if db_path is None:
        db_path = resolve_db_path()

    # 默认输出目录
    if output_dir is None:
        output_dir = os.path.dirname(__file__)

    # 检查数据库是否存在
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")

    # 连接数据库
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 查询没有坐标的地点
    cursor.execute("""
        SELECT id, name, modern_name, dynasty, province, city,
               district, specific_location
        FROM places
        WHERE longitude IS NULL
           OR latitude IS NULL
        ORDER BY dynasty, name
    """)

    places = []
    for row in cursor.fetchall():
        place = {
            'id': row['id'],
            'name': row['name'],
            'modern_name': row['modern_name'],
            'dynasty': row['dynasty'],
            'province': row['province'],
            'city': row['city'],
            'district': row['district'],
            'specific_location': row['specific_location']
        }
        places.append(place)

    conn.close()

    # 生成输出文件名
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'unmapped_places_{timestamp}.json')

    # 写入 JSON 文件
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(places, f, ensure_ascii=False, indent=2)

    print(f"导出完成: {len(places)} 个待编码地点")
    print(f"输出文件: {output_file}")

    return output_file


def get_unmapped_places_from_db(db_path: str = None) -> List[Dict]:
    """
    直接从数据库获取没有坐标的地点（不写入文件）

    Args:
        db_path: 数据库路径

    Returns:
        地点列表
    """
    if db_path is None:
        db_path = resolve_db_path()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, modern_name, dynasty, province, city,
               district, specific_location
        FROM places
        WHERE longitude IS NULL
           OR latitude IS NULL
        ORDER BY dynasty, name
    """)

    places = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return places


def get_statistics(db_path: str = None) -> Dict:
    """
    获取坐标统计信息

    Args:
        db_path: 数据库路径

    Returns:
        统计信息字典
    """
    if db_path is None:
        db_path = resolve_db_path()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 总地点数
    cursor.execute("SELECT COUNT(*) FROM places")
    total = cursor.fetchone()[0]

    # 有坐标的地点数
    cursor.execute("""
        SELECT COUNT(*) FROM places
        WHERE longitude IS NOT NULL AND latitude IS NOT NULL
    """)
    with_coords = cursor.fetchone()[0]

    # 按来源统计
    cursor.execute("""
        SELECT coord_source, COUNT(*) as count
        FROM places
        WHERE longitude IS NOT NULL AND latitude IS NOT NULL
        GROUP BY coord_source
    """)
    by_source = {row[0]: row[1] for row in cursor.fetchall()}

    # 按置信度统计
    cursor.execute("""
        SELECT coord_confidence, COUNT(*) as count
        FROM places
        WHERE longitude IS NOT NULL AND latitude IS NOT NULL
        GROUP BY coord_confidence
    """)
    by_confidence = {row[0]: row[1] for row in cursor.fetchall()}

    conn.close()

    return {
        'total_places': total,
        'with_coordinates': with_coords,
        'without_coordinates': total - with_coords,
        'completion_rate': f"{(with_coords / total * 100):.1f}%" if total > 0 else "0%",
        'by_source': by_source,
        'by_confidence': by_confidence
    }


if __name__ == '__main__':
    # 导出待编码地点
    output_file = export_unmapped_places()

    # 显示统计信息
    print("\n=== 坐标统计 ===")
    stats = get_statistics()
    print(f"总地点数: {stats['total_places']}")
    print(f"已有坐标: {stats['with_coordinates']}")
    print(f"待编码: {stats['without_coordinates']}")
    print(f"完成率: {stats['completion_rate']}")

    if stats['by_source']:
        print("\n按来源统计:")
        for source, count in stats['by_source'].items():
            print(f"  {source}: {count}")

    if stats['by_confidence']:
        print("\n按置信度统计:")
        for confidence, count in stats['by_confidence'].items():
            print(f"  {confidence}: {count}")
