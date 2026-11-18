"""Developer-only model download; the application loads local paths exclusively."""
from pathlib import Path
from urllib.request import urlopen
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
MODELS={
    'ch_PP-OCRv5_det_mobile.onnx':('det','4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae'),
    'th_PP-OCRv5_rec_mobile.onnx':('rec','de541dd83161c241ff426f7ecfd602a0ba77d686cf3ab9a6c255ea82fd08006e'),
}


def main():
    directory=ROOT/'vendor/neural-ocr'
    directory.mkdir(parents=True,exist_ok=True)
    manifest=[]
    for name,(kind,expected) in MODELS.items():
        destination=directory/name
        url=f'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/{kind}/{name}'
        if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest()!=expected:
            with urlopen(url,timeout=90) as response:
                content=response.read()
            if hashlib.sha256(content).hexdigest()!=expected:
                raise RuntimeError('Model checksum mismatch: '+name)
            destination.write_bytes(content)
        manifest.append({'name':name,'sha256':expected,'url':url,'license':'Apache-2.0'})
        print('Verified '+name)
    (directory/'models.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    license_path=directory/'PaddleOCR-LICENSE.txt'
    if not license_path.exists():
        with urlopen('https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/LICENSE',timeout=30) as response:
            license_path.write_bytes(response.read())


if __name__=='__main__':main()
