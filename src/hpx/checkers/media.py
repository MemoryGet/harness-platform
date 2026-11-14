"""Deterministic media evidence collection using FFmpeg tools."""

import json
import re
import subprocess
from pathlib import Path


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=120, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"缺少媒体工具：{command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("媒体探测超时") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"媒体探测失败：{exc.stderr[-500:]}") from exc


def media_probe(artifact: Path) -> dict:
    """Collect metadata and EBU R128 integrated loudness; no pass/fail decision."""
    if not artifact.is_file():
        raise ValueError(f"媒体文件不存在：{artifact}")
    probe = json.loads(_run(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration,format_name:stream=codec_type,width,height,r_frame_rate",
                             "-of", "json", str(artifact)]).stdout)
    streams = probe.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    result = {"video": {"duration_sec": float(probe.get("format", {}).get("duration", 0))}}
    if video:
        result["video"].update({key: video[key] for key in ("width", "height") if key in video})
        rate = video.get("r_frame_rate", "0/1")
        numerator, denominator = map(int, rate.split("/"))
        result["video"]["fps"] = numerator / denominator if denominator else 0
    if audio:
        output = _run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(artifact),
                       "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
        loudness = re.findall(r"Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?) LUFS", output)
        peak = re.findall(r"True peak:\s*Peak:\s*(-?\d+(?:\.\d+)?) dBFS", output)
        if not loudness:
            raise RuntimeError("FFmpeg 未返回整体响度")
        result["audio"] = {"integrated_lufs": float(loudness[-1])}
        if peak:
            result["audio"]["true_peak_db"] = float(peak[-1])
    return result
