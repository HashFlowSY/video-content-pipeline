# Acquisition record — rapidocr (ocr_primary, bundled in wheel)

**Status:** acquired · recorded from the pinned wheel (not one of the seven downloads)

| Field | Value |
|---|---|
| Model | `rapidocr (PyPI wheel, bundled PP-OCRv6 small det+rec + v4 mobile cls)` |
| Source | https://github.com/RapidAI/RapidOCR (approved) |
| License | Apache-2.0 (approved) |
| Revision | `3.9.2` |
| Acquisition method | bundled in pinned rapidocr==3.9.2 wheel (no separate download) |
| Target path | `bundled-in-wheel` |
| Total size | 31,749,509 bytes |
| asset_sha256 (manifest digest) | `b074b483b736e064d9d09805e395e64da3abde87a27d7deb3fc127f1b5026ce3` |

## File manifest (SHA-256 verified on disk)

| Path | Size (bytes) | SHA-256 |
|---|---|---|
| `.gitkeep` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `PP-OCRv6_det_small.onnx` | 9,929,594 | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` |
| `PP-OCRv6_rec_small.onnx` | 21,234,383 | `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` |
| `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | 585,532 | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` |

## Authorization

Model-download authorization only; never reused as media-download
authorization. Recorded from the pinned rapidocr==3.9.2 wheel (no separate download): the three default det/cls/rec models ship inside the wheel, confirmed against the wheel's dist-info RECORD (hashes and sizes match this manifest). License and official source approved by the maintainer 2026-08-16.
