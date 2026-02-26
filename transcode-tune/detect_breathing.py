#!/usr/bin/env python3
"""
呼吸效应检测工具
通过帧级PSNR/SSIM分析检测视频呼吸效应
"""

import json
import subprocess
import numpy as np
from pathlib import Path
from typing import Optional
import matplotlib.pyplot as plt


def compute_frame_psnr(distorted: str, reference: str) -> list[dict]:
    """
    逐帧计算PSNR
    返回: [{'frame': 0, 'pts': 0.0, 'pict_type': 'I', 'psnr': 42.5}, ...]
    """
    
    # 使用ffmpeg的fpsync滤镜逐帧对比
    cmd = [
        'ffmpeg', '-i', reference, '-i', distorted,
        '-lavfi', 'psnr=stats_file=-',  # 输出到stdout
        '-f', 'null', '-'
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # 解析PSNR输出
    frames = []
    for line in result.stderr.split('\n'):
        if line.startswith('n:') or 'psnr' in line.lower():
            # 格式: n:0 pts:0 ... psnr:42.5 ...
            parts = {}
            for item in line.split():
                if ':' in item:
                    k, v = item.split(':', 1)
                    parts[k] = v
            
            if 'n' in parts:
                frames.append({
                    'frame': int(parts.get('n', 0)),
                    'pts': float(parts.get('pts', 0)),
                    'psnr': float(parts.get('psnr', 0)) if 'psnr' in parts else None
                })
    
    # 如果上面解析失败，用另一种方法
    if not frames:
        frames = compute_frame_psnr_vmaf(distorted, reference)
    
    return frames


def compute_frame_psnr_vmaf(distorted: str, reference: str) -> list[dict]:
    """
    使用libvmaf的per-frame模式计算PSNR
    """
    
    cmd = [
        'ffmpeg', '-i', reference, '-i', distorted,
        '-lavfi', (
            "libvmaf="
            "model=version=vmaf_v0.6.1:"
            "feature=name=psnr|name=ssim:"
            "log_path=-:"
            "log_fmt=json"
        ),
        '-f', 'null', '-'
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # VMAF会输出JSON到stderr
    try:
        # 找到JSON部分
        output = result.stderr
        json_start = output.find('{')
        if json_start >= 0:
            data = json.loads(output[json_start:])
            frames = []
            for frame_data in data.get('frames', []):
                metrics = frame_data.get('metrics', {})
                frames.append({
                    'frame': frame_data.get('frameNum', 0),
                    'pts': frame_data.get('pooled_metrics', {}).get('psnr', {}).get('min', 0),
                    'psnr': metrics.get('psnr', 0),
                    'ssim': metrics.get('ssim', 0),
                    'vmaf': metrics.get('vmaf', 0)
                })
            return frames
    except Exception as e:
        print(f"[WARN] JSON解析失败: {e}")
    
    return []


def get_frame_types(video: str) -> list[dict]:
    """
    获取每帧的类型(I/P/B)和大小
    """
    
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-select_streams', 'v:0',
        '-show_entries', 'frame=pict_type,pkt_size,pkt_pts_time',
        '-of', 'json',
        video
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    
    frames = []
    for i, f in enumerate(data.get('frames', [])):
        frames.append({
            'frame': i,
            'pts': float(f.get('pkt_pts_time', 0)),
            'pict_type': f.get('pict_type', '?'),
            'size': int(f.get('pkt_size', 0))
        })
    
    return frames


def analyze_breathing(video: str, reference: Optional[str] = None, 
                      output_dir: Optional[str] = None) -> dict:
    """
    分析呼吸效应
    
    Args:
        video: 待分析视频
        reference: 参考视频(原片)，如果提供则计算PSNR
        output_dir: 输出目录
    
    Returns:
        分析结果
    """
    
    print(f"📹 分析视频: {video}")
    
    # 获取帧类型和大小
    print("  获取帧信息...")
    frame_info = get_frame_types(video)
    
    # 分离I/P/B帧
    i_frames = [f for f in frame_info if f['pict_type'] == 'I']
    p_frames = [f for f in frame_info if f['pict_type'] == 'P']
    b_frames = [f for f in frame_info if f['pict_type'] == 'B']
    
    print(f"  总帧数: {len(frame_info)}, I帧: {len(i_frames)}, P帧: {len(p_frames)}, B帧: {len(b_frames)}")
    
    # 帧大小统计
    i_sizes = [f['size'] for f in i_frames if f['size'] > 0]
    p_sizes = [f['size'] for f in p_frames if f['size'] > 0]
    b_sizes = [f['size'] for f in b_frames if f['size'] > 0]
    
    i_avg = np.mean(i_sizes) if i_sizes else 0
    p_avg = np.mean(p_sizes) if p_sizes else 0
    b_avg = np.mean(b_sizes) if b_sizes else 0
    
    # 计算帧大小波动系数
    all_sizes = [f['size'] for f in frame_info if f['size'] > 0]
    size_std = np.std(all_sizes) if all_sizes else 0
    size_mean = np.mean(all_sizes) if all_sizes else 0
    size_cv = (size_std / size_mean * 100) if size_mean > 0 else 0  # 变异系数%
    
    # GOP分析 - 检测周期性
    if len(i_frames) >= 2:
        gop_lengths = []
        for i in range(1, len(i_frames)):
            gop_lengths.append(i_frames[i]['frame'] - i_frames[i-1]['frame'])
        avg_gop = np.mean(gop_lengths) if gop_lengths else 0
        gop_std = np.std(gop_lengths) if gop_lengths else 0
    else:
        avg_gop = 0
        gop_std = 0
    
    result = {
        'video': str(video),
        'total_frames': len(frame_info),
        'frame_counts': {
            'I': len(i_frames),
            'P': len(p_frames),
            'B': len(b_frames)
        },
        'frame_sizes': {
            'I_avg': int(i_avg),
            'P_avg': int(p_avg),
            'B_avg': int(b_avg),
            'I_P_ratio': round(i_avg / p_avg, 2) if p_avg > 0 else 0,
            'size_cv': round(size_cv, 2)  # 变异系数，越大波动越剧烈
        },
        'gop': {
            'avg_length': round(avg_gop, 1),
            'std': round(gop_std, 1)
        }
    }
    
    # 如果有参考视频，计算帧级PSNR
    if reference and Path(reference).exists():
        print(f"  计算帧级PSNR (参考: {reference})...")
        psnr_frames = compute_frame_psnr(video, reference)
        
        if psnr_frames:
            # 合并帧信息
            for i, f in enumerate(frame_info):
                if i < len(psnr_frames):
                    f.update(psnr_frames[i])
            
            # 按帧类型统计PSNR
            i_psnrs = [f.get('psnr', 0) for f in frame_info if f['pict_type'] == 'I' and f.get('psnr')]
            p_psnrs = [f.get('psnr', 0) for f in frame_info if f['pict_type'] == 'P' and f.get('psnr')]
            b_psnrs = [f.get('psnr', 0) for f in frame_info if f['pict_type'] == 'B' and f.get('psnr')]
            
            all_psnrs = [f.get('psnr', 0) for f in frame_info if f.get('psnr')]
            psnr_mean = np.mean(all_psnrs) if all_psnrs else 0
            psnr_std = np.std(all_psnrs) if all_psnrs else 0
            psnr_range = (max(all_psnrs) - min(all_psnrs)) if all_psnrs else 0
            
            result['psnr'] = {
                'mean': round(psnr_mean, 2),
                'std': round(psnr_std, 2),
                'range': round(psnr_range, 2),
                'I_mean': round(np.mean(i_psnrs), 2) if i_psnrs else 0,
                'P_mean': round(np.mean(p_psnrs), 2) if p_psnrs else 0,
                'B_mean': round(np.mean(b_psnrs), 2) if b_psnrs else 0,
            }
    
    # 呼吸效应评估
    breathing_score = 0
    issues = []
    
    # 1. 帧大小变异系数过大
    if size_cv > 50:
        breathing_score += 30
        issues.append(f"帧大小波动剧烈 (CV={size_cv:.1f}%)")
    elif size_cv > 30:
        breathing_score += 15
        issues.append(f"帧大小波动较大 (CV={size_cv:.1f}%)")
    
    # 2. I帧和P帧大小比例过大
    i_p_ratio = result['frame_sizes']['I_P_ratio']
    if i_p_ratio > 5:
        breathing_score += 30
        issues.append(f"I/P帧大小差异过大 (ratio={i_p_ratio:.1f})")
    elif i_p_ratio > 3:
        breathing_score += 15
        issues.append(f"I/P帧大小差异较大 (ratio={i_p_ratio:.1f})")
    
    # 3. PSNR波动
    if 'psnr' in result:
        psnr_range = result['psnr']['range']
        if psnr_range > 5:
            breathing_score += 40
            issues.append(f"PSNR波动过大 (range={psnr_range:.1f}dB)")
        elif psnr_range > 3:
            breathing_score += 20
            issues.append(f"PSNR波动较大 (range={psnr_range:.1f}dB)")
        
        # I帧和P帧PSNR差异
        if result['psnr']['I_mean'] > 0 and result['psnr']['P_mean'] > 0:
            psnr_diff = result['psnr']['I_mean'] - result['psnr']['P_mean']
            if psnr_diff > 2:
                breathing_score += 20
                issues.append(f"I/P帧PSNR差异大 (Δ={psnr_diff:.1f}dB)")
    
    # 评级
    if breathing_score >= 70:
        rating = "🔴 严重"
    elif breathing_score >= 40:
        rating = "🟡 中等"
    elif breathing_score >= 20:
        rating = "🟢 轻微"
    else:
        rating = "✅ 正常"
    
    result['breathing'] = {
        'score': breathing_score,
        'rating': rating,
        'issues': issues
    }
    
    # 输出报告
    print("\n" + "="*50)
    print("📊 呼吸效应分析报告")
    print("="*50)
    print(f"评级: {rating} (分数: {breathing_score})")
    
    if issues:
        print("\n⚠️ 问题:")
        for issue in issues:
            print(f"  - {issue}")
    
    print(f"\n帧统计:")
    print(f"  I帧: {result['frame_counts']['I']} (avg size: {i_avg:.0f})")
    print(f"  P帧: {result['frame_counts']['P']} (avg size: {p_avg:.0f})")
    print(f"  B帧: {result['frame_counts']['B']} (avg size: {b_avg:.0f})")
    print(f"  I/P比例: {i_p_ratio}")
    print(f"  帧大小CV: {size_cv:.1f}%")
    
    if 'psnr' in result:
        print(f"\nPSNR:")
        print(f"  平均: {result['psnr']['mean']:.2f} dB")
        print(f"  波动: ±{result['psnr']['std']:.2f} dB (范围: {result['psnr']['range']:.2f})")
        print(f"  I帧: {result['psnr']['I_mean']:.2f}, P帧: {result['psnr']['P_mean']:.2f}")
    
    print("="*50)
    
    # 保存结果
    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        
        # JSON结果
        with open(out_path / 'breathing_analysis.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        # 生成图表
        if len(frame_info) > 0:
            generate_plots(frame_info, out_path)
        
        print(f"\n📁 结果已保存: {out_path}")
    
    return result


def generate_plots(frame_info: list[dict], output_dir: Path):
    """生成分析图表"""
    
    try:
        fig, axes = plt.subplots(2, 1, figsize=(14, 8))
        
        frames = [f['frame'] for f in frame_info]
        sizes = [f['size'] / 1024 for f in frame_info]  # KB
        types = [f['pict_type'] for f in frame_info]
        
        # 帧大小图
        ax1 = axes[0]
        colors = ['red' if t == 'I' else 'blue' if t == 'P' else 'green' 
                  for t in types]
        ax1.scatter(frames, sizes, c=colors, s=1, alpha=0.6)
        ax1.set_xlabel('Frame')
        ax1.set_ylabel('Frame Size (KB)')
        ax1.set_title('Frame Size Distribution (Red=I, Blue=P, Green=B)')
        ax1.grid(True, alpha=0.3)
        
        # PSNR图 (如果有)
        ax2 = axes[1]
        psnrs = [f.get('psnr') for f in frame_info]
        valid_psnr = [p for p in psnrs if p is not None and p > 0]
        
        if valid_psnr:
            ax2.plot(frames, psnrs, linewidth=0.5, alpha=0.8)
            ax2.set_xlabel('Frame')
            ax2.set_ylabel('PSNR (dB)')
            ax2.set_title('Per-Frame PSNR')
            ax2.grid(True, alpha=0.3)
            
            # 标记I帧位置
            i_frame_nums = [f['frame'] for f in frame_info if f['pict_type'] == 'I']
            for i_fn in i_frame_nums[::max(1, len(i_frame_nums)//20)]:  # 最多标记20个
                ax2.axvline(x=i_fn, color='red', alpha=0.2, linewidth=0.5)
        else:
            ax2.text(0.5, 0.5, 'No PSNR data\n(requires reference video)', 
                    ha='center', va='center', transform=ax2.transAxes)
            ax2.set_title('Per-Frame PSNR (No Data)')
        
        plt.tight_layout()
        plt.savefig(output_dir / 'breathing_analysis.png', dpi=150)
        plt.close()
        
        print(f"📈 图表已生成: {output_dir / 'breathing_analysis.png'}")
        
    except ImportError:
        print("⚠️ matplotlib未安装，跳过图表生成")
    except Exception as e:
        print(f"⚠️ 图表生成失败: {e}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='呼吸效应检测工具')
    parser.add_argument('video', help='待分析视频')
    parser.add_argument('-r', '--reference', help='参考视频(原片)')
    parser.add_argument('-o', '--output', default='./breathing_result', help='输出目录')
    
    args = parser.parse_args()
    
    analyze_breathing(args.video, args.reference, args.output)


if __name__ == '__main__':
    main()
