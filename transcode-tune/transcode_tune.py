#!/usr/bin/env python3
"""
Transcode Parameter Tuner - 自动化转码参数迭代工具
支持批量文件处理和实验归档
"""

import json
import yaml
import time
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from typing import Any, Optional
from copy import deepcopy

# Windows 通知
try:
    from win11toast import notify
    HAS_TOAST = True
except ImportError:
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        HAS_TOAST = True
    except ImportError:
        HAS_TOAST = False


def send_notification(title: str, message: str):
    """发送Windows通知"""
    if HAS_TOAST:
        try:
            if 'win11toast' in globals():
                notify(title, message)
            else:
                toaster.show_toast(title, message, duration=5)
        except Exception as e:
            print(f"[通知失败] {e}")
    print(f"\n{'='*50}")
    print(f"📢 {title}")
    print(f"   {message}")
    print(f"{'='*50}\n")


class Experiment:
    """实验管理"""
    
    def __init__(self, exp_dir: str):
        self.exp_dir = Path(exp_dir)
        self.config_path = self.exp_dir / 'experiment.yaml'
        self.template_path = self.exp_dir / 'base.json'
        self.files_path = self.exp_dir / 'files.txt'
        self.results_dir = self.exp_dir / 'results'
        
        self.config = None
        self.template = None
        self.files = []
        
    def load(self) -> bool:
        """加载实验配置"""
        if not self.config_path.exists():
            print(f"❌ 配置文件不存在: {self.config_path}")
            return False
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        # 加载模板
        template_path = self.config.get('template', 'base.json')
        if not Path(template_path).is_absolute():
            template_path = self.exp_dir / template_path
        
        with open(template_path, 'r', encoding='utf-8') as f:
            self.template = json.load(f)
        
        # 加载文件列表
        files_path = self.config.get('files', 'files.txt')
        if not Path(files_path).is_absolute():
            files_path = self.exp_dir / files_path
        
        if files_path.exists():
            with open(files_path, 'r', encoding='utf-8') as f:
                self.files = [line.strip() for line in f if line.strip()]
        else:
            print(f"⚠️ 文件列表不存在: {files_path}")
            self.files = []
        
        return True
    
    def create_structure(self, name: str):
        """创建新实验目录结构"""
        self.exp_dir = Path('experiments') / name
        self.exp_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(exist_ok=True)
        
        # 创建默认配置
        default_config = {
            'name': name,
            'template': 'base.json',
            'files': 'files.txt',
            'api_url': 'http://localhost:8080/api/transcode',
            'uri_paths': {
                'input': 'input.uri',      # JSON路径
                'output': 'output.uri',    # JSON路径
            },
            'params': {
                'encoder.bitrate': [2000, 3000, 4000],
            },
            'remote': {
                'host': '192.168.1.100',
                'user': 'transcode',
            },
            'targets': {
                'bitrate_avg': 3500,
                'bitrate_max': 5000,
            },
            'metrics': ['psnr', 'vmaf']
        }
        
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(default_config, f, default_flow_style=False, allow_unicode=True)
        
        # 创建空文件
        self.template_path.touch()
        self.files_path.touch()
        
        print(f"✅ 实验目录已创建: {self.exp_dir}")
        print(f"   请编辑以下文件:")
        print(f"   - {self.config_path}")
        print(f"   - {self.template_path}")
        print(f"   - {self.files_path}")


def set_nested_value(obj: dict, path: str, value: Any):
    """通过路径设置嵌套字典的值"""
    keys = path.split('.')
    current = obj
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def get_nested_value(obj: dict, path: str, default=None) -> Any:
    """通过路径获取嵌套字典的值"""
    keys = path.split('.')
    current = obj
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


def inject_params(template: dict, params: dict[str, Any]) -> dict:
    """将参数注入模板"""
    result = deepcopy(template)
    for path, value in params.items():
        set_nested_value(result, path, value)
    return result


def generate_param_combinations(param_defs: dict) -> list[dict]:
    """生成参数组合"""
    import itertools
    
    keys = list(param_defs.keys())
    values = [v if isinstance(v, list) else [v] for v in param_defs.values()]
    
    combinations = []
    for combo in itertools.product(*values):
        combinations.append(dict(zip(keys, combo)))
    return combinations


