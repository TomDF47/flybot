from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay FlyBot session recordings")
    parser.add_argument("recording_dir", type=Path, nargs="?", help="Recording directory to replay")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("artifacts/recordings"),
        help="Recording root path used for --list",
    )
    parser.add_argument("--list", action="store_true", help="List available recording directories")
    parser.add_argument("--fps", type=int, default=8, help="Frames per second for MP4 export")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="MP4 output path (defaults to <recording_dir>/session.mp4)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Serve the recording directory over a local HTTP server after preparing output",
    )
    parser.add_argument("--port", type=int, default=8765, help="Port for --serve")
    return parser.parse_args()


def list_recordings(recording_root: Path) -> None:
    if not recording_root.exists():
        print(f"No recordings found at {recording_root}")
        return
    recording_directories = sorted(path for path in recording_root.iterdir() if path.is_dir())
    if not recording_directories:
        print(f"No recordings found at {recording_root}")
        return
    print(f"Recordings under {recording_root}:")
    for recording_directory in recording_directories:
        frame_count = len(list((recording_directory / "frames").glob("frame_*.png")))
        telemetry_path = recording_directory / "telemetry.jsonl"
        telemetry_exists = telemetry_path.exists()
        print(
            f"- {recording_directory} "
            f"(frames={frame_count}, telemetry={'yes' if telemetry_exists else 'no'})"
        )


def export_recording_mp4(recording_directory: Path, output_path: Path, fps: int) -> bool:
    frames_directory = recording_directory / "frames"
    if not frames_directory.exists():
        raise SystemExit(f"Frames directory not found: {frames_directory}")
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    input_pattern = str(frames_directory / "frame_%06d.png")
    subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-framerate",
            str(fps),
            "-i",
            input_pattern,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ],
        check=True,
    )
    print(f"Wrote MP4 replay to {output_path}")
    return True


def write_html_scrubber(recording_directory: Path, frames: list[Path]) -> Path:
    html_path = recording_directory / "replay.html"
    frame_sources = ",".join(f'"frames/{frame.name}"' for frame in frames)
    html_path.write_text(
        (
            "<!doctype html>\n"
            "<html><head><meta charset='utf-8'><title>FlyBot Session Replay</title>"
            "<style>body{background:#0e1328;color:#ecf1ff;font-family:system-ui;padding:1rem}"
            "img{max-width:100%;border:1px solid #3d4a75;border-radius:8px}"
            "input{width:100%}</style></head><body>\n"
            "<h1>FlyBot Session Replay</h1>\n"
            "<p id='status'></p>\n"
            "<input id='slider' type='range' min='0' value='0' step='1'>\n"
            "<img id='frame' alt='FlyBot frame'>\n"
            "<script>\n"
            f"const frames=[{frame_sources}];\n"
            "const slider=document.getElementById('slider');\n"
            "const frame=document.getElementById('frame');\n"
            "const status=document.getElementById('status');\n"
            "slider.max=Math.max(0,frames.length-1);\n"
            "function render(){\n"
            "  const index=Number(slider.value)||0;\n"
            "  frame.src=frames[index]||'';\n"
            "  status.textContent=`Frame ${index+1}/${frames.length}`;\n"
            "}\n"
            "slider.addEventListener('input',render);\n"
            "render();\n"
            "</script></body></html>\n"
        ),
        encoding="utf-8",
    )
    return html_path


def serve_directory(recording_directory: Path, port: int) -> None:
    print(
        "Serving recording directory. Press Ctrl+C to stop. "
        f"Open http://127.0.0.1:{port}/ in your browser."
    )
    subprocess.run(
        ["python3", "-m", "http.server", str(port), "--directory", str(recording_directory)],
        check=True,
    )


def main() -> None:
    arguments = parse_arguments()
    if arguments.list or arguments.recording_dir is None:
        list_recordings(arguments.root)
        if arguments.recording_dir is None:
            return

    recording_directory = arguments.recording_dir
    if recording_directory is None:
        raise SystemExit("recording_dir is required unless --list-only mode is used")
    if not recording_directory.exists():
        raise SystemExit(f"Recording directory does not exist: {recording_directory}")

    frames = sorted((recording_directory / "frames").glob("frame_*.png"))
    if not frames:
        raise SystemExit(f"No frames found in {recording_directory / 'frames'}")

    output_path = (
        arguments.output if arguments.output is not None else recording_directory / "session.mp4"
    )
    exported_mp4 = export_recording_mp4(recording_directory, output_path, arguments.fps)
    if not exported_mp4:
        replay_html_path = write_html_scrubber(recording_directory, frames)
        print(
            "ffmpeg is unavailable. Wrote HTML frame scrubber to "
            f"{replay_html_path}. Open it in your browser."
        )

    if arguments.serve:
        serve_directory(recording_directory, arguments.port)


if __name__ == "__main__":
    main()
