/**
 * Web Worker 用于处理轨迹数据的解压和解析
 * 避免主线程卡顿，提高用户体验
 */

// 导入 pako 解压库
self.importScripts('https://unpkg.com/pako@2.1.0/dist/pako.min.js');

/**
 * 处理数据解压和解析
 */
function processTrackData(arrayBuffer, isGzip = true) {
    try {
        let jsonData;
        
        if (isGzip) {
            // 解压 gzip 数据
            const compressedData = new Uint8Array(arrayBuffer);
            const decompressedData = pako.inflate(compressedData, { to: 'string' });
            jsonData = JSON.parse(decompressedData);
        } else {
            // 直接解析 JSON
            const textDecoder = new TextDecoder();
            const jsonString = textDecoder.decode(arrayBuffer);
            jsonData = JSON.parse(jsonString);
        }
        
        return jsonData;
    } catch (error) {
        throw new Error(`数据处理失败: ${error.message}`);
    }
}

/**
 * 优化轨迹数据结构，移除冗余字段
 */
function optimizeTrackData(data) {
    const optimized = {
        metrics: data.metrics,
        bounds: data.bounds,
        tracks: {},
        generated_at: data.generated_at,
        version: data.version
    };
    
    // 优化每个轨迹类型的数据
    Object.entries(data.tracks).forEach(([trackType, trackData]) => {
        optimized.tracks[trackType] = {
            color: trackData.color,
            display_type: trackData.display_type,
            points_count: trackData.points_count,
            lines_count: trackData.lines_count
        };
        
        // 只保留渲染必需的字段
        if (trackData.points && trackData.points.features) {
            optimized.tracks[trackType].points = {
                features: trackData.points.features.map(feature => ({
                    geometry: {
                        coordinates: feature.geometry.coordinates
                    },
                    properties: feature.properties ? {
                        timestamp: feature.properties.timestamp
                    } : {}
                }))
            };
        }
        
        if (trackData.lines && trackData.lines.features) {
            optimized.tracks[trackType].lines = {
                features: trackData.lines.features.map(feature => ({
                    geometry: {
                        coordinates: feature.geometry.coordinates
                    },
                    properties: feature.properties ? {
                        start_time: feature.properties.start_time,
                        end_time: feature.properties.end_time
                    } : {}
                }))
            };
        }
    });
    
    return optimized;
}

/**
 * 基于视窗范围过滤轨迹点（虚拟化渲染）
 */
function filterTracksByBounds(data, bounds, zoomLevel) {
    if (!bounds) return data;
    
    const filtered = { ...data };
    
    // 根据缩放级别调整采样率
    const sampleRate = zoomLevel < 4 ? 0.1 : zoomLevel < 6 ? 0.3 : 1;
    
    Object.entries(data.tracks).forEach(([trackType, trackData]) => {
        filtered.tracks[trackType] = { ...trackData };
        
        // 过滤点数据
        if (trackData.points && trackData.points.features) {
            const filteredPoints = trackData.points.features.filter((feature, index) => {
                const [lng, lat] = feature.geometry.coordinates;
                const inBounds = lng >= bounds.west && lng <= bounds.east && 
                                lat >= bounds.south && lat <= bounds.north;
                const sampled = Math.random() < sampleRate;
                return inBounds && sampled;
            });
            
            filtered.tracks[trackType].points = {
                features: filteredPoints
            };
        }
        
        // 过滤线数据
        if (trackData.lines && trackData.lines.features) {
            const filteredLines = trackData.lines.features.filter(feature => {
                // 检查线段是否与视窗相交
                const coords = feature.geometry.coordinates;
                return coords.some(([lng, lat]) => 
                    lng >= bounds.west && lng <= bounds.east && 
                    lat >= bounds.south && lat <= bounds.north
                );
            });
            
            filtered.tracks[trackType].lines = {
                features: filteredLines
            };
        }
    });
    
    return filtered;
}

// 监听主线程消息
self.addEventListener('message', async function(e) {
    const { type, data, messageId } = e.data;
    
    try {
        switch (type) {
            case 'PROCESS_TRACK_DATA':
                const { arrayBuffer, isGzip, optimize } = data;
                let processedData = processTrackData(arrayBuffer, isGzip);
                
                if (optimize) {
                    processedData = optimizeTrackData(processedData);
                }
                
                self.postMessage({
                    type: 'TRACK_DATA_PROCESSED',
                    messageId,
                    data: processedData,
                    success: true
                });
                break;
                
            case 'FILTER_TRACKS_BY_BOUNDS':
                const { tracksData, bounds, zoomLevel } = data;
                const filteredData = filterTracksByBounds(tracksData, bounds, zoomLevel);
                
                self.postMessage({
                    type: 'TRACKS_FILTERED',
                    messageId,
                    data: filteredData,
                    success: true
                });
                break;
                
            default:
                throw new Error(`未知的消息类型: ${type}`);
        }
    } catch (error) {
        self.postMessage({
            type: 'ERROR',
            messageId,
            error: error.message,
            success: false
        });
    }
});

console.log('📦 Data Worker 已启动');