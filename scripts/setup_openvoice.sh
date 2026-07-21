#!/bin/bash
# OpenVoice V2 一次性建置（使用你既有的 anaconda）
# 完成後在 ~/.config/weekly-report/config.yaml 的 podcast.openvoice 填入印出的路徑，
# 並把 podcast.tts_backend 改成 "openvoice"。
set -euo pipefail

ENV_NAME="openvoice"
CKPT_DIR="$HOME/.cache/openvoice/checkpoints_v2"

echo "▶ 建立 conda 環境（python 3.10，OpenVoice 相依較舊）…"
conda env list | grep -q "^$ENV_NAME " || conda create -y -n "$ENV_NAME" python=3.10
PY="$(conda info --base)/envs/$ENV_NAME/bin/python"
PIP="$(conda info --base)/envs/$ENV_NAME/bin/pip"

echo "▶ 安裝 OpenVoice V2 + MeloTTS…"
"$PIP" install --quiet git+https://github.com/myshell-ai/OpenVoice.git
"$PIP" install --quiet git+https://github.com/myshell-ai/MeloTTS.git
"$PY" -m unidic download

echo "▶ 下載 OpenVoice V2 checkpoints（約 500MB）…"
"$PIP" install --quiet "huggingface_hub[cli]"
mkdir -p "$CKPT_DIR"
"$PY" -c "
from huggingface_hub import snapshot_download
snapshot_download('myshell-ai/OpenVoiceV2', local_dir='$CKPT_DIR')
print('checkpoints ready')
"

echo ""
echo "✅ 建置完成。請準備兩段聲音參考樣本（30 秒以上 wav/mp3）："
echo "   ⚠️ 必須是你本人的錄音，或已取得本人授權的聲音——"
echo "      未經同意複製他人（包括公眾人物）的聲音屬於聲音冒用，請勿使用。"
echo ""
echo "然後把以下設定填入 ~/.config/weekly-report/config.yaml："
echo "podcast:"
echo "  tts_backend: \"openvoice\""
echo "  openvoice:"
echo "    python: \"$PY\""
echo "    checkpoints_dir: \"$CKPT_DIR\""
echo "    reference_host: \"/path/to/你的主持人聲音樣本.wav\""
echo "    reference_guest: \"/path/to/你的來賓聲音樣本.wav\""
