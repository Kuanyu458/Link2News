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
| *AutoMem: Automated Learning of Memory as a Cognitive Skill*, Figures 1-2 | `weekly_2026-W28.pdf` research-report demo; reproduced with citation and no content edits | Shengguang Wu, Hao Zhu, Yuhui Zhang, Xiaohan Wang, and Serena Yeung-Levy; [arXiv:2607.01224](https://arxiv.org/abs/2607.01224); arXiv non-exclusive distribution license. Authors retain copyright. |
| *Shepherd: Enabling Programmable Meta-Agents via Reversible Agentic Execution Traces*, Figures 1-2 | `weekly_2026-W28.pdf` non-commercial research-report demo; reproduced with citation and no content edits | Simon Yu, Derek Chong, Ananjan Nandi, Dilara Soylu, Jiuding Sun, Christopher D. Manning, and Weiyan Shi; [arXiv:2605.10913](https://arxiv.org/abs/2605.10913); [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/). |
| *Gemma 4 Technical Report*, first-page preview | `weekly_2026-W28.pdf` research-report demo; rendered from the original PDF with no content edits | Gemma Team; [arXiv:2607.02770](https://arxiv.org/abs/2607.02770); [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). |
| *AI Premium*, Figure 1 and Table 2 | README report demo; cropped from the PDF with no content edits | Nicola Borri, Yukun Liu, and Aleh Tsyvinski; [arXiv:2606.30583](https://arxiv.org/abs/2606.30583); [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). |
| *Program-as-Weights*, Figures 1-3 | README report demo; cropped from the PDF with no content edits | Wentao Zhang, Liliana Hotsko, Woojeong Kim, Pengyu Nie, Stuart Shieber, and Yuntian Deng; [arXiv:2607.02512](https://arxiv.org/abs/2607.02512); [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). |

The cited paper text and figures embedded in `docs/assets/weekly_2026-W28.pdf`
are third-party materials and are not relicensed under this repository's MIT
License. The PDF is provided as a documentation demo; retain its citations and
comply with each source license when redistributing or reusing its contents.

Do not use a voice sample without the speaker's informed authorization. The
project does not ship voice samples or model checkpoints. The README podcast
demo uses macOS system voices and synthetic text.
