"""OpenVoice V2 合成 worker——在獨立的 OpenVoice conda/venv 環境中執行。

用法：<openvoice_env_python> openvoice_worker.py job.json
job.json：{segments: [{i, speaker: HOST|GUEST, text}], checkpoints_dir,
           reference_host, reference_guest, out_dir}

流程：MeloTTS(ZH) 產生基底語音 → OpenVoice ToneColorConverter 轉換成
參考樣本的音色（參考樣本必須是使用者本人或已取得授權的聲音）。
輸出：out_dir/seg_0000.wav ...
"""
import json
import sys
from pathlib import Path


def main():
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_dir = Path(job["out_dir"])
    ckpt = Path(job["checkpoints_dir"])

    import torch
    from melo.api import TTS
    from openvoice import se_extractor
    from openvoice.api import ToneColorConverter

    device = "mps" if torch.backends.mps.is_available() else "cpu"

    converter = ToneColorConverter(str(ckpt / "converter" / "config.json"), device=device)
    converter.load_ckpt(str(ckpt / "converter" / "checkpoint.pth"))

    # 基底語音：MeloTTS 中文（可唸混入的英文詞）
    tts = TTS(language="ZH", device=device)
    speaker_id = list(tts.hps.data.spk2id.values())[0]
    base_se = torch.load(str(ckpt / "base_speakers" / "ses" / "zh.pth"),
                         map_location=device)

    # 兩位說話者的目標音色（從參考樣本抽取，結果會快取）
    targets = {}
    for role, ref in [("HOST", job["reference_host"]), ("GUEST", job["reference_guest"])]:
        se, _ = se_extractor.get_se(ref, converter, vad=True)
        targets[role] = se

    for seg in job["segments"]:
        base_path = out_dir / f"base_{seg['i']:04d}.wav"
        final_path = out_dir / f"seg_{seg['i']:04d}.wav"
        tts.tts_to_file(seg["text"], speaker_id, str(base_path), speed=1.0)
        converter.convert(
            audio_src_path=str(base_path),
            src_se=base_se,
            tgt_se=targets[seg["speaker"]],
            output_path=str(final_path),
        )
        base_path.unlink()
        print(f"done {seg['i']}", flush=True)


if __name__ == "__main__":
    main()
