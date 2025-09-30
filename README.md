# GPX 轨迹可视化项目 - 性能优化版

一个基于 Mapbox GL JS 和 deck.gl 的高性能轨迹可视化系统，支持大规模 GPX 数据的快速加载和渲染。


exploreworld足迹展示
演示页面：

https://ex-cat.pages.dev/

https://e.liyunjian.dpdns.org

足迹展示网页，模仿一生足迹APP的展示形式。

GPXtoJSON是GPX转成GEOJSON的python程序。

运行之后会生成cache文件夹

cache中

·metrics.json是记录足迹探索面积

config.js中需要Mapbox的API key用于加载地图。

## ✨ 新功能 - 性能优化

### 🚀 加载速度优化

1. **数据分片与渐进式加载**
   - 支持将大数据文件分片处理，每个分片独立加载
   - 渐进式渲染：分片加载完成即可开始渲染，用户体验更流畅
   - 智能分片大小控制，避免单次加载过大数据

2. **Web Worker 多线程处理**
   - 数据解压和解析在 Worker 线程中进行，避免主线程卡顿
   - 支持回退机制，Worker 不可用时自动使用主线程处理
   - 优化数据结构，移除渲染时的冗余字段

3. **虚拟化渲染**
   - 基于地图视窗动态过滤轨迹点，只渲染可见区域
   - 根据缩放级别智能采样，低缩放时显示较少点以提高性能
   - 地图交互（移动、缩放）后自动重新渲染可见区域

4. **图层优化**
   - deck.gl 图层使用 `updateTriggers` 优化重绘
   - 禁用不必要的交互功能（tooltip、点击）提高性能
   - 优化点/线的像素大小范围，避免过度渲染

## 项目特色

- 🚀 **高性能渲染**：使用 deck.gl WebGL 技术，支持海量轨迹点渲染
- ⚡ **智能加载**：Web Worker + 分片 + 虚拟化渲染，大幅提升加载速度
- 📦 **高效压缩**：Gzip 压缩 + 数据优化，减少 90%+ 文件大小
- 🌐 **跨日界线修复**：自动处理跨太平洋航线的显示问题
- 🎨 **多种轨迹类型**：支持道路（散点）、火车/飞机（线段）不同渲染方式
- 📱 **移动端优化**：渐进式加载，流畅的触摸交互，不阻塞用户操作
- ⚡ **渐进式加载**：先显示地图，轨迹分批加载，保证流畅体验

## 🛠️ 数据处理工具

### 集成化预处理脚本 (`GPXtoJSON/calculate_metrics.py`)

所有优化功能已集成到主预处理脚本中，一次运行即可完成数据处理、优化和分片：

```bash
# 基础用法（中等优化 + 0.5MB分片）
python calculate_metrics.py

# 高级优化（最大压缩）
python calculate_metrics.py -o high

# 自定义分片大小
python calculate_metrics.py -c 0.3

# 禁用分片（单文件模式）
python calculate_metrics.py --no-chunk

# 禁用Gzip压缩
python calculate_metrics.py --no-gzip

# 指定缓存目录
python calculate_metrics.py --cache-dir ../my_cache

# 组合使用
python calculate_metrics.py -o high -c 0.3
```

**优化级别说明：**
- `none`: 不进行数据优化
- `low`: 基础优化，保留更多精度
- `medium`: 中等优化，平衡性能与精度（默认）
- `high`: 高级优化，最大化性能，精度略有损失

## 📊 性能提升效果

### 加载速度提升
- **单文件模式**: 使用 Web Worker 后，大文件加载时页面不再卡顿
- **分片模式**: 用户可以更早看到部分轨迹，感知加载时间减少 50-70%
- **数据优化**: 文件大小通常减少 20-40%，网络传输时间相应缩短

### 渲染性能提升
- **虚拟化渲染**: 大数据集下帧率提升 2-5 倍
- **视窗过滤**: 内存使用减少，滚动和缩放更流畅
- **图层优化**: 减少不必要的重绘，交互响应更快

## 💡 使用建议

### 数据量 < 1MB
使用默认设置即可：
```bash
cd GPXtoJSON
python calculate_metrics.py
```

### 数据量 1-10MB  
使用中等优化 + 适当分片：
```bash
cd GPXtoJSON
python calculate_metrics.py -o medium -c 0.5
```

### 数据量 > 10MB
使用高级优化 + 小分片：
```bash
cd GPXtoJSON
python calculate_metrics.py -o high -c 0.3
```

