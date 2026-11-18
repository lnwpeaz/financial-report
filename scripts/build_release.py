"""Build a self-contained Windows x64 executable and a clean distributable ZIP."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import zipfile
import pefile

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "tesseract"


def copy_runtime(target):
    target.mkdir(parents=True, exist_ok=True)
    available = {p.name.lower(): p for p in VENDOR.glob("*.dll")}
    todo = [VENDOR / "tesseract.exe"]
    copied = set()
    while todo:
        path = todo.pop()
        if path.name.lower() in copied:
            continue
        if not path.exists():
            raise SystemExit("Missing Tesseract. Run scripts/bootstrap_ocr.ps1 first.")
        shutil.copy2(path, target / path.name)
        copied.add(path.name.lower())
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"], pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        for imported in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []):
            name = imported.dll.decode("ascii").lower()
            if name in available:
                todo.append(available[name])
        pe.close()
    data = target / "tessdata"
    data.mkdir(exist_ok=True)
    for name in ["eng.traineddata", "tha.traineddata"]:
        shutil.copy2(VENDOR / "tessdata" / name, data / name)
    shutil.copytree(VENDOR / "tessdata" / "configs", data / "configs", dirs_exist_ok=True)
    shutil.copytree(VENDOR / "doc", target / "doc", dirs_exist_ok=True)
    return sorted(copied)


def main():
    if os.name != "nt":
        raise SystemExit("Build this Windows executable on Windows x64.")
    runtime = ROOT / "build" / "ocr-runtime"
    # Use a fresh staging directory without removing any user work.
    import tempfile
    runtime = Path(tempfile.mkdtemp(prefix="ocr-runtime-", dir=ROOT / "build"))
    dlls = copy_runtime(runtime)
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT/'scripts/bootstrap_neural.py')],cwd=ROOT,check=True)
    import rapidocr
    rapid_root = Path(rapidocr.__file__).parent
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "SlipToExcel",
                    "--add-data", f"{runtime};tesseract", "--add-data", f"{ROOT/'vendor/neural-ocr'};neural-ocr",
                    "--add-data", f"{rapid_root/'config.yaml'};rapidocr", "--add-data", f"{rapid_root/'default_models.yaml'};rapidocr",
                    "--hidden-import", "rapidocr.main", "--hidden-import", "rapidocr.inference_engine.onnxruntime.main",
                    "--exclude-module", "pytest", "--exclude-module", "torch", "--exclude-module", "paddle", "--exclude-module", "openvino",
                    str(ROOT / "app.py")], cwd=ROOT, check=True)
    release = ROOT / "release" / "SlipToExcel"
    release.mkdir(parents=True, exist_ok=True)
    exe_target = release / "SlipToExcel.exe"
    try:
        shutil.copy2(ROOT / "dist" / "SlipToExcel.exe", exe_target)
    except PermissionError:
        # The user's old app may still be open. Do not terminate it or lose edits.
        exe_target = release / "SlipToExcel-1.4.exe"
        shutil.copy2(ROOT / "dist" / "SlipToExcel.exe", exe_target)
        print(f"Old app is in use. New app ready at: {exe_target}")
    shutil.copy2(ROOT / "คู่มือการใช้งาน.txt", release / "คู่มือการใช้งาน.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", release / "THIRD_PARTY_NOTICES.md")
    (release / "data").mkdir(exist_ok=True)
    licenses = release / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copy2(VENDOR / "doc" / "LICENSE", licenses / "Tesseract-Apache-2.0.txt")
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    if py_license.exists():
        shutil.copy2(py_license, licenses / "Python-LICENSE.txt")
    for package in ["Pillow", "openpyxl", "pytesseract", "pyinstaller", "et_xmlfile", "packaging", "rapidocr", "onnxruntime", "opencv-python-headless", "numpy", "shapely", "pyclipper", "omegaconf", "PyYAML", "requests", "urllib3", "certifi", "charset-normalizer", "idna", "protobuf", "flatbuffers", "colorlog", "tqdm", "six"]:
        dist = importlib.metadata.distribution(package)
        for file in dist.files or []:
            if "license" in file.name.lower() or "copying" in file.name.lower():
                source = Path(dist.locate_file(file))
                if source.is_file():
                    shutil.copy2(source, licenses / f"{package}-{file.name}")
    shutil.copy2(ROOT/'vendor/neural-ocr/models.json',licenses/'neural-models.json')
    for source in (ROOT/'vendor/neural-ocr').glob('*LICENSE*'):
        shutil.copy2(source,licenses/source.name)
    manifest = {"version": "1.4.0", "platform": "Windows x64", "python": sys.version, "packages": {p: importlib.metadata.version(p) for p in ["Pillow", "openpyxl", "pytesseract", "pyinstaller", "rapidocr", "onnxruntime", "numpy", "opencv-python-headless"]}, "ocr_files": dlls, "neural_models":json.loads((ROOT/'vendor/neural-ocr/models.json').read_text()), "exe_sha256": hashlib.file_digest(exe_target.open("rb"), "sha256").hexdigest()}
    (release / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Explicit allowlist: never package slips, workbooks, cached OCR, or user settings.
    archive = ROOT / "release" / "SlipToExcel-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for name in ["SlipToExcel.exe", "คู่มือการใช้งาน.txt", "THIRD_PARTY_NOTICES.md", "build-manifest.json"]:
            bundle.write(exe_target if name == "SlipToExcel.exe" else release / name, f"SlipToExcel/{name}")
        bundle.writestr("SlipToExcel/data/", "")
        for path in licenses.iterdir():
            if path.is_file():
                bundle.write(path, f"SlipToExcel/licenses/{path.name}")
    print(f"Release: {archive} ({archive.stat().st_size / 1024 / 1024:.1f} MB)")
    source_archive()


def source_archive():
    destination = ROOT / "release" / "SlipToExcel-Source.zip"
    paths = [ROOT / name for name in [".gitignore", "README.md", "THIRD_PARTY_NOTICES.md", "คู่มือการใช้งาน.txt", "app.py", "build.ps1", "requirements.txt", "requirements-dev.txt"]]
    for directory in ["slipreport", "scripts", "tests"]:
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.suffix in {".py", ".ps1"} and "__pycache__" not in p.parts and p.name not in {"diagnose_ocr.py", "benchmark_local.py", "benchmark_neural.py", "audit_local.py", "compare_recognition.py", "evaluate_local.py"})
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in paths:
            bundle.write(path, "SlipToExcel-Source/" + path.relative_to(ROOT).as_posix())
    return destination


if __name__ == "__main__":
    (ROOT / "build").mkdir(exist_ok=True)
    main()
