#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPX轨迹数据预处理脚本
功能：
1. 计算road目录下所有GPX文件的探索面积和百分比
2. 将所有类型的GPX轨迹转换为GeoJSON格式
3. 生成轨迹缓存文件供网页快速加载
结果保存为JSON文件供网页调用
"""

import os
import json
import xml.etree.ElementTree as ET
import math
import glob
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

# 地球表面积（平方米）
EARTH_SURFACE_AREA = 510072000e6

# 文件大小限制（字节）
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

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

def get_json_size(data):
    """
    估算JSON数据序列化后的字节大小（使用实际保存格式）
    """
    return len(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))

def create_cache_directory():
    """
    创建缓存目录
    """
    cache_dir = "cache"
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
        print(f"创建缓存目录: {cache_dir}")
    return cache_dir

def save_to_json_with_size_limit(data, base_filename, cache_dir):
    """
    保存数据到JSON文件，支持大小限制和文件分片
    """
    file_info = []
    
    # 检查单个文件是否超过大小限制
    total_size = get_json_size(data)
    print(f"总数据大小: {total_size / 1024 / 1024:.2f} MB")
    
    if total_size <= MAX_FILE_SIZE:
        # 文件大小在限制内，直接保存
        filepath = os.path.join(cache_dir, f"{base_filename}.json")
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"✅ 保存到单个文件: {filepath} ({total_size / 1024 / 1024:.2f} MB)")
            
            # 生成静态配置文件（单个文件）
            config_data = {
                "data_type": "single",
                "single_file": f"{base_filename}.json",
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
            
            return [{"file": f"{base_filename}.json", "size_mb": total_size / 1024 / 1024}]
        except Exception as e:
            print(f"❌ 保存文件 {filepath} 时出错: {e}")
            return []
    else:
        # 文件过大，需要分片
        print(f"⚠️ 文件过大 ({total_size / 1024 / 1024:.2f} MB > {MAX_FILE_SIZE / 1024 / 1024} MB)，开始分片...")
        
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
    修复跨越日界线的坐标序列
    如果相邻两点经度差超过180度，则调整坐标使轨迹正确显示
    """
    if len(coordinates) < 2:
        return coordinates
    
    fixed_coordinates = [coordinates[0]]  # 保留第一个点
    
    for i in range(1, len(coordinates)):
        prev_lon = fixed_coordinates[-1][0]
        curr_lon = coordinates[i][0]
        curr_lat = coordinates[i][1]
        
        # 计算经度差
        lon_diff = curr_lon - prev_lon
        
        # 如果经度差超过180度，说明跨越了日界线
        if lon_diff > 180:
            # 从东向西跨越日界线，将当前点的经度减360度
            fixed_coordinates.append([curr_lon - 360, curr_lat])
        elif lon_diff < -180:
            # 从西向东跨越日界线，将当前点的经度加360度
            fixed_coordinates.append([curr_lon + 360, curr_lat])
        else:
            # 正常情况，不需要调整
            fixed_coordinates.append([curr_lon, curr_lat])
    
    return fixed_coordinates

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
                # 修复跨日界线的坐标
                fixed_coordinates = fix_dateline_crossing(coordinates)
                
                line_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": fixed_coordinates
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
    print("=== GPX轨迹数据预处理工具 ===")
    print("功能：1. 计算探索面积  2. 生成轨迹缓存（支持大文件分片）")
    
    # 检查GPX目录是否存在
    if not os.path.exists("GPX"):
        print("错误：GPX 目录不存在")
        exit(1)
    
    # 创建缓存目录
    cache_dir = create_cache_directory()
    
    # 1. 统一处理road轨迹数据（去重 + 面积计算）
    road_unique_points, metrics = process_road_data()
    
    if not metrics:
        print("\n❌ 探索面积计算失败！")
        exit(1)
    
    # 2. 生成所有轨迹数据（使用预处理的road数据）
    print("\n开始生成轨迹数据...")
    tracks_data, bounds = generate_tracks_data(road_unique_points)
    
    # 3. 合并所有数据
    final_data = {
        "metrics": metrics,
        "tracks": tracks_data,
        "bounds": bounds,
        "generated_at": datetime.now().isoformat(),
        "version": "2.2"
    }
    
    # 4. 保存数据文件
    print(f"\n=== 保存数据文件到缓存目录 ({cache_dir}) ===")
    
    # 保存指标文件（保持向后兼容）
    metrics_file = os.path.join(cache_dir, "metrics.json")
    if save_to_json(metrics, metrics_file):
        print("✅ 指标文件保存成功")
    else:
        print("❌ 指标文件保存失败")
        exit(1)
    
    # 保存完整轨迹数据（支持大文件分片）
    file_info = save_to_json_with_size_limit(final_data, "tracks_data", cache_dir)
    
    if file_info:
        print("✅ 轨迹数据文件保存成功")
        
        # 显示统计信息
        total_points = sum(track.get('points_count', 0) for track in tracks_data.values())
        total_lines = sum(track.get('lines_count', 0) for track in tracks_data.values())
        total_files = sum(len(track.get('files', [])) for track in tracks_data.values())
        
        print(f"\n=== 轨迹数据统计 ===")
        print(f"处理文件总数: {total_files}")
        print(f"轨迹点总数: {total_points}")
        print(f"轨迹线总数: {total_lines}")
        
        for track_type, data in tracks_data.items():
            if data.get('files'):
                print(f"{track_type}: {len(data['files'])} 个文件, {data.get('points_count', 0)} 个点, {data.get('lines_count', 0)} 条线")
        
        if bounds:
            print(f"轨迹边界: 经度 {bounds['min_lng']:.4f} ~ {bounds['max_lng']:.4f}, 纬度 {bounds['min_lat']:.4f} ~ {bounds['max_lat']:.4f}")
        
        print(f"\n=== 生成的文件列表 ===")
        for info in file_info:
            if info.get('is_index'):
                print(f"📋 索引文件: {info['file']} ({info['size_mb']:.2f} MB)")
            elif 'track_type' in info:
                print(f"🧩 分片文件: {info['file']} ({info['size_mb']:.2f} MB) - {info['track_type']}")
            else:
                print(f"📄 数据文件: {info['file']} ({info['size_mb']:.2f} MB)")
        
        print(f"\n✅ 数据预处理完成！")
        print(f"所有文件已保存到 {cache_dir} 目录")
        print("现在需要修改 index.html 以从缓存目录读取文件")
        
    else:
        print("❌ 轨迹数据文件保存失败")
        exit(1)