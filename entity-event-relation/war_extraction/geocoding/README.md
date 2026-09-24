# 地理编码工具

用于处理历史地名到现代坐标的映射，解决历史地图视图中的行军路线显示问题。

## 功能概述

1. **导出待编码地点** - 从数据库导出没有坐标的地点
2. **高德API编码** - 调用高德地图API进行地理编码
3. **人工审核** - 审核编码结果，确保准确性
4. **导入数据库** - 将审核通过的坐标写回数据库

## 文件结构

```
geocoding/
├── __init__.py                    # 包初始化
├── README.md                      # 本文件
├── historical_places_mapping.py   # 历史地名映射表
├── export_unmapped_places.py      # 导出待编码地点
├── geocode_amap.py                # 高德API编码
├── review_geocoding.py            # 人工审核
├── import_coordinates.py          # 导入数据库
└── main.py                        # 主程序（协调流程）
```

## 使用方法

### 前置准备

1. **申请高德API Key**
   - 访问：https://console.amap.com/
   - 注册账号并实名认证
   - 创建应用 → 添加Key → 选择"Web服务"
   - 免费额度：个人开发者 5000次/天

2. **设置API Key**

   ```bash
   # 方式1：环境变量（推荐）
   export AMAP_API_KEY="your_api_key"

   # 方式2：命令行参数
   --api-key your_api_key
   ```

### 方式一：运行完整流程

```bash
# 进入项目目录
cd <项目根>/entity-event-relation/src

# 运行完整流程
python -m geocoding.main pipeline --api-key YOUR_API_KEY

# 指定数据库路径
python -m geocoding.main pipeline --api-key YOUR_API_KEY --db-path ../backend/database

# 批量审核模式（基于置信度自动审核）
python -m geocoding.main pipeline --api-key YOUR_API_KEY --mode batch
```

### 方式二：分步执行

#### 步骤1：导出待编码地点

```bash
python -m geocoding.main export

# 指定数据库路径
python -m geocoding.main export --db-path ../backend/database
```

输出文件：`unmapped_places_YYYYMMDD_HHMMSS.json`

#### 步骤2：调用高德API编码

```bash
python -m geocoding.main geocode unmapped_places_YYYYMMDD_HHMMSS.json --api-key YOUR_API_KEY
```

输出文件：`geocoded_results_YYYYMMDD_HHMMSS.json`

#### 步骤3：人工审核

```bash
# 交互式审核（推荐）
python -m geocoding.main review geocoded_results_YYYYMMDD_HHMMSS.json

# 批量审核
python -m geocoding.main review geocoded_results_YYYYMMDD_HHMMSS.json --mode batch
```

输出文件：`approved_coordinates_YYYYMMDD_HHMMSS.json`

#### 步骤4：导入数据库

```bash
python -m geocoding.main import approved_coordinates_YYYYMMDD_HHMMSS.json

# 指定数据库路径
python -m geocoding.main import approved_coordinates_YYYYMMDD_HHMMSS.json --db-path ../backend/database
```

### 方式三：直接调用Python模块

```python
from geocoding import export_unmapped_places, AmapGeocoder, review_results, import_coordinates

# 1. 导出待编码地点
output_file = export_unmapped_places()

# 2. 编码
results = load_and_geocode(output_file, 'YOUR_API_KEY')

# 3. 审核
approved = review_results('geocoded_results.json', mode='interactive')

# 4. 导入
import_coordinates('approved_coordinates.json')
```

## 历史地名映射

`historical_places_mapping.py` 包含常见历史地名到现代地名的映射，包括：

- 两汉时期地名（长安、洛阳、宛城等）
- 三国时期地名（建业、建康、江陵等）
- 两晋南北朝地名（平城、姑臧、高昌等）
- 隋唐时期地名（大兴城、东都、太原等）
- 五代十国地名
- 宋元时期地名（东京、临安、大都等）
- 历史区域名称（汉水、荆楚、淮河流域等）

### 添加新映射

如需添加新的历史地名映射，编辑 `historical_places_mapping.py`：

```python
HISTORICAL_MAPPING = {
    # 添加新的映射
    "新历史地名": "现代地名",
    ...
}
```

## 坐标系说明

- **高德地图**：使用 GCJ-02 坐标系（国测局坐标）
- **百度地图**：使用 BD-09 坐标系
- **腾讯地图**：使用 GCJ-02 坐标系

本工具默认使用高德API，坐标系为 GCJ-02。

## 数据库字段说明

导入后，`places` 表将更新以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| longitude | FLOAT | 经度（GCJ-02） |
| latitude | FLOAT | 纬度（GCJ-02） |
| coord_source | VARCHAR(100) | 坐标来源（如 'amap'） |
| coord_confidence | VARCHAR(50) | 置信度（high/medium/low） |
| coord_note | TEXT | 备注 |

## 注意事项

1. **请求频率**：高德API限制 100次/秒，工具默认间隔 0.05秒
2. **免费额度**：个人开发者 5000次/天
3. **坐标范围**：工具会检查坐标是否在中国范围内（73-135°E, 18-54°N）
4. **数据备份**：导入前建议备份数据库
5. **重启后端**：导入完成后需要重启后端服务，刷新地图页面

## 常见问题

### Q: 为什么有些地名编码失败？

A: 可能的原因：
- 历史地名在映射表中不存在
- 地名过于冷僻，高德无法识别
- 地名对应的现代地名已变更

解决方案：
1. 在 `historical_places_mapping.py` 中添加映射
2. 使用人工审核功能手动输入坐标
3. 查阅历史地理资料确认现代位置

### Q: 如何验证坐标是否正确？

A: 可以使用以下方式：
1. 在高德地图中搜索坐标点
2. 使用交互式审核模式，逐个确认
3. 对比历史地图资料

### Q: 导入后地图没有更新？

A: 需要：
1. 重启后端服务
2. 刷新前端页面
3. 清除浏览器缓存（如有必要）
