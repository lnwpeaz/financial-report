# Third-party components

Slip to Excel bundles open-source software. Copyright remains with the respective authors. License texts available from the installed packages are included in `licenses/` in the release. The build manifest records exact Python package versions and native runtime filenames.

| Component | License / source |
| --- | --- |
| Python 3.13 | PSF License, https://www.python.org/downloads/source/ |
| Tcl/Tk 8.6 | BSD-style Tcl/Tk license, https://www.tcl.tk/software/tcltk/license.html |
| Pillow | HPND and bundled library notices, https://github.com/python-pillow/Pillow |
| openpyxl / et_xmlfile | MIT, https://foss.heptapod.net/openpyxl/openpyxl |
| pytesseract | Apache-2.0, https://github.com/madmaze/pytesseract |
| packaging | Apache-2.0 / BSD-2-Clause, https://github.com/pypa/packaging |
| PyInstaller | GPL-2.0-or-later with exception permitting distribution of bundled applications, https://pyinstaller.org/en/stable/license.html |
| Tesseract 5.5.3.20260724 | Apache-2.0, https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3 |
| Thai model (tessdata_fast) and English model | Apache-2.0, https://github.com/tesseract-ocr/tessdata_fast and https://github.com/tesseract-ocr/tessdata |
| Leptonica | BSD-2-Clause, https://github.com/DanBloomberg/leptonica |
| PP-OCRv5 Thai recognizer and mobile detector | Apache-2.0, https://github.com/PaddlePaddle/PaddleOCR (ONNX conversion distributed by RapidAI) |
| RapidOCR 3.10 | Apache-2.0, https://github.com/RapidAI/RapidOCR |
| ONNX Runtime | MIT, https://github.com/microsoft/onnxruntime |
| OpenCV | Apache-2.0 and included third-party notices, https://github.com/opencv/opencv |
| NumPy | BSD-3-Clause and bundled library notices, https://github.com/numpy/numpy |
| Shapely / GEOS | BSD-3-Clause / LGPL-2.1, https://github.com/shapely/shapely |
| pyclipper | MIT, https://github.com/fonttools/pyclipper |
| OmegaConf, PyYAML, Requests and supporting Python packages | License texts copied from distributions into licenses/ |

Tesseract's Windows binaries include their required native dependencies from the upstream Windows release. Sources and license notices for these projects are available from their official distributions: https://github.com/msys2/MINGW-packages (matching package recipes and upstream source references), https://gcc.gnu.org (GCC runtime libraries, GPL with GCC Runtime Library Exception), https://curl.se (curl license), https://www.libarchive.org (BSD), https://www.zlib.net (zlib), https://www.libpng.org (libpng), https://libjpeg-turbo.org (IJG/BSD/zlib), https://libtiff.gitlab.io/libtiff/ (libtiff), https://developers.google.com/speed/webp (BSD), https://www.openjpeg.org (BSD), https://github.com/facebook/zstd (BSD), https://tukaani.org/xz/ (multiple licenses; library largely public domain), https://www.bzip.org (BSD-style), https://github.com/lz4/lz4 (BSD), https://www.gnu.org/software/libiconv/ (LGPL), https://www.gnu.org/software/libunistring/ (LGPL/GPL), https://www.gnu.org/software/libidn/ (LGPL/GPL), https://github.com/rockdaboot/libpsl (MIT), https://www.libssh2.org (BSD), and https://github.com/google/brotli (MIT).

The application makes no runtime network requests. The original OCR installer and models are fetched only by the developer bootstrap script when building from source.
