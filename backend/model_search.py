"""
Neo4j图数据库操作类

功能说明:
    - Neo4j图数据库的连接和查询
    - 节点查询（按名称、按类型、模糊搜索）
    - 关系查询（获取关系类型、节点关联关系）
    - 图谱可视化数据构建
    - 子页面关系图谱查询（事件-事件、事件-组织、事件-人物、事件-地点）

数据流向:
    - 仅用于可视化展示
    - 数据从SQLite同步而来
    - 智能问答从此库查询图谱数据
"""

from py2neo import Graph

import local_settings
from relation_types import relationship_type_aliases


class neo4j_db():
    '''neo4j的操作'''
    def __init__(self, uri="bolt://localhost:7687", user=None, password=None, **kwargs):
        if user is None:
            user = local_settings.NEO4J_USER
        if password is None:
            password = local_settings.require_neo4j_password()

        try:
            self.graph = Graph(uri, user=user, password=password)
                # 测试连接
            test_result = self.graph.run("RETURN 1 as test").data()
            print(f"✅ Neo4j连接成功: {uri}")
        except Exception as e:
            print(f"❌ Neo4j连接失败: {e}")
            raise

    # 创建节点
    def create_node(self, label, name):
        """创建节点 - 使用 Cypher CREATE 确保立即提交"""
        try:
            # 使用参数化查询防止注入，并强制返回 ID
            cypher = f"""
            CREATE (n:`{label}` {{name: $name}})
            RETURN id(n) as node_id, labels(n) as labels, n.name as name
            """
            result = self.graph.run(cypher, name=name).data()

            if result and len(result) > 0:
                node_id = result[0]['node_id']
                labels = result[0]['labels']
                print(f"✅ Neo4j节点创建成功: ID={node_id}, Labels={labels}, Name={name}")

                # 返回一个包含 identity 属性的简单对象
                class SimpleNode:
                    def __init__(self, identity):
                        self.identity = identity

                return SimpleNode(node_id)
            else:
                print(f"❌ Neo4j节点创建失败: Cypher执行无返回结果")
                return None

        except Exception as e:
            print(f"❌ Neo4j节点创建异常: {e}")
            import traceback
            traceback.print_exc()
            raise

    # 更新节点
    def update_node(self, label, node_id, new_name):
        """使用 Cypher 更新节点名称"""
        try:
            cypher = f"""
            MATCH (n:`{label}`)
            WHERE id(n) = $node_id
            SET n.name = $new_name
            RETURN id(n) as node_id
            """
            result = self.graph.run(cypher, node_id=node_id, new_name=new_name).data()

            if result:
                print(f"✅ Neo4j节点更新成功: ID={node_id}, NewName={new_name}")
                return True
            else:
                print(f"⚠️ Neo4j节点更新失败: 未找到节点 {node_id}")
                return False

        except Exception as e:
            print(f"❌ Neo4j节点更新异常: {e}")
            raise

    # 获取节点详细信息
    def get_node_detail(self, node_id):
        """
        根据节点ID获取节点的完整属性信息

        :param node_id: Neo4j内部节点ID
        :return: 节点属性字典，不存在则返回None
        """

        # 构造Cypher查询语句，根据ID匹配节点
        sql = f'''
        MATCH (n)
        WHERE id(n) = {node_id}
        RETURN n, labels(n) AS labels
        '''

        # 执行查询语句，返回结果列表
        result = self.graph.run(sql).data()

        # 若查询结果不为空
        if result:
            # 获取节点对象
            node = result[0]['n']

            # 获取节点标签（类型）
            labels = result[0]['labels']

            # 将节点属性转换为字典格式
            node_properties = dict(node)

            # 添加节点ID（Neo4j内部ID）
            node_properties['id'] = node.identity

            # 添加节点类型（取第一个标签）
            node_properties['type'] = labels[0] if labels else ''

            # 返回节点详细信息
            return node_properties

        # 未查询到节点时返回None
        return None

    # 更新节点所有属性
    def update_node_properties(self, node_id, properties):
        """使用 Cypher 更新节点属性"""
        try:
            # 移除 id 和 type，这些不是节点属性
            props = {k: v for k, v in properties.items() if k not in ['id', 'type']}

            if not props:
                print(f"⚠️ 无有效属性需要更新")
                return True

            # 构建 SET 子句和 REMOVE 子句
            set_clauses = []
            remove_clauses = []
            params = {'node_id': node_id}
            for key, value in props.items():
                if value is None:
                    remove_clauses.append(f"n.{key}")
                elif isinstance(value, (str, int, float, bool)):
                    set_clauses.append(f"n.{key} = ${key}")
                    params[key] = value

            if not set_clauses and not remove_clauses:
                return True

            set_part = f"SET {', '.join(set_clauses)}" if set_clauses else ""
            remove_part = f"REMOVE {', '.join(remove_clauses)}" if remove_clauses else ""

            cypher = f"""
            MATCH (n)
            WHERE id(n) = $node_id
            {set_part}
            {remove_part}
            RETURN id(n) as node_id
            """

            result = self.graph.run(cypher, **params).data()

            if result:
                print(f"✅ Neo4j属性更新成功: ID={node_id}, Props={list(props.keys())}")
                return True
            else:
                print(f"⚠️ Neo4j属性更新失败: 未找到节点 {node_id}")
                return False

        except Exception as e:
            print(f"❌ Neo4j属性更新异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    # 删除节点
    def delete_node(self, label, node_id):
        """使用 Cypher 删除节点"""
        try:
            cypher = f"""
            MATCH (n:`{label}`)
            WHERE id(n) = $node_id
            DETACH DELETE n
            """
            result = self.graph.run(cypher, node_id=node_id)
            print(f"✅ Neo4j节点删除成功: ID={node_id}")
            return True

        except Exception as e:
            print(f"❌ Neo4j节点删除异常: {e}")
            raise

    def get_node_types(self):
        """
        获取系统中所有节点的类型（标签）

        :return: 排序后的节点类型列表
        """

        # 构造Cypher查询语句
        # 查询所有节点的标签，并去重
        query = """
        MATCH (n)
        RETURN DISTINCT labels(n) AS types
        """

        # 执行查询
        results = self.graph.run(query)

        # 存储节点类型列表
        node_types = []

        # 遍历查询结果
        for record in results:

            # 判断当前记录是否包含标签
            if record['types']:
                # 获取第一个标签作为节点主类型
                node_types.append(record['types'][0])

        # 对节点类型进行排序后返回
        return sorted(node_types)

    def get_relationship_types(self):
        """获取所有关系类型"""
        query = """
        MATCH ()-[r]-()
        RETURN DISTINCT type(r) as type
        """
        print("执行cypher查询1")
        results = self.graph.run(query)
        rel_types = []
        for record in results:
            if record['type']:
                rel_types.append(record['type'])
        return sorted(rel_types)

    def get_relationship_types_by_Event(self):
        """根据节点类型Event获取关系类型"""

        query = f"""
        MATCH (n:Event)-[r]-(m:Event)
        RETURN DISTINCT type(r) AS type
        ORDER BY type
        """
        results = self.graph.run(query)
        print("执行cypher查询")
        rel_types = []
        for record in results:
            if record['type']:
                rel_types.append(record['type'])
        return sorted(rel_types)


    def get_node_relations(self, node_id):
        """
        获取节点的所有直接关系
        :param node_id: 节点ID
        :return: 与节点直接相关的节点和关系
        """
        sql = f"""
        MATCH (n)-[r]-(m)
        WHERE id(n) = {node_id}
        RETURN n, r, m
        """
        result = self.graph.run(sql).data()
        
        nodes = []
        lines = []
        node_ids = set()
        
        # 加入中心节点
        center_node_sql = f"""
        MATCH (n)
        WHERE id(n) = {node_id}
        RETURN n
        """
        center_result = self.graph.run(center_node_sql).data()
        if center_result:
            center_node = center_result[0]['n']
            node_data = {
                'id': center_node.identity,
                'name': center_node['name'],
                'type': list(center_node.labels)[0] if center_node.labels else ''
            }
            nodes.append(node_data)
            node_ids.add(center_node.identity)
        
        for record in result:
            # 添加关联节点
            rel_node = record['m']
            if rel_node.identity not in node_ids:
                node_data = {
                    'id': rel_node.identity,
                    'name': rel_node['name'],
                    'type': list(rel_node.labels)[0] if rel_node.labels else ''
                }
                nodes.append(node_data)
                node_ids.add(rel_node.identity)
            
            # 添加关系
            rel = record['r']
            rel_type = type(rel).__name__
            
            # 构建关系数据
            line_data = {
                'from': rel.start_node.identity,
                'to': rel.end_node.identity,
                'text': rel_type
            }
            
            # 添加关系属性，包括关系类型
            for key, value in rel.items():
                line_data[key] = value
                
            # 特别处理关系类型属性
            if 'relation_type' in rel:
                line_data['relation_category'] = rel['relation_type']
            
            lines.append(line_data)
        
        return {"nodes": nodes, "lines": lines}
        
    def get_nodes_by_type(self, node_type):
        """
        根据节点类型获取节点列表
        :param node_type: 节点类型
        :return: 节点列表
        """
        sql = f"""
        MATCH (n:{node_type})
        RETURN n
        LIMIT 100
        """
        
        result = self.graph.run(sql).data()
        
        if not result:
            return {"nodes": [], "lines": []}
            
        nodes = []
        node_ids = set()
        
        for record in result:
            node = record['n']
            node_id = node.identity
            if node_id not in node_ids:
                node_data = {
                    'id': node_id,
                    'name': node['name'],
                    'type': node_type
                }
                nodes.append(node_data)
                node_ids.add(node_id)
            
        return {"nodes": nodes, "lines": []}
        
    def get_nodes_by_relationship(self, rel_type):
        """
        根据关系类型获取所有相关节点和关系
        :param rel_type: 关系类型
        :return: 相关节点和关系
        """
        rel_types = relationship_type_aliases(rel_type)
        sql = f"""
        MATCH (n)-[r]-(m)
        WHERE type(r) IN $rel_types
        RETURN n, r, m
        LIMIT 100
        """
        
        result = self.graph.run(sql, rel_types=rel_types).data()
        
        if not result:
            return {"nodes": [], "lines": []}
            
        nodes = []
        lines = []
        node_ids = set()
        
        for record in result:
            # 添加源节点
            source_node = record['n']
            if source_node.identity not in node_ids:
                source_data = {
                    'id': source_node.identity,
                    'name': source_node['name'],
                    'type': list(source_node.labels)[0] if source_node.labels else ''
                }
                nodes.append(source_data)
                node_ids.add(source_node.identity)
                
            # 添加目标节点
            target_node = record['m']
            if target_node.identity not in node_ids:
                target_data = {
                    'id': target_node.identity,
                    'name': target_node['name'],
                    'type': list(target_node.labels)[0] if target_node.labels else ''
                }
                nodes.append(target_data)
                node_ids.add(target_node.identity)
                
            # 添加关系
            rel = record['r']
            rel_type_name = type(rel).__name__
            
            # 构建关系数据
            line_data = {
                'from': rel.start_node.identity,
                'to': rel.end_node.identity,
                'text': rel_type_name
            }
            
            # 添加关系属性，包括关系类型
            for key, value in rel.items():
                line_data[key] = value
                
            # 特别处理关系类型属性
            if 'relation_type' in rel:
                line_data['relation_category'] = rel['relation_type']
            
            lines.append(line_data)
            
        return {"nodes": nodes, "lines": lines}

    def search_nodes_by_name(self, search_text, limit=100):
        if not search_text:
            return {"nodes": [], "lines": []}

        try:
            search_text = search_text.replace("'", "\\'")

            # 不限制标签类型，按名称模糊查询所有节点
            cypher_query = f"""
            MATCH (n)
            WHERE toLower(n.name) CONTAINS toLower('{search_text}') 
            RETURN n, labels(n) as labs, id(n) as nid
            LIMIT {limit}
            """

            result = self.graph.run(cypher_query).data()

            nodes = []
            for record in result:
                node = record['n']
                labels = record['labs']

                node_data = {
                    'id': record['nid'],
                    'name': node.get('name', ''),
                    'type': labels[0] if labels else 'Unknown',
                    'labels': labels  # 返回所有标签
                }

                # 添加其他属性
                for key, value in node.items():
                    if key != 'name':
                        node_data[key] = value

                nodes.append(node_data)

            print(f"搜索 '{search_text}' 找到 {len(nodes)} 个匹配节点")
            return {"nodes": nodes, "lines": []}

        except Exception as e:
            print(f"节点名称搜索异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"nodes": [], "lines": []}

    def get_default_graph(self, limit=50, load_all=False):
        """
        获取默认图谱数据（用于可视化初始化）
        :param limit: 返回的节点数量限制
        :param load_all: 是否加载所有节点和关系，不进行限制
        :return: 包含节点和关系的图谱数据
        """
        try:
            # 存储节点和关系
            nodes = []
            lines = []
            # 用于记录节点ID，避免重复
            node_ids = set()

            # ============ 加载全部图谱 ============
            if load_all:
                # 使用一种更可靠的方法保证连通性
                # 1. 先获取所有节点
                node_query = """
                MATCH (n) 
                RETURN n
                """
                node_result = self.graph.run(node_query).data()
                
                # 处理所有节点数据
                for record in node_result:
                    node = record['n']
                    node_id = node.identity
                    
                    # 构造节点数据格式
                    node_data = {
                        'id': node_id,
                        'name': node['name'],
                        'type': list(node.labels)[0] if node.labels else ''
                    }
                    
                    # 添加其他属性
                    for prop in node:
                        if prop != 'name':  # 名称已添加
                            node_data[prop] = node[prop]
                    
                    nodes.append(node_data)
                    node_ids.add(node_id)
                
                # 2. 获取所有关系 - 确保使用路径查询而非直接关系查询
                path_query = """
                MATCH path = (n)-[r*1..1]-(m)
                RETURN relationships(path) as rels
                """
                path_result = self.graph.run(path_query).data()
                
                # 添加所有关系
                processed_relations = set()  # 用于去重
                # 处理关系数据
                for record in path_result:
                    for rel in record['rels']:
                        # 构造关系唯一标识
                        rel_id = f"{rel.start_node.identity}-{rel.end_node.identity}-{type(rel).__name__}"
                        
                        # 避免重复添加相同关系
                        if rel_id in processed_relations:
                            continue
                            
                        processed_relations.add(rel_id)
                        
                        rel_type = type(rel).__name__
                        
                        # 构建关系数据
                        line_data = {
                            'from': rel.start_node.identity,
                            'to': rel.end_node.identity,
                            'text': rel_type
                        }
                        
                        # 添加关系属性
                        for key, value in rel.items():
                            line_data[key] = value
                            
                        # 特别处理关系类型属性
                        if 'relation_type' in rel:
                            line_data['relation_category'] = rel['relation_type']
                        
                        lines.append(line_data)
            else:
                node_query = f"""
                MATCH (n)
                RETURN n
                LIMIT {limit}
                """
                
                node_result = self.graph.run(node_query).data()
                
                if not node_result:
                    return {"nodes": [], "lines": []}
                    
                # 处理节点数据
                for record in node_result:
                    node = record['n']
                    node_id = node.identity
                    
                    # 添加节点数据
                    node_data = {
                        'id': node_id,
                        'name': node['name'],
                        'type': list(node.labels)[0] if node.labels else ''
                    }
                    
                    # 添加其他属性
                    for prop in node:
                        if prop != 'name':  # 名称已添加
                            node_data[prop] = node[prop]
                    
                    nodes.append(node_data)
                    node_ids.add(node_id)
                
                # 获取这些节点之间的关系
                if node_ids:
                    # 限制关系数量
                    node_id_list = ", ".join(map(str, node_ids))
                    relation_query = f"""
                    MATCH (n)-[r]-(m)
                    WHERE id(n) IN [{node_id_list}] AND id(m) IN [{node_id_list}]
                    RETURN r
                    LIMIT {limit*2}
                    """
                    relation_result = self.graph.run(relation_query).data()
                    
                    # 处理关系数据
                    for record in relation_result:
                        rel = record['r']
                        rel_type = type(rel).__name__
                        
                        # 构建关系数据
                        line_data = {
                            'from': rel.start_node.identity,
                            'to': rel.end_node.identity,
                            'text': rel_type
                        }
                        
                        # 添加关系属性
                        for key, value in rel.items():
                            line_data[key] = value
                            
                        # 特别处理关系类型属性
                        if 'relation_type' in rel:
                            line_data['relation_category'] = rel['relation_type']
                        
                        lines.append(line_data)
            
            print(f"图谱加载: {len(nodes)}个节点, {len(lines)}个关系, {'加载全部' if load_all else '加载部分'}")
            return {"nodes": nodes, "lines": lines}
            
        except Exception as e:
            print(f"获取默认图谱数据异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"nodes": [], "lines": []}



# ============================添加代码==========
    def search_by_name_and_type(self, name, node_type, limit=100):
        """
        按 节点名称 + 节点类型 查询关联子图
        语义：先找到 name，再找它关联的 node_type
        返回：中心节点 + 关联节点 + 关系
        """

        try:
            # ---------- 参数校验 ----------
            if not name or not node_type:
                print("名称或节点类型为空，返回空结果")
                return {"nodes": [], "lines": []}

            # ---------- 防止注入 ----------
            name = name.replace("'", "\\'")

            # ---------- 构造 Cypher ----------
            cypher = f"""
            MATCH (center)
            WHERE toLower(center.name) CONTAINS toLower('{name}')

            MATCH (center)-[r]-(m:{node_type})

            RETURN center, r, m
            LIMIT {limit}
            """

            # ---------- 执行查询 ----------
            result = self.graph.run(cypher).data()

            # ---------- 无结果直接返回 ----------
            if not result:
                print(f"未找到匹配结果：{name} + {node_type}")
                return {"nodes": [], "lines": []}

            nodes = []
            lines = []
            node_ids = set()

            # ---------- 构建子图 ----------
            for record in result:

                center = record['center']  # 中心节点
                m = record['m']  # 关联节点
                r = record['r']  # 关系

                # ===== 添加中心节点 =====
                if center.identity not in node_ids:

                    center_data = {
                        'id': center.identity,
                        'name': center.get('name', ''),
                        'type': list(center.labels)[0] if center.labels else ''
                    }

                    for prop in center:
                        if prop != 'name':
                            center_data[prop] = center[prop]

                    nodes.append(center_data)
                    node_ids.add(center.identity)

                # ===== 添加目标节点 =====
                if m.identity not in node_ids:

                    m_data = {
                        'id': m.identity,
                        'name': m.get('name', ''),
                        'type': list(m.labels)[0] if m.labels else ''
                    }

                    for prop in m:
                        if prop != 'name':
                            m_data[prop] = m[prop]

                    nodes.append(m_data)
                    node_ids.add(m.identity)

                # ===== 添加关系 =====
                rel_type = type(r).__name__

                line_data = {
                    'from': r.start_node.identity,
                    'to': r.end_node.identity,
                    'text': rel_type
                }

                for k, v in r.items():
                    line_data[k] = v

                lines.append(line_data)

            print(f"名称+类型组合查询成功：{name} + {node_type}，节点数 {len(nodes)}")

            return {
                "nodes": nodes,
                "lines": lines
            }

        # ---------- 异常 ----------
        except Exception as e:

            print("search_by_name_and_type 出错：", str(e))

            import traceback
            traceback.print_exc()

            return {
                "nodes": [],
                "lines": []
            }

    def search_by_name_and_relation(self, name, rel_type, limit=100):
        """
        按 节点名称 + 关系类型 查询关联子图
        语义：先找到 name，再找指定关系
        如果无结果，返回空图
        """

        try:
            # ---------- 参数清洗 ----------
            if not name or not rel_type:
                print("名称或关系类型为空，返回空结果")
                return {"nodes": [], "lines": []}

            rel_types = relationship_type_aliases(rel_type)

            # ---------- 构造 Cypher ----------
            cypher = f"""
            MATCH (center)
            WHERE toLower(center.name) CONTAINS toLower($name)

            MATCH (center)-[r]-(m)
            WHERE type(r) IN $rel_types

            RETURN center, r, m
            LIMIT {limit}
            """

            # ---------- 执行查询 ----------
            result = self.graph.run(cypher, name=name, rel_types=rel_types).data()

            # ---------- 没有关系直接返回 ----------
            if not result:
                print(f"未找到关系：{name} + {rel_type}")
                return {"nodes": [], "lines": []}

            nodes = []
            lines = []
            node_ids = set()

            # ---------- 构建子图 ----------
            for record in result:

                center = record['center']
                m = record['m']
                r = record['r']

                # ===== 中心节点 =====
                if center.identity not in node_ids:

                    center_data = {
                        'id': center.identity,
                        'name': center.get('name', ''),
                        'type': list(center.labels)[0] if center.labels else ''
                    }

                    for prop in center:
                        if prop != 'name':
                            center_data[prop] = center[prop]

                    nodes.append(center_data)
                    node_ids.add(center.identity)

                # ===== 关联节点 =====
                if m.identity not in node_ids:

                    m_data = {
                        'id': m.identity,
                        'name': m.get('name', ''),
                        'type': list(m.labels)[0] if m.labels else ''
                    }

                    for prop in m:
                        if prop != 'name':
                            m_data[prop] = m[prop]

                    nodes.append(m_data)
                    node_ids.add(m.identity)

                # ===== 关系 =====
                line_data = {
                    'from': r.start_node.identity,
                    'to': r.end_node.identity,
                    'text': rel_type
                }

                for k, v in r.items():
                    line_data[k] = v

                lines.append(line_data)

            print(f"名称+关系查询成功：{name} + {rel_type}，节点数 {len(nodes)}")

            return {
                "nodes": nodes,
                "lines": lines
            }

        # ---------- 异常兜底 ----------
        except Exception as e:

            print("search_by_name_and_relation 出错：", str(e))

            import traceback
            traceback.print_exc()

            # 出异常时也保证前端不炸
            return {
                "nodes": [],
                "lines": []
            }

    def get_event_event_relations(self, name_filter='', rel_type=''):
        try:
            where_conditions = []
            params = {}
            if name_filter:
                where_conditions.append(
                    "(toLower(e1.name) CONTAINS toLower($name_filter) OR toLower(e2.name) CONTAINS toLower($name_filter))")
                params["name_filter"] = name_filter

            rel_match = "-[r]-"
            if rel_type:
                where_conditions.append("type(r) IN $rel_types")
                params["rel_types"] = relationship_type_aliases(rel_type)
            where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
            isolated_name_filter = "AND toLower(e.name) CONTAINS toLower($name_filter)" if name_filter else ""

            cypher = f"""
            MATCH (e1:Event){rel_match}(e2:Event)
            {where_clause}
            RETURN e1, e2, r
            LIMIT 200

            UNION

            MATCH (e:Event)
            WHERE NOT (e)-[]-(:Event)
            {isolated_name_filter}
            RETURN null as e1, e as e2, null as r
            LIMIT 50
            """

            result = self.graph.run(cypher, **params).data()

            nodes = []
            lines = []
            node_ids = set()

            for record in result:
                e1 = record.get('e1')
                e2 = record.get('e2')
                r = record.get('r')

                if e1 and e1.identity not in node_ids:
                    node_data = {
                        'id': e1.identity,
                        'name': e1.get('name', ''),
                        'type': 'Event'
                    }
                    for prop in e1:
                        if prop != 'name':
                            node_data[prop] = e1[prop]
                    nodes.append(node_data)
                    node_ids.add(e1.identity)

                if e2 and e2.identity not in node_ids:
                    node_data = {
                        'id': e2.identity,
                        'name': e2.get('name', ''),
                        'type': 'Event'
                    }
                    for prop in e2:
                        if prop != 'name':
                            node_data[prop] = e2[prop]
                    nodes.append(node_data)
                    node_ids.add(e2.identity)

                if r:
                    rel_type_name = type(r).__name__
                    line_data = {
                        'from': r.start_node.identity,
                        'to': r.end_node.identity,
                        'text': rel_type_name
                    }
                    for k, v in r.items():
                        line_data[k] = v
                    lines.append(line_data)

            return {"nodes": nodes, "lines": lines}

        except Exception as e:
            print(f"获取事件-事件关系异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"nodes": [], "lines": []}

    def get_event_organization_relations(self, name_filter='', rel_type=''):
        try:
            where_conditions = []
            if name_filter:
                name_filter = name_filter.replace("'", "\\'")
                where_conditions.append(
                    f"(toLower(e.name) CONTAINS toLower('{name_filter}') OR toLower(o.name) CONTAINS toLower('{name_filter}'))")

            rel_match = f"-[r:{rel_type}]-" if rel_type else "-[r]-"
            where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

            cypher = f"""
            MATCH (e:Event){rel_match}(o:Organization)
            {where_clause}
            RETURN e, o, r
            LIMIT 200

            UNION

            MATCH (o:Organization)
            WHERE NOT (o)-[]-(:Event)
            {f"AND toLower(o.name) CONTAINS toLower('{name_filter}')" if name_filter else ""}
            RETURN null as e, o, null as r
            LIMIT 50
            """

            result = self.graph.run(cypher).data()

            nodes = []
            lines = []
            node_ids = set()

            for record in result:
                e = record.get('e')
                o = record.get('o')
                r = record.get('r')

                if e and e.identity not in node_ids:
                    node_data = {
                        'id': e.identity,
                        'name': e.get('name', ''),
                        'type': 'Event'
                    }
                    for prop in e:
                        if prop != 'name':
                            node_data[prop] = e[prop]
                    nodes.append(node_data)
                    node_ids.add(e.identity)

                if o and o.identity not in node_ids:
                    node_data = {
                        'id': o.identity,
                        'name': o.get('name', ''),
                        'type': 'Organization'
                    }
                    for prop in o:
                        if prop != 'name':
                            node_data[prop] = o[prop]
                    nodes.append(node_data)
                    node_ids.add(o.identity)

                if r:
                    rel_type_name = type(r).__name__
                    line_data = {
                        'from': r.start_node.identity,
                        'to': r.end_node.identity,
                        'text': rel_type_name
                    }
                    for k, v in r.items():
                        line_data[k] = v
                    lines.append(line_data)

            return {"nodes": nodes, "lines": lines}

        except Exception as e:
            print(f"获取事件-组织关系异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"nodes": [], "lines": []}

    def get_event_person_relations(self, name_filter='', rel_type=''):
        try:
            where_conditions = []
            if name_filter:
                name_filter = name_filter.replace("'", "\\'")
                where_conditions.append(
                    f"(toLower(e.name) CONTAINS toLower('{name_filter}') OR toLower(p.name) CONTAINS toLower('{name_filter}'))")

            rel_match = f"-[r:{rel_type}]-" if rel_type else "-[r]-"
            where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

            cypher = f"""
            MATCH (e:Event){rel_match}(p:Person)
            {where_clause}
            RETURN e, p, r
            LIMIT 200

            UNION

            MATCH (p:Person)
            WHERE NOT (p)-[]-(:Event)
            {f"AND toLower(p.name) CONTAINS toLower('{name_filter}')" if name_filter else ""}
            RETURN null as e, p, null as r
            LIMIT 50
            """

            result = self.graph.run(cypher).data()

            nodes = []
            lines = []
            node_ids = set()

            for record in result:
                e = record.get('e')
                p = record.get('p')
                r = record.get('r')

                if e and e.identity not in node_ids:
                    node_data = {
                        'id': e.identity,
                        'name': e.get('name', ''),
                        'type': 'Event'
                    }
                    for prop in e:
                        if prop != 'name':
                            node_data[prop] = e[prop]
                    nodes.append(node_data)
                    node_ids.add(e.identity)

                if p and p.identity not in node_ids:
                    node_data = {
                        'id': p.identity,
                        'name': p.get('name', ''),
                        'type': 'Person'
                    }
                    for prop in p:
                        if prop != 'name':
                            node_data[prop] = p[prop]
                    nodes.append(node_data)
                    node_ids.add(p.identity)

                if r:
                    rel_type_name = type(r).__name__
                    line_data = {
                        'from': r.start_node.identity,
                        'to': r.end_node.identity,
                        'text': rel_type_name
                    }
                    for k, v in r.items():
                        line_data[k] = v
                    lines.append(line_data)

            return {"nodes": nodes, "lines": lines}

        except Exception as e:
            print(f"获取事件-人物关系异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"nodes": [], "lines": []}

    def get_event_place_relations(self, name_filter='', rel_type=''):
        """
        获取事件-地点关系图（包含孤立的地点节点）
        """
        try:
            where_conditions = []
            params = {}
            if name_filter:
                where_conditions.append(
                    "(toLower(e.name) CONTAINS toLower($name_filter) OR toLower(p.name) CONTAINS toLower($name_filter))")
                params["name_filter"] = name_filter

            rel_match = "-[r]-"
            if rel_type:
                where_conditions.append("type(r) IN $rel_types")
                params["rel_types"] = relationship_type_aliases(rel_type)
            where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
            isolated_name_filter = "AND toLower(p.name) CONTAINS toLower($name_filter)" if name_filter else ""

            #使用 UNION 合并：1有关联的节点 2孤立的地点节点
            cypher = f"""
            // 第一部分：有关联的 Event-Place
            MATCH (e:Event){rel_match}(p:Place)
            {where_clause}
            RETURN e, p, r, 'connected' as node_status
            LIMIT 200

            UNION

            // 第二部分：孤立的 Place 节点（没有关系）
            MATCH (p:Place)
            WHERE NOT (p)-[]-(:Event)
            {isolated_name_filter}
            RETURN null as e, p, null as r, 'isolated' as node_status
            LIMIT 50
            """

            result = self.graph.run(cypher, **params).data()

            nodes = []
            lines = []
            node_ids = set()

            for record in result:
                e = record.get('e')
                p = record.get('p')
                r = record.get('r')

                # 添加事件节点（如果存在）
                if e and e.identity not in node_ids:
                    node_data = {
                        'id': e.identity,
                        'name': e.get('name', ''),
                        'type': 'Event'
                    }
                    for prop in e:
                        if prop != 'name':
                            node_data[prop] = e[prop]
                    nodes.append(node_data)
                    node_ids.add(e.identity)

                # 添加地点节点（孤立或有关系的都添加）
                if p and p.identity not in node_ids:
                    node_data = {
                        'id': p.identity,
                        'name': p.get('name', ''),
                        'type': 'Place'
                    }
                    for prop in p:
                        if prop != 'name':
                            node_data[prop] = p[prop]
                    nodes.append(node_data)
                    node_ids.add(p.identity)

                # 添加关系（只有存在时才添加）
                if r:
                    rel_type_name = type(r).__name__
                    line_data = {
                        'from': r.start_node.identity,
                        'to': r.end_node.identity,
                        'text': rel_type_name
                    }
                    for k, v in r.items():
                        line_data[k] = v
                    lines.append(line_data)

            print(f"地点图谱: {len(nodes)} 个节点, {len(lines)} 条关系")
            return {"nodes": nodes, "lines": lines}

        except Exception as e:
            print(f"获取事件-地点关系异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"nodes": [], "lines": []}
