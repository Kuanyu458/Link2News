#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK_DIR="$PROJECT_DIR/tmp/podcast-demo"
OUTPUT="$PROJECT_DIR/docs/assets/link2news-podcast-demo.mp3"

mkdir -p "$WORK_DIR" "$(dirname "$OUTPUT")"

/usr/bin/say -v 'Eddy (中文（台灣）)' -r 185 -o "$WORK_DIR/host-1.aiff" \
  '歡迎收聽 Link to News Podcast 示範。本段內容使用合成資料與系統語音製作，不包含任何真實使用者資料。'
/usr/bin/say -v 'Meijia' -r 195 -o "$WORK_DIR/guest.aiff" \
  '本週焦點是可靠的人工智慧代理。新的工具不只追求回答正確，更重視每一次工具呼叫是否能被追蹤、重試與人工覆核。'
/usr/bin/say -v 'Eddy (中文（台灣）)' -r 185 -o "$WORK_DIR/host-2.aiff" \
  'Link to News 會把你分享至 LINE 的論文、開源專案與新聞，整理成新聞式週報與語音導讀。這就是本週的示範內容，感謝收聽。'

ffmpeg -hide_banner -loglevel error -y \
  -i "$WORK_DIR/host-1.aiff" \
  -i "$WORK_DIR/guest.aiff" \
  -i "$WORK_DIR/host-2.aiff" \
  -filter_complex \
  '[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,apad=pad_dur=0.35[a0];[1:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,apad=pad_dur=0.35[a1];[2:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a2];[a0][a1][a2]concat=n=3:v=0:a=1[out]' \
  -map '[out]' -codec:a libmp3lame -b:a 128k "$OUTPUT"

echo "$OUTPUT"
