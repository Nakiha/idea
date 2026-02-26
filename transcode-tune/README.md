# Transcode Parameter Tuner

自动化转码参数调优工具，支持批量文件处理和实验归档。

## 功能

- 📂 批量文件处理 (files.txt)
- 🔧 灵活的JSON路径配置
- 📊 参考流分析生成目标
- 🔄 自动迭代调参
- 🎯 达标检测
- 📢 Windows通知
- 📁 实验归档管理

## 安装

```bash
pip install -r requirements.txt
```

确保系统已安装 `ffmpeg`/`ffprobe` 和 `ssh`/`scp`。

## 快速开始

### 1. 创建新实验

```bash
python transcode_tune.py --new my_experiment
```

这会创建目录结构：
```
experiments/
└── my_experiment/
    ├── experiment.yaml   # 配置文件
    ├── base.json         # JSON模板
    ├── files.txt         # 文件列表
    └── results/          # 结果目录
```

### 2. 分析参考流（生成优化目标）

```bash
# 分析本地视频文件
python analyze_refs.py ./ref_videos/ -o targets.json

# 或从文件列表
python analyze_refs.py ref_files.txt -o targets.json
```

将输出的目标配置复制到 `experiment.yaml` 的 `targets` 部分。

### 3. 编辑配置

**experiment.yaml:**
```yaml
name: my_experiment

uri_paths:
  input: source.file.uri      # 根据你的JSON结构调整
  output: destination.uri

params:
  encoder.bitrate: [2000, 3000, 4000]
  encoder.crf: [18, 23, 28]

targets:
  bitrate_avg: 3500
  bitrate_max: 5000
```

**files.txt:**
```
file:///data/videos/source1.mp4
file:///data/videos/source2.mp4
file:///data/videos/source3.mp4
```

**base.json:**
```json
{
  "source": {
    "file": {
      "uri": "file:///input.mp4"
    }
  },
  "destination": {
    "uri": "file:///output.mp4"
  },
  "encoder": {
    "bitrate": 3000,
    "crf": 23
  }
}
```

### 4. 运行实验

```bash
# 预览参数组合
python transcode_tune.py experiments/my_experiment --dry-run

# 正式运行
python transcode_tune.py experiments/my_experiment
```

## 目录结构

```
transcode-tune/
├── transcode_tune.py    # 主程序
├── analyze_refs.py      # 参考流分析工具
├── requirements.txt     # 依赖
└── experiments/         # 实验目录
    ├── exp_001/
    │   ├── experiment.yaml
    │   ├── base.json
    │   ├── files.txt
    │   └── results/
    │       └── 20260226_173000/
    │           ├── task_0001_config.json
    │           ├── task_0001_output.mp4
    │           ├── task_0002_...
    │           └── summary.json
    └── exp_002/
        └── ...
```

## URI路径配置

由于不同服务的JSON结构不同，通过 `uri_paths` 配置输入输出位置：

```yaml
uri_paths:
  input: source.file.uri      # 对应 {"source": {"file": {"uri": "..."}}}
  output: destination.uri     # 对应 {"destination": {"uri": "..."}}
```

## 参数注入

使用点号路径访问嵌套字段：

```yaml
params:
  encoder.x264.bitrate: [2000, 3000]
  video.codec.profile: ["high", "main"]
```

会注入到：
```json
{
  "encoder": {
    "x264": {
      "bitrate": 3000
    }
  },
  "video": {
    "codec": {
      "profile": "high"
    }
  }
}
```

## 工作流程

```
files.txt (源流列表)     参考流
       │                   │
       │                   ▼
       │           analyze_refs.py
       │                   │
       │                   ▼
       │            targets.yaml
       │                   │
       ▼                   │
   base.json ◄─────────────┘
       │
       ▼
 transcode_tune.py
       │
       ├──► POST 触发转码
       ├──► SSH 轮询等待
       ├──► SCP 下载
       ├──► ffprobe 分析
       ├──► 达标检测
       │
       ▼
  results/summary.json
```

## 扩展

- 修改 `generate_param_combinations()` 实现其他搜索策略
- 修改 `check_targets()` 自定义达标逻辑
- 添加更多分析指标
