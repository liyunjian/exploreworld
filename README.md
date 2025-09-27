# exploreworld

GPXtoJSON是GPX转成JSON的python程序

运行之后会生成cache文件夹

cache中

·metrics.json是记录足迹探索面积

·其他的是做了分片的GEOjson（脚本设置了不允许文件大于20MB，因为CF pages最大只能单个文件25MB）


index.html中需要Mapbox的API key用于加载地图
