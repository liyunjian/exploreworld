#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPX轨迹数据预处理脚本（性能优化版本）
功能：
1. 计算road目录下所有GPX文件的探索面积和百分比
2. 将所有类型的GPX轨迹转换为GeoJSON格式
3. 优化数据结构，移除冗余字段，提高加载性能
4. 智能数据分片，支持渐进式加载
5. 生成轨迹缓存文件供网页快速加载（支持JSON和Gzip格式）
结果保存为JSON或二进制文件供网页调用
"""

import os
import json
import gzip
import xml.etree.ElementTree as ET
import math
import glob
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

# 压缩格式支持
GZIP_AVAILABLE = True
print("✅ Gzip 压缩可用，将生成压缩的二进制缓存")

# 地球表面积（平方米）
EARTH_SURFACE_AREA = 510072000e6

# 文件大小限制（字节）
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

# 数据优化配置
OPTIMIZATION_LEVELS = {
    'none': {'coord_precision': 6, 'duplicate_tolerance': 0, 'simplify_tolerance': 0},
    'low': {'coord_precision': 6, 'duplicate_tolerance': 0.000001, 'simplify_tolerance': 0.00005},
    'medium': {'coord_precision': 5, 'duplicate_tolerance': 0.00001, 'simplify_tolerance': 0.0001},
    'high': {'coord_precision': 4, 'duplicate_tolerance': 0.0001, 'simplify_tolerance': 0.0005}
}

# 分片配置
CHUNK_CONFIG = {
    'max_chunk_size_mb': 0.5,  # 每个分片最大大小（MB）
    'min_chunks': 2,           # 最少分片数
    'max_chunks': 100          # 最多分片数（提高限制以支持更小分片）
}

# 轨迹类型配置
GPX_TYPES = {
    'road': {'color': '#ef4444', 'display_type': 'points'},
    'train': {'color': '#10b981', 'display_type': 'lines'},
    'plane': {'color': '#3b82f6', 'display_type': 'lines'},
    'other': {'color': '#f59e0b', 'display_type': 'lines'}
}

def haversine(lat1, lon1, lat2, lon2):
    """
    计算两点之间的哈弗辛距离（米）
    """
    R = 6371e3  # 地球半径（米）
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    
    a = (math.sin(dlat/2)**2 + 
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c

def optimize_coordinates(coordinates, precision=5):
    """优化坐标精度"""
    if not coordinates:
        return coordinates
    
    optimized = []
    for coord in coordinates:
        if isinstance(coord, (list, tuple)) and len(coord) >= 2:
            optimized_coord = [
                round(coord[0], precision),
                round(coord[1], precision)
            ]
            if len(coord) > 2:
                optimized_coord.append(round(coord[2], precision))
            optimized.append(optimized_coord)
        else:
            optimized.append(coord)
    return optimized

def remove_duplicate_points(coordinates, tolerance=0.00001):
    """移除重复的坐标点"""
    if not coordinates or len(coordinates) <= 1:
        return coordinates
    
    filtered = [coordinates[0]]
    
    for current in coordinates[1:]:
        if len(current) >= 2 and len(filtered[-1]) >= 2:
            distance = ((current[0] - filtered[-1][0]) ** 2 + 
                       (current[1] - filtered[-1][1]) ** 2) ** 0.5
            if distance > tolerance:
                filtered.append(current)
        else:
            filtered.append(current)
    
    return filtered

def simplify_line_douglas_peucker(coordinates, tolerance=0.0001):
    """使用道格拉斯-普克算法简化线段"""
    if len(coordinates) <= 2:
        return coordinates
    
    def point_line_distance(point, line_start, line_end):
        if len(point) < 2 or len(line_start) < 2 or len(line_end) < 2:
            return 0
        
        x0, y0 = point[0], point[1]
        x1, y1 = line_start[0], line_start[1]
        x2, y2 = line_end[0], line_end[1]
        
        line_length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if line_length == 0:
            return ((x0 - x1) ** 2 + (y0 - y1) ** 2) ** 0.5
        
        return abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1) / line_length
    
    def douglas_peucker(points, tolerance):
        if len(points) <= 2:
            return points
        
        max_distance = 0
        max_index = 0
        
        for i in range(1, len(points) - 1):
            distance = point_line_distance(points[i], points[0], points[-1])
            if distance > max_distance:
                max_distance = distance
                max_index = i
        
        if max_distance > tolerance:
            left = douglas_peucker(points[:max_index + 1], tolerance)
            right = douglas_peucker(points[max_index:], tolerance)
            return left[:-1] + right
        else:
            return [points[0], points[-1]]
    
    return douglas_peucker(coordinates, tolerance)

def optimize_track_data(track_data, optimization_level='medium'):
    """
    优化单个轨迹数据
    """
    if optimization_level == 'none':
        return track_data
        
    config = OPTIMIZATION_LEVELS.get(optimization_level, OPTIMIZATION_LEVELS['medium'])
    
    optimized = {
        'color': track_data.get('color'),
        'display_type': track_data.get('display_type'),
        'files': track_data.get('files', [])  # 保留文件列表用于统计
    }
    
    # 优化点数据
    if 'points' in track_data and track_data['points'] and 'features' in track_data['points']:
        original_points = track_data['points']['features']
        optimized_points = []
        
        for feature in original_points:
            if 'geometry' in feature and 'coordinates' in feature['geometry']:
                coords = feature['geometry']['coordinates']
                if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                    # 优化坐标精度
                    optimized_coords = [
                        round(coords[0], config['coord_precision']),
                        round(coords[1], config['coord_precision'])
                    ]
                    if len(coords) > 2:
                        optimized_coords.append(round(coords[2], config['coord_precision']))
                    
                    optimized_feature = {
                        'geometry': {'coordinates': optimized_coords}
                    }
                    
                    # 保留必要的属性
                    if 'properties' in feature and feature['properties']:
                        properties = {}
                        if 'timestamp' in feature['properties']:
                            properties['timestamp'] = feature['properties']['timestamp']
                        if properties:
                            optimized_feature['properties'] = properties
                    
                    optimized_points.append(optimized_feature)
        
        # 去除重复点
        if config['duplicate_tolerance'] > 0:
            filtered_points = []
            seen_coords = set()
            
            for point in optimized_points:
                coord_key = (point['geometry']['coordinates'][0], 
                           point['geometry']['coordinates'][1])
                coord_key = (round(coord_key[0], config['coord_precision']), 
                           round(coord_key[1], config['coord_precision']))
                if coord_key not in seen_coords:
                    seen_coords.add(coord_key)
                    filtered_points.append(point)
            
            optimized_points = filtered_points
        
        optimized['points'] = {'features': optimized_points}
        optimized['points_count'] = len(optimized_points)
    
    # 优化线数据
    if 'lines' in track_data and track_data['lines'] and 'features' in track_data['lines']:
        original_lines = track_data['lines']['features']
        optimized_lines = []
        
        for feature in original_lines:
            if 'geometry' in feature and 'coordinates' in feature['geometry']:
                coords = feature['geometry']['coordinates']
                if isinstance(coords, list) and len(coords) > 1:
                    # 优化坐标精度
                    optimized_coords = optimize_coordinates(coords, config['coord_precision'])
                    
                    # 去除重复点
                    if config['duplicate_tolerance'] > 0:
                        optimized_coords = remove_duplicate_points(
                            optimized_coords, config['duplicate_tolerance'])
                    
                    # 简化线段
                    if config['simplify_tolerance'] > 0 and len(optimized_coords) > 2:
                        optimized_coords = simplify_line_douglas_peucker(
                            optimized_coords, config['simplify_tolerance'])
                    
                    if len(optimized_coords) >= 2:
                        optimized_feature = {
                            'geometry': {'coordinates': optimized_coords}
                        }
                        
                        # 保留必要的属性
                        if 'properties' in feature and feature['properties']:
                            properties = {}
                            for key in ['start_time', 'end_time']:
                                if key in feature['properties']:
                                    properties[key] = feature['properties'][key]
                            if properties:
                                optimized_feature['properties'] = properties
                        
                        optimized_lines.append(optimized_feature)
        
        optimized['lines'] = {'features': optimized_lines}
        optimized['lines_count'] = len(optimized_lines)
    
    return optimized

def get_data_size(data, use_gzip=False):
    """
    估算数据序列化后的字节大小
    """
    json_data = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if use_gzip and GZIP_AVAILABLE:
        return len(gzip.compress(json_data))
    else:
        return len(json_data)

def save_data_file(data, filepath, use_gzip=False):
    """
    保存数据文件（JSON或Gzip压缩格式）
    """
    json_data = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if use_gzip and GZIP_AVAILABLE:
        with gzip.open(filepath, 'wb') as f:
            f.write(json_data)
    else:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

def create_cache_directory(cache_path="../cache"):
    """
    创建缓存目录
    """
    cache_dir = os.path.abspath(cache_path)
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
        print(f"创建缓存目录: {cache_dir}")
    return cache_dir

def save_to_data_with_size_limit(data, base_filename, cache_dir, use_gzip=False, 
                                optimization_level='medium', enable_chunking=True):
    """
    保存数据到文件，支持优化、智能分片和渐进式加载
    """
    print(f"\n=== 开始数据优化和保存 ===")
    print(f"优化级别: {optimization_level}")
    print(f"启用分片: {enable_chunking}")
    
    # 第一步：数据优化
    if optimization_level != 'none':
        print("正在优化数据结构...")
        original_size = get_data_size(data, use_gzip)
        
        optimized_data = {
            'metrics': data.get('metrics'),
            'bounds': data.get('bounds'),
            'generated_at': data.get('generated_at'),
            'version': data.get('version', '1.0'),
            'tracks': {}
        }
        
        optimization_stats = {}
        
        for track_type, track_data in data.get('tracks', {}).items():
            print(f"  优化轨迹类型: {track_type}")
            original_track_data = track_data.copy()
            optimized_track_data = optimize_track_data(track_data, optimization_level)
            optimized_data['tracks'][track_type] = optimized_track_data
            
            # 统计优化效果
            orig_points = original_track_data.get('points_count', 0)
            opt_points = optimized_track_data.get('points_count', 0)
            orig_lines = original_track_data.get('lines_count', 0) 
            opt_lines = optimized_track_data.get('lines_count', 0)
            
            optimization_stats[track_type] = {
                'points': {'original': orig_points, 'optimized': opt_points},
                'lines': {'original': orig_lines, 'optimized': opt_lines}
            }
        
        optimized_size = get_data_size(optimized_data, use_gzip)
        size_reduction = (original_size - optimized_size) / original_size * 100
        
        print(f"✅ 数据优化完成:")
        print(f"   原始大小: {original_size / 1024 / 1024:.2f} MB")
        print(f"   优化后大小: {optimized_size / 1024 / 1024:.2f} MB")
        print(f"   压缩比率: {size_reduction:.1f}%")
        
        for track_type, stats in optimization_stats.items():
            if stats['points']['original'] > 0:
                point_reduction = (stats['points']['original'] - stats['points']['optimized']) / stats['points']['original'] * 100
                print(f"   {track_type} 点数: {stats['points']['original']} → {stats['points']['optimized']} ({point_reduction:+.1f}%)")
            if stats['lines']['original'] > 0:
                line_reduction = (stats['lines']['original'] - stats['lines']['optimized']) / stats['lines']['original'] * 100
                print(f"   {track_type} 线段数: {stats['lines']['original']} → {stats['lines']['optimized']} ({line_reduction:+.1f}%)")
        
        data = optimized_data
    
    file_info = []
    file_ext = '.json.gz' if (use_gzip and GZIP_AVAILABLE) else '.json'
    format_name = 'gzip' if (use_gzip and GZIP_AVAILABLE) else 'json'
    
    # 检查是否需要分片
    total_size = get_data_size(data, use_gzip)
    max_chunk_size = CHUNK_CONFIG['max_chunk_size_mb'] * 1024 * 1024
    
    print(f"\n=== 分片决策 ===")
    print(f"总数据大小: {total_size / 1024 / 1024:.2f} MB")
    print(f"最大分片大小: {CHUNK_CONFIG['max_chunk_size_mb']} MB")
    print(f"文件大小限制: {MAX_FILE_SIZE / 1024 / 1024} MB")
    
    should_chunk = (enable_chunking and 
                   (total_size > max_chunk_size or 
                    len(data.get('tracks', {})) >= CHUNK_CONFIG['min_chunks']))
    
    if not should_chunk or len(data.get('tracks', {})) <= 1:
        # 单文件模式
        print("选择: 单文件模式")
        
        filepath = os.path.join(cache_dir, f"{base_filename}{file_ext}")
        try:
            save_data_file(data, filepath, use_gzip)
            print(f"✅ 保存到单个文件: {os.path.basename(filepath)} ({total_size / 1024 / 1024:.2f} MB)")
            
            # 生成配置文件
            config_data = {
                "data_type": "single",
                "single_file": f"{base_filename}{file_ext}",
                "format": format_name,
                "total_size_mb": total_size / 1024 / 1024,
                "optimization_level": optimization_level,
                "generated_at": data.get("generated_at")
            }
            
            config_filepath = os.path.join(cache_dir, "data_config.json")
            with open(config_filepath, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            
            return [{"file": f"{base_filename}{file_ext}", "size_mb": total_size / 1024 / 1024}]
            
        except Exception as e:
            print(f"❌ 保存文件失败: {e}")
            return []
    
    else:
        # 智能分片模式
        print("选择: 智能分片模式")
        
        chunks = []
        chunk_files = []
        
        # 计算实际需要的分片数量
        max_chunk_size = CHUNK_CONFIG['max_chunk_size_mb'] * 1024 * 1024
        target_chunks = max(CHUNK_CONFIG['min_chunks'], 
                           min(CHUNK_CONFIG['max_chunks'], 
                               int(total_size / max_chunk_size) + 1))
        
        print(f"目标分片数: {target_chunks}")
        print(f"每个分片目标大小: {CHUNK_CONFIG['max_chunk_size_mb']} MB")
        
        # 收集所有轨迹特征
        all_features = []
        track_type_map = {}  # 记录每个特征属于哪个轨迹类型
        
        for track_type, track_data in data.get('tracks', {}).items():
            # 收集点特征
            if track_data.get('points') and track_data['points'].get('features'):
                for feature in track_data['points']['features']:
                    feature_with_meta = {
                        'data': feature,
                        'track_type': track_type,
                        'feature_type': 'points'
                    }
                    all_features.append(feature_with_meta)
                    
            # 收集线特征
            if track_data.get('lines') and track_data['lines'].get('features'):
                for feature in track_data['lines']['features']:
                    feature_with_meta = {
                        'data': feature,
                        'track_type': track_type,
                        'feature_type': 'lines'
                    }
                    all_features.append(feature_with_meta)
        
        print(f"总特征数: {len(all_features)}")
        
        if not all_features:
            print("⚠️ 没有找到轨迹特征，回退到按轨迹类型分片")
            # 回退到原来的按轨迹类型分片
            track_items = list(data.get('tracks', {}).items())
            tracks_per_chunk = max(1, len(track_items) // target_chunks)
            
            for i in range(0, len(track_items), tracks_per_chunk):
                chunk_tracks = dict(track_items[i:i + tracks_per_chunk])
                
                chunk_data = {
                    "metrics": data.get("metrics"),
                    "bounds": data.get("bounds"),
                    "generated_at": data.get("generated_at"),
                    "version": data.get("version", "1.0"),
                    "tracks": chunk_tracks,
                    "chunk_info": {
                        "chunk_id": len(chunks),
                        "total_chunks": None,
                        "track_types": list(chunk_tracks.keys())
                    }
                }
                
                chunk_filename = f"{base_filename}_chunk_{len(chunks) + 1:03d}{file_ext}"
                chunk_filepath = os.path.join(cache_dir, chunk_filename)
                
                try:
                    save_data_file(chunk_data, chunk_filepath, use_gzip)
                    chunk_size = get_data_size(chunk_data, use_gzip)
                    print(f"✅ 保存分片: {chunk_filename} ({chunk_size / 1024 / 1024:.2f} MB) - {list(chunk_tracks.keys())}")
                    
                    chunks.append(chunk_filename)
                    chunk_files.append({
                        "file": chunk_filename, 
                        "size_mb": chunk_size / 1024 / 1024,
                        "track_types": list(chunk_tracks.keys())
                    })
                    
                except Exception as e:
                    print(f"❌ 保存分片失败: {e}")
        
        else:
            # 基于特征数量的智能分片
            features_per_chunk = max(1, len(all_features) // target_chunks)
            print(f"每个分片特征数: {features_per_chunk}")
            
            for i in range(0, len(all_features), features_per_chunk):
                chunk_features = all_features[i:i + features_per_chunk]
                
                # 按轨迹类型和特征类型重新组织数据
                chunk_tracks = {}
                for feature_meta in chunk_features:
                    track_type = feature_meta['track_type']
                    feature_type = feature_meta['feature_type']
                    feature_data = feature_meta['data']
                    
                    if track_type not in chunk_tracks:
                        # 复制轨迹类型的基本信息
                        original_track = data['tracks'][track_type]
                        chunk_tracks[track_type] = {
                            'color': original_track.get('color'),
                            'display_type': original_track.get('display_type'),
                            'files': original_track.get('files', [])
                        }
                        if feature_type == 'points':
                            chunk_tracks[track_type]['points'] = {'features': []}
                            chunk_tracks[track_type]['points_count'] = 0
                        if feature_type == 'lines':
                            chunk_tracks[track_type]['lines'] = {'features': []}
                            chunk_tracks[track_type]['lines_count'] = 0
                    
                    # 添加特征
                    if feature_type == 'points':
                        if 'points' not in chunk_tracks[track_type]:
                            chunk_tracks[track_type]['points'] = {'features': []}
                        chunk_tracks[track_type]['points']['features'].append(feature_data)
                        chunk_tracks[track_type]['points_count'] = len(chunk_tracks[track_type]['points']['features'])
                    elif feature_type == 'lines':
                        if 'lines' not in chunk_tracks[track_type]:
                            chunk_tracks[track_type]['lines'] = {'features': []}
                        chunk_tracks[track_type]['lines']['features'].append(feature_data)
                        chunk_tracks[track_type]['lines_count'] = len(chunk_tracks[track_type]['lines']['features'])
                
                chunk_data = {
                    "metrics": data.get("metrics"),
                    "bounds": data.get("bounds"),
                    "generated_at": data.get("generated_at"),
                    "version": data.get("version", "1.0"),
                    "tracks": chunk_tracks,
                    "chunk_info": {
                        "chunk_id": len(chunks),
                        "total_chunks": None,
                        "track_types": list(chunk_tracks.keys())
                    }
                }
                
                chunk_filename = f"{base_filename}_chunk_{len(chunks) + 1:03d}{file_ext}"
                chunk_filepath = os.path.join(cache_dir, chunk_filename)
                
                try:
                    save_data_file(chunk_data, chunk_filepath, use_gzip)
                    chunk_size = get_data_size(chunk_data, use_gzip)
                    
                    # 统计分片内容
                    total_points = sum(track.get('points_count', 0) for track in chunk_tracks.values())
                    total_lines = sum(track.get('lines_count', 0) for track in chunk_tracks.values())
                    
                    print(f"✅ 保存分片: {chunk_filename} ({chunk_size / 1024 / 1024:.2f} MB) - {total_points} 点, {total_lines} 线")
                    
                    chunks.append(chunk_filename)
                    chunk_files.append({
                        "file": chunk_filename, 
                        "size_mb": chunk_size / 1024 / 1024,
                        "track_types": list(chunk_tracks.keys()),
                        "points": total_points,
                        "lines": total_lines
                    })
                    
                except Exception as e:
                    print(f"❌ 保存分片失败: {e}")
        
        # 更新所有分片的 total_chunks 信息
        for i, chunk_filename in enumerate(chunks):
            chunk_filepath = os.path.join(cache_dir, chunk_filename)
            try:
                if use_gzip:
                    with gzip.open(chunk_filepath, 'rt', encoding='utf-8') as f:
                        chunk_data = json.load(f)
                else:
                    with open(chunk_filepath, 'r', encoding='utf-8') as f:
                        chunk_data = json.load(f)
                
                chunk_data['chunk_info']['total_chunks'] = len(chunks)
                save_data_file(chunk_data, chunk_filepath, use_gzip)
                
            except Exception as e:
                print(f"⚠️ 更新分片信息失败: {e}")
        
        # 生成分片配置文件
        if chunks:
            config_data = {
                "data_type": "chunked",
                "chunks": chunks,
                "format": format_name,
                "total_chunks": len(chunks),
                "total_size_mb": total_size / 1024 / 1024,
                "optimization_level": optimization_level,
                "chunk_size_mb": CHUNK_CONFIG['max_chunk_size_mb'],
                "generated_at": data.get("generated_at")
            }
            
            config_filepath = os.path.join(cache_dir, "data_config.json")
            with open(config_filepath, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            print(f"✅ 生成分片配置文件: data_config.json")
        
        print(f"✅ 智能分片完成: {len(chunks)} 个分片")
        return chunk_files
        
        # 按轨迹类型分片
        for i, (track_type, track_data) in enumerate(data.get("tracks", {}).items()):
            # 每个轨迹类型单独保存
            chunk_data = {
                "metrics": data.get("metrics", {}),
                "tracks": {track_type: track_data},
                "bounds": data.get("bounds"),
                "generated_at": data.get("generated_at"),
                "version": data.get("version"),
                "chunk_info": {
                    "chunk_id": i,
                    "track_type": track_type,
                    "is_chunk": True
                }
            }
            
            chunk_size = get_json_size(chunk_data)
            chunk_filename = f"{base_filename}_chunk_{i}_{track_type}.json"
            chunk_filepath = os.path.join(cache_dir, chunk_filename)
            
            try:
                with open(chunk_filepath, 'w', encoding='utf-8') as f:
                    json.dump(chunk_data, f, ensure_ascii=False, indent=2)
                print(f"✅ 保存分片文件: {chunk_filename} ({chunk_size / 1024 / 1024:.2f} MB)")
                file_info.append({
                    "file": chunk_filename, 
                    "size_mb": chunk_size / 1024 / 1024,
                    "track_type": track_type,
                    "chunk_id": i
                })
            except Exception as e:
                print(f"❌ 保存分片文件 {chunk_filepath} 时出错: {e}")
        
        # 生成主索引文件
        index_data = {
            "total_size_mb": total_size / 1024 / 1024,
            "chunks": file_info,
            "generated_at": data.get("generated_at"),
            "version": data.get("version"),
            "is_chunked": True
        }
        
        index_filepath = os.path.join(cache_dir, f"{base_filename}_index.json")
        try:
            with open(index_filepath, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, ensure_ascii=False, indent=2)
            print(f"✅ 生成索引文件: {base_filename}_index.json")
            file_info.insert(0, {"file": f"{base_filename}_index.json", "size_mb": get_json_size(index_data) / 1024 / 1024, "is_index": True})
        except Exception as e:
            print(f"❌ 生成索引文件时出错: {e}")
        
        # 生成静态配置文件（用于静态托管）
        config_data = {
            "data_type": "chunked",
            "chunks": [chunk["file"] for chunk in file_info if not chunk.get("is_index")],
            "index_file": f"{base_filename}_index.json",
            "total_size_mb": total_size / 1024 / 1024,
            "generated_at": data.get("generated_at")
        }
        
        config_filepath = os.path.join(cache_dir, "data_config.json")
        try:
            with open(config_filepath, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            print(f"✅ 生成静态配置文件: data_config.json")
        except Exception as e:
            print(f"❌ 生成配置文件时出错: {e}")
    
    return file_info

def parse_gpx_file_fast(file_path):
    """
    快速解析GPX文件，提取轨迹点坐标，减少重复查找
    """
    points = []
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # 预编译命名空间路径，支持 GPX 1.0 和 1.1 版本
        namespaced_path_v11 = './/{http://www.topografix.com/GPX/1/1}trkpt'
        namespaced_path_v10 = './/{http://www.topografix.com/GPX/1/0}trkpt'
        simple_path = './/trkpt'
        
        # 尝试不同的命名空间路径
        trkpts = root.findall(namespaced_path_v11)
        if not trkpts:
            trkpts = root.findall(namespaced_path_v10)
        if not trkpts:
            trkpts = root.findall(simple_path)
        
        # 批量处理，减少函数调用开销
        for trkpt in trkpts:
            try:
                lat = float(trkpt.get('lat'))
                lon = float(trkpt.get('lon'))
                points.append((lat, lon))
            except (ValueError, TypeError):
                continue  # 跳过无效点
            
        print(f"  快速解析 {os.path.basename(file_path)}: {len(points)} 个轨迹点")
        return points
        
    except Exception as e:
        print(f"  错误：解析 {file_path} 时出错: {e}")
        return []

def fix_dateline_crossing(coordinates):
    """
    分割跨越日界线的轨迹，避免绕全球显示
    将跨日界线的长线段分割成多个短线段
    """
    if len(coordinates) < 2:
        return [coordinates]  # 返回线段列表
    
    line_segments = []  # 存储分割后的线段
    current_segment = [coordinates[0]]  # 当前线段
    
    for i in range(1, len(coordinates)):
        prev_lon = current_segment[-1][0]
        curr_lon = coordinates[i][0]
        curr_lat = coordinates[i][1]
        
        # 计算经度差
        lon_diff = curr_lon - prev_lon
        
        # 如果经度差超过180度，说明跨越了日界线
        if abs(lon_diff) > 180:
            # 结束当前线段
            if len(current_segment) > 1:
                line_segments.append(current_segment)
            
            # 开始新的线段
            current_segment = [[curr_lon, curr_lat]]
        else:
            # 正常情况，添加到当前线段
            current_segment.append([curr_lon, curr_lat])
    
    # 添加最后一个线段
    if len(current_segment) > 1:
        line_segments.append(current_segment)
    
    return line_segments if line_segments else [coordinates]

def parse_gpx_to_geojson(file_path, track_type='road'):
    """
    将GPX文件解析为GeoJSON格式
    返回点和线的GeoJSON数据
    """
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        points_features = []
        line_features = []
        
        # 查找所有轨迹段，支持 GPX 1.0 和 1.1 版本
        trksegs = root.findall('.//{http://www.topografix.com/GPX/1/1}trkseg')
        if not trksegs:
            trksegs = root.findall('.//{http://www.topografix.com/GPX/1/0}trkseg')
        if not trksegs:
            trksegs = root.findall('.//trkseg')
        
        for trkseg in trksegs:
            coordinates = []
            
            # 处理轨迹段中的每个点，支持 GPX 1.0 和 1.1 版本
            trkpts = trkseg.findall('{http://www.topografix.com/GPX/1/1}trkpt')
            if not trkpts:
                trkpts = trkseg.findall('{http://www.topografix.com/GPX/1/0}trkpt')
            if not trkpts:
                trkpts = trkseg.findall('trkpt')
            
            for trkpt in trkpts:
                lat = float(trkpt.get('lat'))
                lon = float(trkpt.get('lon'))
                coordinates.append([lon, lat])  # GeoJSON格式是[lon, lat]
                
                # 为每个点创建Point特征
                points_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [lon, lat]
                    },
                    "properties": {
                        "track_type": track_type
                    }
                })
            
            # 如果有多个点，创建LineString特征
            if len(coordinates) > 1:
                # 分割跨日界线的轨迹
                line_segments = fix_dateline_crossing(coordinates)
                
                # 为每个线段创建一个LineString特征
                for segment in line_segments:
                    if len(segment) > 1:  # 确保线段至少有2个点
                        line_features.append({
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": segment
                            },
                            "properties": {
                                "track_type": track_type
                            }
                        })
        
        return {
            "points": {
                "type": "FeatureCollection",
                "features": points_features
            },
            "lines": {
                "type": "FeatureCollection",
                "features": line_features
            }
        }
        
    except Exception as e:
        print(f"  错误：解析 {file_path} 为GeoJSON时出错: {e}")
        return {
            "points": {"type": "FeatureCollection", "features": []},
            "lines": {"type": "FeatureCollection", "features": []}
        }

def calculate_grid_area_fast(points, grid_size_meters=50):
    """
    使用集合快速计算网格化面积，时间复杂度O(n)
    """
    if not points:
        return 0, 0
    
    # 使用集合存储网格坐标，自动去重
    grid_cells = set()
    
    for lat, lon in points:
        # 计算网格坐标
        lat_degrees_per_grid = grid_size_meters / 111000
        lon_degrees_per_grid = grid_size_meters / (111000 * math.cos(math.radians(lat)))
        
        # 将坐标映射到网格
        grid_lat = int(lat / lat_degrees_per_grid)
        grid_lon = int(lon / lon_degrees_per_grid)
        
        grid_cells.add((grid_lat, grid_lon))
    
    # 计算总面积
    total_area_m2 = len(grid_cells) * (grid_size_meters ** 2)
    
    return len(grid_cells), total_area_m2

def remove_duplicates_fast(points, min_distance=50):
    """
    使用空间网格索引快速去重，时间复杂度O(n)
    """
    if not points:
        return []
    
    # 计算网格大小（稍大于min_distance以确保覆盖）
    grid_size = min_distance * 1.5  # 75米网格
    grid_dict = {}
    unique_points = []
    total_points = len(points)
    
    print(f"  使用 {grid_size}m 网格加速去重...")
    
    for i, (lat, lon) in enumerate(points):
        # 计算网格坐标
        grid_lat = int(lat * 111000 / grid_size)  # 纬度网格
        grid_lon = int(lon * 111000 * math.cos(math.radians(lat)) / grid_size)  # 经度网格
        
        # 检查当前网格和相邻8个网格
        is_duplicate = False
        for dlat in [-1, 0, 1]:
            for dlon in [-1, 0, 1]:
                check_grid = (grid_lat + dlat, grid_lon + dlon)
                if check_grid in grid_dict:
                    # 只检查同网格内的点
                    for existing_lat, existing_lon in grid_dict[check_grid]:
                        if haversine(lat, lon, existing_lat, existing_lon) <= min_distance:
                            is_duplicate = True
                            break
                if is_duplicate:
                    break
            if is_duplicate:
                break
        
        if not is_duplicate:
            unique_points.append((lat, lon))
            # 添加到网格字典
            grid_key = (grid_lat, grid_lon)
            if grid_key not in grid_dict:
                grid_dict[grid_key] = []
            grid_dict[grid_key].append((lat, lon))
        
        # 显示进度
        if (i + 1) % 5000 == 0 or i == total_points - 1:
            print(f"  快速去重进度: {i+1}/{total_points} -> {len(unique_points)} 个有效点")
    
    return unique_points

def remove_duplicates(points, min_distance=50):
    """
    去除重复点（距离小于min_distance米的点只保留一个）
    """
    unique_points = []
    total_points = len(points)
    
    for i, point in enumerate(points):
        is_duplicate = False
        for existing in unique_points:
            if haversine(point[0], point[1], existing[0], existing[1]) <= min_distance:
                is_duplicate = True
                break
        
        if not is_duplicate:
            unique_points.append(point)
        
        # 显示进度
        if (i + 1) % 5000 == 0 or i == total_points - 1:
            print(f"  去重进度: {i+1}/{total_points} -> {len(unique_points)} 个有效点")
    
    return unique_points

def process_road_data():
    """
    统一处理road类型的GPX数据，进行去重和面积计算
    返回去重后的点坐标和计算结果
    """
    print("\n=== 处理road轨迹数据 ===")
    
    # 查找GPX/road目录下的所有GPX文件
    gpx_pattern = os.path.join("GPX", "road", "*.gpx")
    gpx_files = glob.glob(gpx_pattern)
    
    if not gpx_files:
        print("警告：在 GPX/road/ 目录下没有找到任何GPX文件")
        return [], {
            "calculation_time": datetime.now().isoformat(),
            "total_files": 0,
            "total_points": 0,
            "unique_points": 0,
            "grid_cells": 0,
            "grid_size_meters": 50,
            "explored_area_km2": 0,
            "earth_percentage": 0,
            "calculation_method": "grid_based",
            "files": []
        }
    
    print(f"找到 {len(gpx_files)} 个GPX文件:")
    for file in gpx_files:
        print(f"  - {os.path.basename(file)}")
    
    # 并行处理多个GPX文件
    print(f"使用 {min(len(gpx_files), 4)} 个线程并行处理文件...")
    all_points = []
    
    with ThreadPoolExecutor(max_workers=min(len(gpx_files), 4)) as executor:
        future_to_file = {executor.submit(parse_gpx_file_fast, gpx_file): gpx_file 
                         for gpx_file in gpx_files}
        
        for future in future_to_file:
            points = future.result()
            all_points.extend(points)
    
    print(f"\n总共收集到 {len(all_points)} 个轨迹点")
    
    if not all_points:
        print("错误：没有解析到任何轨迹点")
        return [], None
    
    # 一次性完成去重计算
    print("开始快速去重计算...")
    unique_points = remove_duplicates_fast(all_points)
    
    # 使用去重后的点计算网格化面积
    print("计算网格化探索面积...")
    grid_count, total_explored_area_m2 = calculate_grid_area_fast(unique_points, grid_size_meters=50)
    explored_area_km2 = total_explored_area_m2 / 1e6
    percentage = (total_explored_area_m2 / EARTH_SURFACE_AREA) * 100
    
    result = {
        "calculation_time": datetime.now().isoformat(),
        "total_files": len(gpx_files),
        "total_points": len(all_points),
        "unique_points": len(unique_points),
        "grid_cells": grid_count,
        "grid_size_meters": 50,
        "explored_area_km2": round(explored_area_km2, 6),
        "earth_percentage": round(percentage, 15),
        "calculation_method": "grid_based",
        "files": [os.path.basename(f) for f in gpx_files]
    }
    
    print(f"\n=== 计算结果 ===")
    print(f"处理文件数: {result['total_files']}")
    print(f"总轨迹点: {result['total_points']}")
    print(f"去重后点数: {result['unique_points']}")
    print(f"网格单元数: {result['grid_cells']}")
    print(f"网格大小: {result['grid_size_meters']}m × {result['grid_size_meters']}m")
    print(f"探索面积: {result['explored_area_km2']} km²")
    print(f"占地球比例: {result['earth_percentage']}%")
    print(f"计算方法: {result['calculation_method']}")
    
    return unique_points, result

def generate_tracks_data(road_unique_points=None):
    """
    生成所有轨迹的GeoJSON数据
    road_unique_points: 预处理的road轨迹去重点，避免重复计算
    """
    print("\n=== 生成轨迹数据 ===")
    
    tracks_data = {}
    all_coordinates = []  # 用于计算边界
    
    for track_type in GPX_TYPES.keys():
        print(f"\n处理 {track_type} 轨迹...")
        
        if track_type == 'road' and road_unique_points is not None:
            # 使用预处理的road数据，避免重复计算
            print(f"  使用预处理的road数据: {len(road_unique_points)} 个去重点")
            
            all_points_features = []
            for lat, lon in road_unique_points:
                all_points_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [lon, lat]
                    },
                    "properties": {
                        "track_type": track_type
                    }
                })
                all_coordinates.append([lon, lat])
            
            # 对于road类型，我们主要展示点，线要素可以为空或者从原始数据生成
            gpx_pattern = os.path.join("GPX", track_type, "*.gpx")
            gpx_files = glob.glob(gpx_pattern)
            all_lines_features = []
            
            # 只生成线要素，不重复处理点
            for gpx_file in gpx_files:
                geojson_data = parse_gpx_to_geojson(gpx_file, track_type)
                all_lines_features.extend(geojson_data["lines"]["features"])
            
            tracks_data[track_type] = {
                "points": {
                    "type": "FeatureCollection",
                    "features": all_points_features
                },
                "lines": {
                    "type": "FeatureCollection", 
                    "features": all_lines_features
                },
                "files": [os.path.basename(f) for f in gpx_files],
                "color": GPX_TYPES[track_type]['color'],
                "display_type": GPX_TYPES[track_type]['display_type'],
                "points_count": len(all_points_features),
                "lines_count": len(all_lines_features)
            }
            
            print(f"  完成: {len(all_points_features)} 个点, {len(all_lines_features)} 条线")
            continue
        
        # 处理其他类型的轨迹
        gpx_pattern = os.path.join("GPX", track_type, "*.gpx")
        gpx_files = glob.glob(gpx_pattern)
        
        if not gpx_files:
            print(f"  {track_type} 目录下没有找到GPX文件")
            tracks_data[track_type] = {
                "points": {"type": "FeatureCollection", "features": []},
                "lines": {"type": "FeatureCollection", "features": []},
                "files": [],
                "color": GPX_TYPES[track_type]['color'],
                "display_type": GPX_TYPES[track_type]['display_type'],
                "points_count": 0,
                "lines_count": 0
            }
            continue
        
        print(f"  找到 {len(gpx_files)} 个文件: {[os.path.basename(f) for f in gpx_files]}")
        
        # 合并所有文件的GeoJSON数据
        all_points_features = []
        all_lines_features = []
        all_raw_points = []  # 用于去重的原始点坐标
        
        for gpx_file in gpx_files:
            geojson_data = parse_gpx_to_geojson(gpx_file, track_type)
            all_points_features.extend(geojson_data["points"]["features"])
            all_lines_features.extend(geojson_data["lines"]["features"])
            
            # 收集原始坐标用于去重
            for feature in geojson_data["points"]["features"]:
                coord = feature["geometry"]["coordinates"]
                all_raw_points.append((coord[1], coord[0]))  # 转换为(lat, lon)格式
        
        # 对点进行去重处理
        if all_raw_points:
            print(f"  原始点数: {len(all_raw_points)}")
            unique_raw_points = remove_duplicates_fast(all_raw_points, min_distance=50)
            print(f"  去重后点数: {len(unique_raw_points)}")
            
            # 重新生成去重后的GeoJSON点要素
            all_points_features = []
            for lat, lon in unique_raw_points:
                all_points_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [lon, lat]
                    },
                    "properties": {
                        "track_type": track_type
                    }
                })
                all_coordinates.append([lon, lat])
        
        # 从线要素中也收集坐标用于计算边界
        for feature in all_lines_features:
            if feature["geometry"]["type"] == "LineString":
                for coord in feature["geometry"]["coordinates"]:
                    all_coordinates.append(coord)
        
        tracks_data[track_type] = {
            "points": {
                "type": "FeatureCollection",
                "features": all_points_features
            },
            "lines": {
                "type": "FeatureCollection", 
                "features": all_lines_features
            },
            "files": [os.path.basename(f) for f in gpx_files],
            "color": GPX_TYPES[track_type]['color'],
            "display_type": GPX_TYPES[track_type]['display_type'],
            "points_count": len(all_points_features),
            "lines_count": len(all_lines_features)
        }
        
        print(f"  完成: {len(all_points_features)} 个点, {len(all_lines_features)} 条线")
    
    # 计算边界
    bounds = None
    if all_coordinates:
        lons = [coord[0] for coord in all_coordinates]
        lats = [coord[1] for coord in all_coordinates]
        bounds = {
            "min_lng": min(lons),
            "max_lng": max(lons), 
            "min_lat": min(lats),
            "max_lat": max(lats)
        }
        print(f"\n计算得到轨迹边界: {bounds}")
    
    return tracks_data, bounds

def save_to_json(data, filename="metrics.json"):
    """
    保存数据到JSON文件
    """
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"结果已保存到: {filename}")
        return True
    except Exception as e:
        print(f"保存文件时出错: {e}")
        return False

if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description="GPX轨迹数据预处理工具（性能优化版本）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
优化级别说明：
  none     - 不进行数据优化
  low      - 基础优化，保留更多精度
  medium   - 中等优化，平衡性能与精度（默认）
  high     - 高级优化，最大化性能，精度略有损失

示例用法：
  python calculate_metrics.py                    # 使用默认设置
  python calculate_metrics.py -o high            # 高级优化
  python calculate_metrics.py -o medium -c 0.3   # 中等优化 + 0.3MB分片
  python calculate_metrics.py --no-chunk         # 禁用分片
        """
    )
    
    parser.add_argument('-o', '--optimization', 
                       choices=['none', 'low', 'medium', 'high'],
                       default='medium',
                       help='数据优化级别 (默认: medium)')
    
    parser.add_argument('-c', '--chunk-size', 
                       type=float, 
                       default=0.5,
                       help='分片大小(MB) (默认: 0.5)')
    
    parser.add_argument('--no-chunk', 
                       action='store_true',
                       help='禁用数据分片')
    
    parser.add_argument('--no-gzip', 
                       action='store_true',
                       help='禁用Gzip压缩')
    
    parser.add_argument('--cache-dir', 
                       default='../cache',
                       help='缓存目录路径 (默认: ../cache)')
    
    args = parser.parse_args()
    
    # 更新分片配置
    CHUNK_CONFIG['max_chunk_size_mb'] = args.chunk_size
    
    print("=== GPX轨迹数据预处理工具（性能优化版本）===")
    print("功能：1. 计算探索面积  2. 数据优化  3. 智能分片  4. 生成缓存")
    print(f"优化级别: {args.optimization}")
    print(f"分片设置: {'禁用' if args.no_chunk else f'启用 ({args.chunk_size}MB)'}")
    print(f"压缩设置: {'禁用' if args.no_gzip else '启用Gzip'}")
    
    # 检查GPX目录是否存在
    if not os.path.exists("GPX"):
        print("错误：GPX 目录不存在")
        exit(1)
    
    # 创建缓存目录
    cache_dir = create_cache_directory(args.cache_dir)
    
    # 1. 统一处理road轨迹数据（去重 + 面积计算）
    print(f"\n=== 第1步：处理road轨迹数据 ===")
    road_unique_points, metrics = process_road_data()
    
    if not metrics:
        print("\n❌ 探索面积计算失败！")
        exit(1)
    
    # 2. 生成所有轨迹数据（使用预处理的road数据）
    print(f"\n=== 第2步：生成轨迹数据 ===")
    tracks_data, bounds = generate_tracks_data(road_unique_points)
    
    # 3. 合并所有数据
    final_data = {
        "metrics": metrics,
        "tracks": tracks_data,
        "bounds": bounds,
        "generated_at": datetime.now().isoformat(),
        "version": "3.0"
    }
    
    # 4. 保存数据文件
    print(f"\n=== 第3步：保存优化数据到缓存目录 ({cache_dir}) ===")
    
    # 保存指标文件（保持向后兼容）
    metrics_file = os.path.join(cache_dir, "metrics.json")
    if save_to_json(metrics, metrics_file):
        print("✅ 指标文件保存成功")
    else:
        print("❌ 指标文件保存失败")
        exit(1)
    
    # 保存完整轨迹数据（支持优化和智能分片）
    use_gzip = GZIP_AVAILABLE and not args.no_gzip
    enable_chunking = not args.no_chunk
    
    file_info = save_to_data_with_size_limit(
        final_data, 
        "tracks_data", 
        cache_dir, 
        use_gzip,
        args.optimization,
        enable_chunking
    )
    
    if file_info:
        print("\n=== 第4步：生成统计报告 ===")
        
        # 显示统计信息
        total_points = sum(track.get('points_count', 0) for track in tracks_data.values())
        total_lines = sum(track.get('lines_count', 0) for track in tracks_data.values())
        total_files = sum(len(track.get('files', [])) for track in tracks_data.values())
        
        print(f"\n📊 轨迹数据统计:")
        print(f"  处理文件总数: {total_files}")
        print(f"  轨迹点总数: {total_points:,}")
        print(f"  轨迹线总数: {total_lines:,}")
        
        for track_type, data in tracks_data.items():
            if data.get('files'):
                print(f"  {track_type}: {len(data['files'])} 个文件, {data.get('points_count', 0):,} 个点, {data.get('lines_count', 0):,} 条线")
        
        if bounds:
            print(f"  轨迹边界: 经度 {bounds['min_lng']:.4f} ~ {bounds['max_lng']:.4f}, 纬度 {bounds['min_lat']:.4f} ~ {bounds['max_lat']:.4f}")
        
        print(f"\n📁 生成的文件:")
        total_size = 0
        for info in file_info:
            size = info['size_mb']
            total_size += size
            
            if 'track_types' in info:
                track_info = ', '.join(info['track_types'])
                print(f"  🧩 分片: {info['file']} ({size:.2f} MB) - {track_info}")
            else:
                print(f"  📄 数据: {info['file']} ({size:.2f} MB)")
        
        print(f"  📊 总大小: {total_size:.2f} MB")
        
        print(f"\n✅ 数据预处理完成！")
        print(f"📂 所有文件已保存到: {cache_dir}")
        print("🌐 现在可以在浏览器中打开 index.html 查看轨迹")
        
        # 性能提示
        if args.optimization == 'none':
            print("\n💡 提示: 使用 -o medium 或 -o high 可以显著减少文件大小并提高加载速度")
        if args.no_chunk and total_size > 2:
            print("💡 提示: 大文件建议启用分片以提高加载体验")
        
    else:
        print("❌ 轨迹数据文件保存失败")
        exit(1)