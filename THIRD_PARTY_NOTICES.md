# Third-party notices

This repository is licensed under the MIT License. Runtime dependencies retain
their own licenses. Installing an optional feature means accepting the licenses
and service terms of that feature's dependencies.

| Component | Use | License / notice |
|---|---|---|
| pypdfium2 and PDFium | PDF text extraction and rendering | Apache-2.0 / BSD-3-Clause for pypdfium2; PDFium and bundled components include BSD-style and other notices distributed with its wheels. |
| edge-tts | Optional online text-to-speech backend | LGPL-3.0 for most files. It is installed as a replaceable Python dependency and is not copied into this repository. |
| OpenVoice V2 / MeloTTS | Optional local voice-cloning backend | Installed separately by the user. Pin and review the selected upstream revision and model card before use. |
| Playwright / Chromium | HTML-to-PDF rendering | Apache-2.0 for Playwright; Chromium includes multiple third-party licenses. |
| *AI Premium*, Figure 1 and Table 2 | README report demo; cropped from the PDF with no content edits | Nicola Borri, Yukun Liu, and Aleh Tsyvinski; [arXiv:2606.30583](https://arxiv.org/abs/2606.30583); [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). |
| *Program-as-Weights*, Figures 1-3 | README report demo; cropped from the PDF with no content edits | Wentao Zhang, Liliana Hotsko, Woojeong Kim, Pengyu Nie, Stuart Shieber, and Yuntian Deng; [arXiv:2607.02512](https://arxiv.org/abs/2607.02512); [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). |

Do not use a voice sample without the speaker's informed authorization. The
project does not ship voice samples or model checkpoints. The README podcast
demo uses macOS system voices and synthetic text.
