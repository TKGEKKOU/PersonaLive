# Third-Party Notices

## Open-LLM-VTuber

YUMENO's realtime interaction work references selected designs and
MIT-licensed code from:

- Project: Open-LLM-VTuber
- Repository: <https://github.com/Open-LLM-VTuber/Open-LLM-VTuber>
- Baseline: v1.2.1, commit `3afa410`
- Copyright: Copyright (c) 2025 Yi-Ting Chiu
- License: MIT

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Live2D sample models from Open-LLM-VTuber are not included. Those assets are
governed by separate licenses.

Files that directly incorporate upstream code must retain their applicable
copyright and license notices.

## qwen3-tts.cpp

YUMENO can download a Windows runtime built from the MIT-licensed
qwen3-tts.cpp project:

- Project: qwen3-tts.cpp
- Repository: <https://github.com/predict-woo/qwen3-tts.cpp>
- Reviewed commit: `b3ba14077cf1b3e11b86e5f84aa9184605c89b28`
- License: MIT

The runtime archive includes the upstream license. GGUF model files are
downloaded separately from ModelScope and are not committed to this repository.

## PixiJS

- Project: PixiJS v7 (`pixi.js`)
- Repository: <https://github.com/pixijs/pixijs>
- Version: 7.4.2
- License: MIT

## pixi-live2d-display

- Project: pixi-live2d-display
- Repository: <https://github.com/guansss/pixi-live2d-display>
- Version: 0.4.0
- License: MIT

## Live2D Cubism Core

The Cubism runtime libraries (`live2d.min.js` for Cubism 2.1 and
`live2dcubismcore.min.js` for Cubism 3/4) are proprietary software distributed
by Live2D Inc. under the Live2D Proprietary Software License Agreement
(<https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html>).
They are included only for local rendering; review the agreement before
redistributing this application.

## Live2D Sample Models (Haru / Shizuku)

The bundled demo models under `data/live2d/` are official Live2D sample
models and are redistributed under the Live2D Free Material License
(<https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html>).
They are provided for local evaluation only; replace them with models you are
licensed to distribute before shipping the application to end users.
