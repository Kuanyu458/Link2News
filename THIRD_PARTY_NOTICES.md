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

Do not use a voice sample without the speaker's informed authorization. The
project does not ship voice samples, model checkpoints, or generated media.