def trigger_transcode(api_url: str, payload: dict) -> tuple[bool, Optional[str]]:
    """触发转码任务，返回 (成功, 任务ID或错误信息)"""
    try:
        resp = requests.post(api_url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get('task_id') or data.get('id') or 'unknown'
        print(f"[OK] 任务已提交: {task_id}")
        return True, task_id
    except Exception as e:
        print(f"[ERROR] 提交失败: {e}")
        return False, str(e)


def ssh_command(host: str, user: str, cmd: str, timeout: int = 30) -> tuple[bool, str]:
    """执行SSH命令"""
    ssh_cmd = f"ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no {user}@{host} '{cmd}'"
    result = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.returncode == 0, result.stdout.strip()


def wait_for_output(host: str, user: str, output_uri: str, 
                    check_interval: int = 10, max_wait: int = 3600) -> bool:
    """等待远程输出文件就绪"""
    # 从 file:///path/to/file.mp4 提取路径
    if output_uri.startswith('file:'):
        remote_path = output_uri[5:]  # 去掉 file:
    else:
        remote_path = output_uri
    
    # 轮询检查
    start = time.time()
    while time.time() - start < max_wait:
        success, output = ssh_command(host, user, f"test -f {remote_path} && echo READY")
        if "READY" in output:
            # 额外检查文件是否完整（没有被写入）
            success, size1 = ssh_command(host, user, f"stat -c %s {remote_path} 2>/dev/null")
            if success and size1:
                time.sleep(2)
                success, size2 = ssh_command(host, user, f"stat -c %s {remote_path} 2>/dev/null")
                if size1 == size2 and size1 != '0':
                    print(f"[OK] 文件已就绪: {remote_path}")
                    return True
        
        print(f"[等待] {check_interval}秒后重试...")
        time.sleep(check_interval)
    
    return False


def download_file(host: str, user: str, remote_uri: str, local_path: str) -> bool:
    """下载文件"""
    if remote_uri.startswith('file:'):
        remote_path = remote_uri[5:]
    else:
        remote_path = remote_uri
    
    scp_cmd = f"scp {user}@{host}:{remote_path} {local_path}"
    result = subprocess.run(scp_cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode == 0:
        print(f"[OK] 已下载: {local_path}")
        return True
    print(f"[ERROR] 下载失败: {result.stderr}")
    return False


def analyze_video(video_path: str) -> dict:
    """分析视频码率/I帧"""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format', '-show_streams',
        video_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(result.stdout)
        
        video_stream = next(
            (s for s in data.get('streams', []) if s['codec_type'] == 'video'),
            None
        )
        if not video_stream:
            return {'error': 'No video stream'}
        
        format_info = data.get('format', {})
        bitrate_avg = int(format_info.get('bit_rate', 0)) // 1000
        
        # I帧分析
        frame_cmd = [
            'ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
            '-show_entries', 'frame=pict_type,pkt_size',
            '-of', 'json', video_path
        ]
        frame_result = subprocess.run(frame_cmd, capture_output=True, text=True, timeout=120)
        frames = json.loads(frame_result.stdout).get('frames', [])
        
        i_frames = [f for f in frames if f.get('pict_type') == 'I']
        iframe_sizes = [int(f.get('pkt_size', 0)) for f in i_frames if f.get('pkt_size')]
        
        return {
            'bitrate_avg': bitrate_avg,
            'iframe_avg_size': sum(iframe_sizes) // len(iframe_sizes) if iframe_sizes else 0,
            'iframe_max_size': max(iframe_sizes) if iframe_sizes else 0,
            'iframe_count': len(i_frames),
            'duration': float(format_info.get('duration', 0)),
            'resolution': f"{video_stream.get('width')}x{video_stream.get('height')}",
        }
    except Exception as e:
        return {'error': str(e)}


def check_targets(analysis: dict, targets: dict) -> tuple[bool, list[str]]:
    """检查是否达标"""
    passed = True
    issues = []
    
    if 'bitrate_avg' in targets and 'bitrate_avg' in analysis:
        target = targets['bitrate_avg']
        actual = analysis['bitrate_avg']
        tolerance = targets.get('bitrate_tolerance', target * 0.05)
        if abs(actual - target) > tolerance:
            passed = False
            issues.append(f"码率偏差: {actual} vs 目标 {target} (±{tolerance})")
    
    if 'bitrate_max' in targets and analysis.get('bitrate_max', 0) > targets['bitrate_max']:
        passed = False
        issues.append(f"码率超标: {analysis['bitrate_max']} > {targets['bitrate_max']}")
    
    if 'iframe_avg_size' in targets and 'iframe_avg_size' in analysis:
        target = targets['iframe_avg_size']
        actual = analysis['iframe_avg_size']
        tolerance = targets.get('iframe_tolerance', target * 0.1)
        if abs(actual - target) > tolerance:
            passed = False
            issues.append(f"I帧偏差: {actual} vs 目标 {target}")
    
    return passed, issues


def run_metrics(video_path: str, ref_path: str, metrics: list[str]) -> dict:
    """运行PSNR/VMAF"""
    results = {}
    
    if 'psnr' in metrics:
        cmd = ['ffmpeg', '-i', ref_path, '-i', video_path,
               '-lavfi', 'psnr', '-f', 'null', '-']
        result = subprocess.run(cmd, capture_output=True, text=True)
        for line in result.stderr.split('\n'):
            if 'PSNR' in line:
                results['psnr'] = line.strip()
                break
    
    if 'vmaf' in metrics:
        cmd = ['ffmpeg', '-i', ref_path, '-i', video_path,
               '-lavfi', 'libvmaf', '-f', 'null', '-']
        result = subprocess.run(cmd, capture_output=True, text=True)
        for line in result.stderr.split('\n'):
            if 'VMAF' in line.lower():
                results['vmaf'] = line.strip()
                break
    
    return results


def run_experiment(exp: Experiment, dry_run: bool = False):
    """运行实验"""
    
    config = exp.config
    template = exp.template
    files = exp.files
    remote = config.get('remote', {})
    uri_paths = config.get('uri_paths', {})
    targets = config.get('targets', {})
    metrics = config.get('metrics', [])
    
    # 获取URI路径配置
    input_uri_path = uri_paths.get('input', 'input.uri')
    output_uri_path = uri_paths.get('output', 'output.uri')
    
    # 生成参数组合
    param_defs = config.get('params', {})
    combinations = generate_param_combinations(param_defs)
    
    print(f"\n{'='*60}")
    print(f"📋 实验配置: {config.get('name', 'unnamed')}")
    print(f"📊 参数组合: {len(combinations)} 种")
    print(f"📁 文件数量: {len(files)}")
    print(f"🎯 目标: {targets}")
    print(f"{'='*60}\n")
    
    if dry_run:
        print("参数组合预览:")
        for i, combo in enumerate(combinations, 1):
            print(f"  [{i}] {combo}")
        print("\n文件列表:")
        for f in files:
            print(f"  - {f}")
        return
    
    # 准备结果目录
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = exp.results_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    
    all_results = []
    best_result = None
    best_score = float('inf')
    
    total_tasks = len(combinations) * len(files)
    task_idx = 0
    
    # 遍历参数组合
    for param_idx, params in enumerate(combinations, 1):
        print(f"\n{'='*60}")
        print(f"🔄 参数组合 [{param_idx}/{len(combinations)}]")
        print(f"   {params}")
        print(f"{'='*60}")
        
        param_results = {
            'param_index': param_idx,
            'params': params,
            'files': []
        }
        
        # 遍历文件
        for file_idx, input_uri in enumerate(files, 1):
            task_idx += 1
            
            # 生成输出URI
            # 假设输入 file:///path/to/video.mp4 -> 输出 file:///path/to/video_out_paramX.mp4
            input_basename = Path(input_uri.replace('file:', '')).stem
            output_uri = f"file:/tmp/output/{input_basename}_p{param_idx}.mp4"
            
            print(f"\n[任务 {task_idx}/{total_tasks}] 处理: {input_uri}")
            print(f"  输出: {output_uri}")
            
            # 构建请求payload
            payload = inject_params(template, params)
            set_nested_value(payload, input_uri_path, input_uri)
            set_nested_value(payload, output_uri_path, output_uri)
            
            # 保存配置
            task_config_path = run_dir / f'task_{task_idx:04d}_config.json'
            with open(task_config_path, 'w') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            
            # 触发转码
            success, task_id = trigger_transcode(config['api_url'], payload)
            if not success:
                continue
            
            # 等待完成
            if not wait_for_output(remote['host'], remote['user'], output_uri):
                print(f"[ERROR] 等待超时")
                continue
            
            # 下载
            local_file = run_dir / f'task_{task_idx:04d}_output.mp4'
            if not download_file(remote['host'], remote['user'], output_uri, str(local_file)):
                continue
            
            # 分析
            analysis = analyze_video(str(local_file))
            print(f"📈 分析: {analysis}")
            
            # 检查达标
            passed, issues = check_targets(analysis, targets)
            
            file_result = {
                'file_index': file_idx,
                'input_uri': input_uri,
                'output_uri': output_uri,
                'analysis': analysis,
                'passed': passed,
                'issues': issues
            }
            
            if passed:
                print(f"✅ 达标!")
                # 评分
                score = abs(analysis.get('bitrate_avg', 0) - targets.get('bitrate_avg', 0))
                if score < best_score:
                    best_score = score
                    best_result = {
                        'param_index': param_idx,
                        'params': params,
                        'file_result': file_result
                    }
            else:
                print(f"⚠️ 未达标: {issues}")
            
            # 通知
            send_notification(
                f"任务 {task_idx}/{total_tasks}",
                f"{'✅' if passed else '⚠️'} {Path(input_uri).name}\n码率: {analysis.get('bitrate_avg', 'N/A')} kbps"
            )
            
            param_results['files'].append(file_result)
        
        all_results.append(param_results)
    
    # 最优结果评估
    if best_result and metrics:
        print(f"\n{'='*60}")
        print(f"🏆 最优结果 - 参数 #{best_result['param_index']}")
        print(f"{'='*60}")
        
        best_file = run_dir / f"task_{best_result['param_index']:04d}_output.mp4"
        # TODO: 参考视频路径需要配置
        ref_video = config.get('reference_video')
        if ref_video and Path(ref_video).exists():
            metric_results = run_metrics(str(best_file), ref_video, metrics)
            best_result['metrics'] = metric_results
            print(f"📊 质量评估:")
            for m, v in metric_results.items():
                print(f"   {m}: {v}")
            
            send_notification("最优结果评估完成", 
                            f"参数 #{best_result['param_index']}\n" + 
                            "\n".join(f"{k}: {v}" for k, v in metric_results.items()))
    
    # 保存结果
    summary = {
        'experiment': config.get('name'),
        'timestamp': timestamp,
        'total_tasks': total_tasks,
        'passed_tasks': sum(1 for r in all_results for f in r['files'] if f['passed']),
        'best': best_result,
        'all_results': all_results
    }
    
    with open(run_dir / 'summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"\n📁 结果已保存: {run_dir}")
    print(f"🎉 实验完成!")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='转码参数自动调优')
    parser.add_argument('experiment', nargs='?', help='实验目录路径')
    parser.add_argument('--new', '-n', metavar='NAME', help='创建新实验')
    parser.add_argument('--dry-run', action='store_true', help='预览不执行')
    args = parser.parse_args()
    
    exp = Experiment(args.experiment or '.')
    
    if args.new:
        exp.create_structure(args.new)
        return
    
    if not args.experiment:
        print("用法: python transcode_tune.py <实验目录>")
        print("      python transcode_tune.py --new <实验名>")
        print("\n可用实验:")
        exps_dir = Path('experiments')
        if exps_dir.exists():
            for d in exps_dir.iterdir():
                if d.is_dir() and (d / 'experiment.yaml').exists():
                    print(f"  - {d.name}")
        return
    
    if not exp.load():
        return
    
    run_experiment(exp, args.dry_run)


if __name__ == '__main__':
    main()
