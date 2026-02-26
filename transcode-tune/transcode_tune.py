#!/usr/bin/env python3
"""
Transcode Parameter Tuner - 自动化转码参数迭代工具
"""

import json
import yaml
import time
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from typing import Any
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


def load_config(config_path: str) -> dict:
    """加载实验配置"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_template(template_path: str) -> dict:
    """加载JSON模板"""
    with open(template_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def set_nested_value(obj: dict, path: str, value: Any):
    """通过路径设置嵌套字典的值，如 'encoder.x264.bitrate' """
    keys = path.split('.')
    current = obj
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def inject_params(template: dict, params: dict[str, Any]) -> dict:
    """将参数注入模板"""
    result = deepcopy(template)
    for path, value in params.items():
        set_nested_value(result, path, value)
    return result


def generate_param_combinations(param_defs: dict) -> list[dict]:
    """生成参数组合（网格搜索）"""
    import itertools
    
    keys = list(param_defs.keys())
    values = [v if isinstance(v, list) else [v] for v in param_defs.values()]
    
    combinations = []
    for combo in itertools.product(*values):
        combinations.append(dict(zip(keys, combo)))
    return combinations


def trigger_transcode(api_url: str, payload: dict) -> bool:
    """触发转码任务"""
    try:
        resp = requests.post(api_url, json=payload, timeout=30)
        resp.raise_for_status()
        print(f"[OK] 转码任务已提交")
        return True
    except Exception as e:
        print(f"[ERROR] 提交转码失败: {e}")
        return False


def wait_for_file(remote_host: str, remote_user: str, remote_path: str, 
                  check_interval: int = 10, max_wait: int = 3600) -> bool:
    """轮询远程文件是否释放（可读）"""
    ssh_cmd = f"ssh {remote_user}@{remote_host} 'test -r {remote_path} && echo READY'"
    
    start = time.time()
    while time.time() - start < max_wait:
        result = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True)
        if "READY" in result.stdout:
            print(f"[OK] 文件已就绪: {remote_path}")
            return True
        print(f"[等待] 文件未就绪，{check_interval}秒后重试...")
        time.sleep(check_interval)
    
    print(f"[ERROR] 等待超时")
    return False


def download_file(remote_host: str, remote_user: str, remote_path: str, local_path: str) -> bool:
    """SCP下载文件"""
    scp_cmd = f"scp {remote_user}@{remote_host}:{remote_path} {local_path}"
    result = subprocess.run(scp_cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[OK] 文件已下载: {local_path}")
        return True
    print(f"[ERROR] 下载失败: {result.stderr}")
    return False


def analyze_video(video_path: str) -> dict:
    """使用ffprobe分析视频码率/I帧"""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format', '-show_streams',
        video_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(result.stdout)
        
        # 提取视频流信息
        video_stream = next((s for s in data.get('streams', []) if s['codec_type'] == 'video'), None)
        if not video_stream:
            return {}
        
        format_info = data.get('format', {})
        
        # 码率信息
        bitrate_avg = int(format_info.get('bit_rate', 0)) // 1000  # kbps
        
        # I帧分析（需要更复杂的ffprobe调用）
        iframe_result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
             '-show_entries', 'frame=pict_type,pkt_size',
             '-of', 'json', video_path],
            capture_output=True, text=True
        )
        
        frames = json.loads(iframe_result.stdout).get('frames', [])
        i_frames = [f for f in frames if f.get('pict_type') == 'I']
        iframe_sizes = [int(f.get('pkt_size', 0)) for f in i_frames if f.get('pkt_size')]
        
        iframe_avg = sum(iframe_sizes) // len(iframe_sizes) if iframe_sizes else 0
        iframe_max = max(iframe_sizes) if iframe_sizes else 0
        
        return {
            'bitrate_avg': bitrate_avg,
            'iframe_avg_size': iframe_avg,
            'iframe_max_size': iframe_max,
            'iframe_count': len(i_frames)
        }
    except Exception as e:
        print(f"[ERROR] 分析失败: {e}")
        return {}


def check_targets(analysis: dict, targets: dict) -> tuple[bool, list[str]]:
    """检查是否达标"""
    passed = True
    issues = []
    
    # 检查平均码率
    if 'bitrate_avg' in targets:
        diff = abs(analysis['bitrate_avg'] - targets['bitrate_avg'])
        tolerance = targets.get('bitrate_tolerance', targets['bitrate_avg'] * 0.05)
        if diff > tolerance:
            passed = False
            issues.append(f"码率偏差过大: {analysis['bitrate_avg']} vs 目标 {targets['bitrate_avg']}")
    
    # 检查码率极值
    if 'bitrate_max' in targets and analysis.get('bitrate_max', 0) > targets['bitrate_max']:
        passed = False
        issues.append(f"码率极值超标: {analysis.get('bitrate_max')} > {targets['bitrate_max']}")
    
    return passed, issues


def run_metrics(video_path: str, ref_path: str, metrics: list[str]) -> dict:
    """运行PSNR/VMAF评估"""
    results = {}
    
    if 'psnr' in metrics:
        cmd = [
            'ffmpeg', '-i', ref_path, '-i', video_path,
            '-lavfi', 'psnr', '-f', 'null', '-'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        # 解析PSNR值
        for line in result.stderr.split('\n'):
            if 'PSNR' in line:
                results['psnr'] = line
                break
    
    if 'vmaf' in metrics:
        cmd = [
            'ffmpeg', '-i', ref_path, '-i', video_path,
            '-lavfi', 'libvmaf', '-f', 'null', '-'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        for line in result.stderr.split('\n'):
            if 'VMAF' in line or 'vmaf' in line.lower():
                results['vmaf'] = line
                break
    
    return results


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='转码参数自动调优工具')
    parser.add_argument('--config', '-c', default='experiment.yaml', help='实验配置文件')
    parser.add_argument('--dry-run', action='store_true', help='只生成组合，不执行')
    args = parser.parse_args()
    
    # 加载配置
    print("📋 加载配置...")
    config = load_config(args.config)
    template = load_template(config['template'])
    
    param_defs = config.get('params', {})
    remote = config.get('remote', {})
    targets = config.get('targets', {})
    metrics = config.get('metrics', [])
    
    # 生成参数组合
    combinations = generate_param_combinations(param_defs)
    print(f"📊 生成 {len(combinations)} 种参数组合")
    
    if args.dry_run:
        for i, combo in enumerate(combinations, 1):
            print(f"  [{i}] {combo}")
        return
    
    # 准备工作目录
    work_dir = Path('./results') / datetime.now().strftime('%Y%m%d_%H%M%S')
    work_dir.mkdir(parents=True, exist_ok=True)
    
    results_log = []
    best_result = None
    best_score = float('inf')
    
    # 迭代
    for i, params in enumerate(combinations, 1):
        print(f"\n{'='*60}")
        print(f"🔄 迭代 [{i}/{len(combinations)}]")
        print(f"   参数: {params}")
        print(f"{'='*60}")
        
        # 注入参数
        payload = inject_params(template, params)
        
        # 保存本次配置
        config_path = work_dir / f'config_{i:03d}.json'
        with open(config_path, 'w') as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        
        # 触发转码
        if not trigger_transcode(config['api_url'], payload):
            continue
        
        # 等待完成
        output_filename = f"output_{i:03d}.mp4"
        remote_file = f"{remote['output_dir']}/{output_filename}"
        
        if not wait_for_file(remote['host'], remote['user'], remote_file):
            continue
        
        # 下载
        local_file = work_dir / output_filename
        if not download_file(remote['host'], remote['user'], remote_file, str(local_file)):
            continue
        
        # 分析
        analysis = analyze_video(str(local_file))
        print(f"📈 分析结果: {analysis}")
        
        # 检查达标
        passed, issues = check_targets(analysis, targets)
        
        result_record = {
            'iteration': i,
            'params': params,
            'analysis': analysis,
            'passed': passed,
            'issues': issues
        }
        
        if passed:
            print("✅ 达标！")
            # 计算评分（与目标的接近程度）
            score = abs(analysis['bitrate_avg'] - targets.get('bitrate_avg', 0))
            if score < best_score:
                best_score = score
                best_result = result_record
        else:
            print(f"⚠️ 未达标: {issues}")
        
        # 通知
        send_notification(
            f"转码迭代 {i}/{len(combinations)}",
            f"{'✅ 达标' if passed else '⚠️ 未达标'}\n码率: {analysis.get('bitrate_avg', 'N/A')} kbps"
        )
        
        results_log.append(result_record)
    
    # 最优结果跑PSNR/VMAF
    if best_result and metrics:
        print(f"\n{'='*60}")
        print("🏆 最优结果评估 PSNR/VMAF...")
        
        best_file = work_dir / f"output_{best_result['iteration']:03d}.mp4"
        metric_results = run_metrics(str(best_file), config['reference_video'], metrics)
        best_result['metrics'] = metric_results
        
        for m, v in metric_results.items():
            print(f"   {m}: {v}")
        
        send_notification("最优结果评估完成", f"迭代 #{best_result['iteration']}\n" + "\n".join(f"{k}: {v}" for k, v in metric_results.items()))
    
    # 保存日志
    log_path = work_dir / 'results.json'
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump({
            'config': config,
            'results': results_log,
            'best': best_result
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n📁 结果保存在: {work_dir}")
    print("🎉 完成！")


if __name__ == '__main__':
    main()
