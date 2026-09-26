from __future__ import annotations

# -*- coding: utf-8 -*-
"""
导入坐标到数据库
将审核通过的坐标数据导入 SQLite 数据库
"""

import sqlite3
import json
import os
from typing import List, Dict

from .db_path import resolve_db_path
from datetime import datetime


class CoordinateImporter:
    """坐标导入器"""

    def __init__(self, db_path: str = None):
        """
        初始化导入器

        Args:
            db_path: 数据库路径
        """
        if db_path is None:
            # 路径解析统一走 db_path 模块（显式参数 → EER_DB_PATH → 默认 backend/database）
            db_path = resolve_db_path()

        self.db_path = db_path

        # 检查数据库是否存在
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"数据库文件不存在: {db_path}")

        self.conn = None
        self.success_count = 0
        self.fail_count = 0
        self.skip_count = 0

    def connect(self):
        """连接数据库"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def check_table_structure(self) -> bool:
        """
        检查 places 表结构是否包含坐标字段

        Returns:
            表结构是否正确
        """
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA table_info(places)")
        columns = {row['name'] for row in cursor.fetchall()}

        required_columns = {'longitude', 'latitude', 'coord_source', 'coord_confidence', 'coord_note'}
        missing_columns = required_columns - columns

        if missing_columns:
            print(f"缺少以下列: {missing_columns}")
            return False

        return True

    def ensure_columns(self):
        """确保坐标相关列存在"""
        cursor = self.conn.cursor()

        # 检查并添加缺失的列
        columns_to_add = [
            ('longitude', 'FLOAT'),
            ('latitude', 'FLOAT'),
            ('coord_source', 'VARCHAR(100)'),
            ('coord_confidence', 'VARCHAR(50)'),
            ('coord_note', 'TEXT')
        ]

        for col_name, col_type in columns_to_add:
            try:
                cursor.execute(f"ALTER TABLE places ADD COLUMN {col_name} {col_type}")
                print(f"添加列: {col_name}")
            except sqlite3.OperationalError:
                # 列已存在
                pass

        self.conn.commit()

    def import_single(self, coord: Dict) -> bool:
        """
        导入单条坐标数据

        Args:
            coord: 坐标数据字典

        Returns:
            是否成功
        """
        place_id = coord.get('place_id')
        if not place_id:
            print(f"  ✗ 缺少 place_id: {coord.get('original_name', 'N/A')}")
            return False

        # 验证坐标数据
        longitude = coord.get('longitude')
        latitude = coord.get('latitude')

        if longitude is None or latitude is None:
            print(f"  ✗ 坐标数据不完整: {coord.get('original_name', 'N/A')}")
            return False

        # 验证坐标范围（中国范围）
        if not (73 <= longitude <= 135 and 18 <= latitude <= 54):
            print(f"  ⚠ 坐标可能超出中国范围: {coord.get('original_name', 'N/A')} ({longitude}, {latitude})")
            # 仍然导入，但发出警告

        cursor = self.conn.cursor()

        try:
            # 更新坐标
            cursor.execute("""
                UPDATE places
                SET longitude = ?,
                    latitude = ?,
                    coord_source = ?,
                    coord_confidence = ?,
                    coord_note = ?
                WHERE id = ?
            """, (
                longitude,
                latitude,
                coord.get('source', 'api'),
                coord.get('confidence', 'medium'),
                coord.get('edit_note', coord.get('note', '')),
                place_id
            ))

            if cursor.rowcount > 0:
                self.success_count += 1
                return True
            else:
                print(f"  ✗ 未找到地点 ID: {place_id}")
                self.fail_count += 1
                return False

        except Exception as e:
            print(f"  ✗ 导入失败 {coord.get('original_name', 'N/A')}: {str(e)}")
            self.fail_count += 1
            return False

    def batch_import(self, coordinates: List[Dict]) -> Dict:
        """
        批量导入坐标

        Args:
            coordinates: 坐标列表

        Returns:
            导入结果统计
        """
        total = len(coordinates)

        print(f"\n开始导入坐标: {total} 条记录")
        print("=" * 60)

        for i, coord in enumerate(coordinates, 1):
            original_name = coord.get('original_name', 'N/A')
            print(f"[{i}/{total}] {original_name}", end="")

            success = self.import_single(coord)

            if success:
                print(f" -> ({coord['longitude']:.4f}, {coord['latitude']:.4f})")
            else:
                print(" -> 失败")

        # 提交事务
        self.conn.commit()

        # 打印统计
        print("\n" + "=" * 60)
        print(f"导入完成:")
        print(f"  总记录数: {total}")
        print(f"  成功: {self.success_count}")
        print(f"  失败: {self.fail_count}")
        print(f"  成功率: {(self.success_count / total * 100):.1f}%")

        return {
            'total': total,
            'success': self.success_count,
            'fail': self.fail_count,
            'success_rate': f"{(self.success_count / total * 100):.1f}%"
        }

    def import_from_file(self, input_file: str) -> Dict:
        """
        从文件导入坐标

        Args:
            input_file: 输入文件路径

        Returns:
            导入结果统计
        """
        # 读取输入文件
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 支持两种格式：直接列表或包含 approved 字段的对象
        if isinstance(data, list):
            coordinates = data
        elif isinstance(data, dict) and 'approved' in data:
            coordinates = data['approved']
        else:
            raise ValueError("不支持的文件格式")

        print(f"加载 {len(coordinates)} 条坐标记录")

        # 连接数据库
        self.connect()

        try:
            # 确保表结构正确
            self.ensure_columns()

            # 批量导入
            result = self.batch_import(coordinates)

            return result

        finally:
            self.close()


def import_coordinates(input_file: str, db_path: str = None) -> Dict:
    """
    导入坐标到数据库

    Args:
        input_file: 输入文件路径
        db_path: 数据库路径

    Returns:
        导入结果统计
    """
    importer = CoordinateImporter(db_path)
    return importer.import_from_file(input_file)


def import_from_approved_json(approved_data: List[Dict], db_path: str = None) -> Dict:
    """
    直接从审核通过的数据导入坐标

    Args:
        approved_data: 审核通过的数据列表
        db_path: 数据库路径

    Returns:
        导入结果统计
    """
    importer = CoordinateImporter(db_path)
    importer.connect()

    try:
        importer.ensure_columns()
        return importer.batch_import(approved_data)
    finally:
        importer.close()


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("用法: python import_coordinates.py <input_file> [db_path]")
        print("示例: python import_coordinates.py approved_coordinates.json")
        sys.exit(1)

    input_file = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else None

    result = import_coordinates(input_file, db_path)

    print(f"\n导入完成: {result['success']}/{result['total']} 成功")
