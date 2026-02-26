#!/usr/bin/env python3
"""
参考流分析工具
分析客户已调好的出流，提取码率/I帧等信息作为优化目标
"""

import json
import subprocess
from pathlib import Path
from typing import Optional


def analyze_video(video_path: str) -> dict:
    """使用ffprobe分析视频"""
    
    # 基础信息
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format', '-show_streams',
        video_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    
    video_stream = next(
        (s for s in data.get('streams', []) if s.get('codec_type') == 'video'),
        None
    )
    if not video_stream:
        return {'error': 'No video stream found'}
    
    format_info = data.get('format', {})
    
    # 帧分析 - 获取所有帧信息
    frame_cmd = [
        'ffprobe', '-v', 'quiet',
        '-select_streams', 'v:0',
        '-show_entries', 'frame=pict_type,pkt_size,pkt_pts_time',
        '-of', 'json',
        video_path
    ]
    
    frame_result = subprocess.run(frame_cmd, capture_output=True, text=True)
    frames = json.loads(frame_result.stdout).get('frames', [])
    
    # 分类统计
    i_frames = [f for f in frames if f.get('pict_type') == 'I']
    p_frames = [f for f in frames if f.get('pict_type') == 'P']
    b_frames = [f for f in frames if f.get('pict_type') == 'B']
    
    iframe_sizes = [int(f['pkt_size']) for f in i_frames if f.get('pkt_size')]
    pframe_sizes = [int(f['pkt_size']) for f in p_frames if f.get('pkt_size')]
    bframe_sizes = [int(f['pkt_size']) for f in b_frames if f.get('pkt_size')]
    
    # 码率统计 - 按时间窗口计算
    bitrate_samples = []
    window_size = 1.0  # 1秒窗口
    
    if frames:
        # 按时间分组计算瞬时码率
        frame_data = [(float(f.get('pkt_pts_time', 0)), int(f.get('pkt_size', 0))) 
                      for f in frames if f.get('pkt_pts_time') is not None]
        frame_data.sort(key=lambda x: x[0])
        
        if frame_data:
            duration = frame_data[-1][0] - frame_data[0][0]
            total_bytes = sum(f[1] for f in frame_data)
            avg_bitrate = (total_bytes * 8) / duration / 1000 if duration > 0 else 0  # kbps
            
            # 计算每秒码率
            for t in range(int(frame_data[0][0]), int(frame_data[-1][0]) + 1):
                window_bytes = sum(f[1] for f in frame_data 
                                   if t <= f[0] < t + window_size)
                if window_bytes > 0:
                    bitrate_samples.append(window_bytes * 8 / window_size / 1000)
    
    return {
        'file': str(video_path),
        'duration': float(format_info.get('duration', 0)),
        'bitrate_avg_kbps': int(format_info.get('bit_rate', 0)) // 1000,
        'bitrate_max_kbps': int(max(bitrate_samples)) if bitrate_samples else 0,
        'bitrate_min_kbps': int(min(bitrate_samples)) if bitrate_samples else 0,
        'iframe': {
            'count': len(i_frames),
            'avg_size': int(sum(iframe_sizes) / len(iframe_sizes)) if iframe_sizes else 0,
            'max_size': max(iframe_sizes) if iframe_sizes else 0,
            'min_size': min(iframe_sizes) if iframe_sizes else 0,
        },
        'pframe': {
            'count': len(p_frames),
            'avg_size': int(sum(pframe_sizes) / len(pframe_sizes)) if pframe_sizes else 0,
        },
        'bframe': {
            'count': len(b_frames),
            'avg_size': int(sum(bframe_sizes) / len(bframe_sizes)) if bframe_sizes else 0,
        },
        'resolution': f"{video_stream.get('width', 0)}x{video_stream.get('height', 0)}",
        'fps': eval(video_stream.get('r_frame_rate', '0/1')),
        'codec': video_stream.get('codec_name', 'unknown'),
    }


def analyze_batch(files_path: str, output: Optional[str] = None) -> dict:
    """批量分析文件"""
    
    files = Path(files_path)
    if not files.exists():
        print(f"❌ 文件不存在: {files_path}")
        return {}
    
    video_files = []
    if files.is_file():
        # 读取文件列表
        with open(files, 'r', encoding='utf-8') as f:
            video_files = [line.strip() for line in f if line.strip()]
    else:
        # 当作目录处理
        video_files = list(files.glob('*.mp4')) + list(files.glob('*.mkv'))
        video_files = [str(f) for f in video_files]
    
    results = []
    for i, video_path in enumerate(video_files, 1):
        print(f"[{i}/{len(video_files)}] 分析: {video_path}")
        result = analyze_video(video_path)
        results.append(result)
        
        # 实时输出
        print(f"  码率: {result.get('bitrate_avg_kbps', 'N/A')} kbps")
        print(f"  I帧: avg={result['iframe']['avg_size']}, max={result['iframe']['max_size']}")
    
    # 汇总统计
    if results:
        summary = {
            'files_count': len(results),
            'bitrate': {
                'avg': sum(r['bitrate_avg_kbps'] for r in results) / len(results),
                'max': max(r['bitrate_max_kbps'] for r in results),
                'min': min(r['bitrate_min_kbps'] for r in results),
            },
            'iframe': {
                'avg_size': sum(r['iframe']['avg_size'] for r in results) / len(results),
                'max_size': max(r['iframe']['max_size'] for r in results),
            },
            'details': results
        }
    else:
        summary = {'files_count': 0, 'details': []}
    
    # 输出
    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 结果已保存: {out_path}")
    
    # 生成推荐目标配置
    print("\n" + "="*50)
    print("📊 推荐优化目标 (可复制到 experiment.yaml):")
    print("="*50)
    print(f"""
targets:
  bitrate_avg: {int(summary['bitrate']['avg'])}
  bitrate_max: {int(summary['bitrate']['max'])}
  iframe_avg_size: {int(summary['iframe']['avg_size'])}
  iframe_max_size: {int(summary['iframe']['max_size'])}
""")
    
    return summary


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='参考流分析工具')
    parser.add_argument('files', help='文件列表(files.txt)或目录路径')
    parser.add_argument('-o', '--output', default=None, help='输出JSON路径')
    parser.add_argument('--remote', default=None, help='远程主机 (user@host)，会通过SSH分析')
    
    args = parser.parse_args()
    
    if args.remote:
        # TODO: 支持远程分析
        print("⚠️ 远程分析暂未实现，请先下载到本地")
        return
    
    analyze_batch(args.files, args.output)


if __name__ == '__main__':
    main()
