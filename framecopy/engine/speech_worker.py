from __future__ import annotations

import argparse
import importlib.metadata
import json
import math


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    import mlx_whisper

    result = mlx_whisper.transcribe(args.input, path_or_hf_repo=args.model)
    segments = []
    for item in result.get("segments", []):
        avg_logprob = item.get("avg_logprob")
        confidence = math.exp(float(avg_logprob)) if avg_logprob is not None else 0.0
        segments.append({
            "start": item.get("start", 0),
            "end": item.get("end", 0),
            "text": item.get("text", ""),
            "confidence": max(0.0, min(1.0, confidence)),
        })
    print(json.dumps({
        "provider": "mlx_whisper",
        "provider_version": importlib.metadata.version("mlx-whisper"),
        "language": result.get("language", ""),
        "segments": segments,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
