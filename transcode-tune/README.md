# Transcode Parameter Tuner

自动化转码参数调优工具，支持迭代实验、自动分析和Windows通知。

## 功能

- 📊 参数组合网格搜索
- 🔄 自动触发转码 → 等待完成 → 下载分析
- 📈 码率/I帧分析
- 🎯 达标检测
- 🏆 PSNR/VMAF质量评估
- 📢 Windows通知提醒

## 安装

```bash
pip install -r requirements.txt
```

确保系统已安装：
- `ffmpeg` / `ffprobe` (用于视频分析)
- `ssh` / `scp` (用于远程文件操作)

## 使用

1. **编辑配置文件** `experiment.yaml`：
   - 设置JSON模板路径
   - 定义要调优的参数和候选值
   - 配置远程服务器信息
   - 设置优化目标

2. **准备JSON模板** `base-template.json`

3. **运行**：

```bash
# 正常运行
python transcode_tune.py --config experiment.yaml

# 预览参数组合（不执行）
python transcode_tune.py --config experiment.yaml --dry-run
```

4. **查看结果**：

结果保存在 `./results/YYYYMMDD_HHMMSS/` 目录下：
- `config_XXX.json` - 每次迭代的完整配置
- `output_XXX.mp4` - 下载的转码文件
- `results.json` - 完整日志和最优结果

## 配置说明

```yaml
# JSON模板
template: ./base-template.json

# 转码API
api_url: http://your-server/api/transcode

# 要调优的参数（使用点号路径访问嵌套字段）
params:
  encoder.x264.bitrate: [2000, 3000, 4000]
  encoder.x264.crf: [18, 23, 28]

# 远程服务器
remote:
  host: 192.168.1.100
  user: transcode
  output_dir: /data/output

# 目标指标
targets:
  bitrate_avg: 3500
  bitrate_max: 5000

# 质量评估
metrics:
  - psnr
  - vmaf
```

## 路径注入

参数通过路径注入到JSON模板，例如：

```
encoder.x264.bitrate → {"encoder": {"x264": {"bitrate": 3000}}}
```

## 扩展

- 修改 `generate_param_combinations()` 实现其他搜索策略
- 修改 `check_targets()` 自定义达标逻辑
- 添加更多分析指标
