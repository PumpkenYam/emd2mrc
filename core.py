import re
import h5py
import numpy as np
from pathlib import Path
from PIL import Image
import mrcfile

# ============================================================
# 1. Extract AlphaTilt from EMD (compatible with Metadata / AcquisitionMetadata)
# ============================================================
def extract_alpha_tilt_from_emd(emd_path: Path):
    alpha_tilt = None

    def visitor(name, obj):
        nonlocal alpha_tilt
        if alpha_tilt is not None:
            return
        if not isinstance(obj, h5py.Dataset):
            return

        name_lower = name.lower()
        if "metadata" not in name_lower:
            return

        try:
            raw = obj[()]
            text = raw.flatten().tobytes().decode("utf-8", errors="ignore")
            match = re.search(r'"alphatilt"\s*:\s*"?(−?-?\d+\.?\d*)"?', text, re.IGNORECASE)
            if match:
                alpha_tilt = float(match.group(1))
        except Exception:
            pass

    with h5py.File(emd_path, "r") as f:
        f.visititems(visitor)

    return alpha_tilt

# ============================================================
# 2. Extract data source from filename
# ============================================================
def extract_source_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    tokens = stem.split()
    return tokens[-1] if tokens else ""

# ============================================================
# 3. Group images by source
# ============================================================
def group_images_by_source(image_dir: Path):
    images = []
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff"):
        images.extend(image_dir.glob(ext))

    groups = {}
    for img in images:
        source = extract_source_from_filename(img.name)
        groups.setdefault(source, []).append(img)

    return groups

# ============================================================
# 4. Find matching EMD for a given image
# ============================================================
def find_matching_emd(img_path: Path, emd_dir: Path):
    stem = img_path.stem
    tokens = stem.split()
    if len(tokens) < 2:
        return None
    emd_stem = " ".join(tokens[:-1])
    emd_path = emd_dir / f"{emd_stem}.emd"
    return emd_path if emd_path.exists() else None

# ============================================================
# 5. Load images sorted by AlphaTilt
# ============================================================
def load_images_sorted_by_alpha(image_files, emd_dir: Path, log_cb=None, progress_cb=None, cancel_flag=None):
    records = []
    total = len(image_files)

    for idx, img_path in enumerate(image_files):
        if cancel_flag is not None and cancel_flag():
            return None

        emd_path = find_matching_emd(img_path, emd_dir)
        if emd_path is None:
            if log_cb: log_cb(f"⚠️ EMD not found for: {img_path.name}")
            continue

        alpha = extract_alpha_tilt_from_emd(emd_path)
        if alpha is None:
            if log_cb: log_cb(f"⚠️ AlphaTilt not found for: {emd_path.name}")
            continue

        records.append((alpha, img_path))

        if progress_cb:
            progress_cb(int((idx + 1) / max(total, 1) * 50))  # first collect tilt

    if not records:
        return None

    records.sort(key=lambda x: x[0])

    stack = []
    for j, (alpha, img_path) in enumerate(records):
        if cancel_flag is not None and cancel_flag():
            return None

        img = Image.open(img_path).convert("F")
        stack.append(np.array(img))

        if progress_cb:
            # then read image stack
            progress_cb(50 + int((j + 1) / max(len(records), 1) * 50))

    return np.stack(stack, axis=0)


def write_mrc(stack: np.ndarray, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with mrcfile.new(str(output_path), overwrite=True) as mrc:
        mrc.set_data(stack.astype(np.float32))