### 开发调试
禁用压缩以便查看原始JSON：
```bash
cd GPXtoJSON
python calculate_metrics.py --no-gzip --no-chunk
```

## 🎯 技术特性

- **多种数据格式**: 支持 JSON 和 Gzip 压缩格式
- **智能配置**: 自动检测数据类型（单文件/分片）
- **性能监控**: 控制台输出详细的加载和渲染性能信息
- **错误处理**: 完善的错误处理和回退机制
- **渐进式增强**: 在支持的环境中启用高级特性，不支持时自动回退

## 🔧 配置文件格式

### 单文件模式
```json
{
  "data_type": "single",
  "single_file": "tracks_data.json.gz",
  "format": "gzip",
  "total_size_mb": 1.8
}
```

### 分片模式
```json
{
  "data_type": "chunked",
  "chunks": [
    "tracks_chunk_001.json.gz",
    "tracks_chunk_002.json.gz"
  ],
  "format": "gzip",
  "total_chunks": 2,
  "total_size_mb": 1.8
}
```

## 项目结构

```
Footprint/
├── index.html              # 主页面（已优化，支持 Web Worker + 虚拟化渲染）
├── data-worker.js           # Web Worker 数据处理线程
├── README.md               # 项目说明
├── cache/                  # 缓存目录
│   ├── metrics.json        # 探索面积统计
│   ├── tracks_data.json.gz # 压缩的轨迹数据（单文件模式）
│   ├── data_config.json    # 数据配置文件
│   └── tracks_chunk_*.json.gz # 分片文件（分片模式，可选）
└── GPXtoJSON/              # 数据处理脚本
    ├── calculate_metrics.py # GPX 数据预处理脚本（集成优化功能）
    └── GPX/                # GPX 原始文件目录
        ├── road/           # 道路轨迹
        ├── train/          # 火车轨迹
        ├── plane/          # 飞机轨迹
        └── other/          # 其他轨迹
```

## 技术栈

- **前端**：
  - Mapbox GL JS 2.15.0（地图底图）
  - deck.gl 8.9.0（WebGL 轨迹渲染）
  - pako 2.1.0（Gzip 解压）

- **后端**：
  - Python 3.x
  - gzip（数据压缩）
  - xml.etree.ElementTree（GPX 解析）

## 使用方法

### 1. 配置 Mapbox Token（重要！）

为了保护您的 Mapbox 访问令牌，请按以下步骤配置：

```bash
# 复制配置文件模板
cp config.js.example config.js

# 编辑 config.js，填入您的真实 Mapbox token
# window.CONFIG = {
#     MAPBOX_ACCESS_TOKEN: 'your-real-mapbox-token-here'
# }
```

⚠️ **安全提醒**：
- `config.js` 文件已添加到 `.gitignore`，不会被提交到版本控制
- 请不要将真实的 token 直接写在代码中
- 如需部署到服务器，建议使用环境变量

### 2. 数据预处理

```bash
cd GPXtoJSON
python calculate_metrics.py
```

### 3. 启动 Web 服务器

```bash
# 使用 Python 内置服务器
python -m http.server 8000

# 或使用 Node.js
npx serve .
```

### 4. 访问页面

打开浏览器访问：http://localhost:8000

## 轨迹类型配置

在 `calculate_metrics.py` 中可以配置不同轨迹类型的颜色和显示方式：

```python
GPX_TYPES = {
    'road': {'color': '#ef4444', 'display_type': 'points'},   # 红色散点
    'train': {'color': '#10b981', 'display_type': 'lines'},   # 绿色线段
    'plane': {'color': '#3b82f6', 'display_type': 'lines'},   # 蓝色线段
    'other': {'color': '#f59e0b', 'display_type': 'lines'}    # 橙色线段
}
```

## 性能优化

- **渐进式加载**：地图优先显示，轨迹分批渲染，不阻塞用户交互
- **移动端适配**：自动检测设备，降低渲染复杂度，优化触摸体验
- **Gzip 压缩**：文件大小减少 93.6%
- **分片加载**：超大文件自动分片（20MB 限制）
- **去重算法**：75m 网格加速去重
- **WebGL 渲染**：GPU 加速的点和线渲染
- **跨日界线修复**：避免轨迹绕地球显示

## 浏览器支持

- Chrome 60+
- Firefox 55+
- Safari 12+
- Edge 79+

需要支持 WebGL 和 ES6+ 语法。

## 许可证

GPLv3 License