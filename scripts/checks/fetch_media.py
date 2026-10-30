#!/usr/bin/env python3
"""Deterministically fetch, normalize, and verify images/engravings for Document Swarm."""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import hashlib
import html
import io
import json
import os
import re
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, write_json_atomic

USER_AGENT = "DocumentSwarmMediaFetcher/3.5 (https://github.com/EdneiMonteiro/document-swarm; local editorial verification)"
MAX_IMAGE_BYTES = 15 * 1024 * 1024
DEFAULT_FLUX_ENDPOINT = (
    "https://apim-private-marvin-f254aaba.azure-api.net/providers/blackforestlabs/v1/flux-2-pro?api-version=preview"
)
DEFAULT_CRED_TARGET = "apim-private-marvin-f254aaba"

FLUX_STYLE_PRESETS: dict[str, str] = {
    "engraving": (
        "Fine vintage copperplate and woodblock editorial book engraving on crisp pure white background, "
        "rich cross-hatching, dramatic black ink linework with subtle indigo and crimson accents, "
        "museum-grade collector book plate, no background tint, no text or watermarks: "
    ),
    "manga-engraving": (
        "Masterpiece Japanese seinen manga ink plate and fine woodblock engraving on crisp pure white background, "
        "dynamic speed lines, dramatic chiaroscuro ink linework, intricate mechanical detail, "
        "collector's edition book illustration, no background tint, no text, no speech bubbles, no watermarks: "
    ),
    "technical-cutaway": (
        "Precision technical cutaway illustration and architectural engraving on crisp pure white background, "
        "clean exploded mechanical view, fine ink drafting lines, collector's manual plate, "
        "no background tint, no text labels or gibberish letters: "
    ),
    "editorial-illustration": (
        "Editorial magazine and book feature illustration on clean white background, rich atmospheric lighting, "
        "cinematic composition, tactile brushwork and crisp ink detail, no text or watermarks: "
    ),
}


def _clean_html(raw_text: str) -> str:
    if not raw_text:
        return ""
    stripped = re.sub(r"<[^>]+>", " ", str(raw_text))
    unescaped = html.unescape(stripped)
    latin_safe = "".join(ch if ord(ch) <= 0x024F else " " for ch in unescaped)
    return re.sub(r"\s+", " ", latin_safe).strip()


def _inspect_image_bytes(data: bytes) -> tuple[str, int, int]:
    """Return (ext, width, height) for PNG or JPEG bytes using stdlib or Pillow."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        if width > 0 and height > 0:
            return ".png", int(width), int(height)
    if data.startswith(b"\xff\xd8"):
        idx = 2
        n = len(data)
        while idx + 9 < n:
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            while marker == 0xFF and idx + 2 < n:
                idx += 1
                marker = data[idx + 1]
            if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                idx += 2
                continue
            if idx + 4 > n:
                break
            seg_len = struct.unpack(">H", data[idx + 2 : idx + 4])[0]
            if seg_len < 2 or idx + 2 + seg_len > n:
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height, width = struct.unpack(">HH", data[idx + 5 : idx + 9])
                if width > 0 and height > 0:
                    return ".jpg", int(width), int(height)
            idx += 2 + seg_len
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").upper()
            ext = ".png" if fmt == "PNG" else ".jpg"
            return ext, int(im.width), int(im.height)
    except Exception as exc:
        raise InputError(f"unsupported or corrupt image bytes (expected PNG or JPEG): {exc}") from exc


def normalize_image_bytes(data: bytes, preferred_ext: str = ".jpg", max_dim: int = 1600) -> tuple[bytes, str, int, int]:
    """Normalize image bytes to clean RGB PNG/JPEG when Pillow is installed, or validate raw PNG/JPEG."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            orig_fmt = (im.format or "").upper()
            if im.mode in {"RGBA", "LA"} or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            if max(im.width, im.height) > max_dim:
                scale = max_dim / float(max(im.width, im.height))
                im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.Resampling.LANCZOS)
            out_ext = ".png" if preferred_ext == ".png" else ".jpg"
            buf = io.BytesIO()
            if out_ext == ".png":
                im.save(buf, format="PNG", optimize=True)
            else:
                im.save(buf, format="JPEG", quality=88, optimize=True)
            out_bytes = buf.getvalue()
            return out_bytes, out_ext, int(im.width), int(im.height)
    except ImportError:
        ext, width, height = _inspect_image_bytes(data)
        return data, ext, width, height
    except Exception as exc:
        raise InputError(f"failed to decode/normalize image: {exc}") from exc


def _http_get_bytes(url: str, timeout: float = 20.0) -> bytes:
    parsed = urllib.parse.urlparse(url)
    headers: dict[str, str] = {"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.8"}
    if "static.wikia.nocookie.net" in (parsed.netloc or ""):
        parts = [p for p in (parsed.path or "").split("/") if p]
        wiki_slug = parts[0] if parts else "community"
        headers["Referer"] = f"https://{wiki_slug}.fandom.com/"
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and "Referer" not in headers and parsed.scheme and parsed.netloc:
            retry_headers = dict(headers)
            retry_headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
            retry_headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            retry_req = urllib.request.Request(url, headers=retry_headers)
            with urllib.request.urlopen(retry_req, timeout=timeout) as resp:
                raw = resp.read(MAX_IMAGE_BYTES + 1)
        else:
            raise
    if len(raw) > MAX_IMAGE_BYTES:
        raise InputError(f"image exceeds {MAX_IMAGE_BYTES} bytes: {url}")
    return raw


def _http_get_json(url: str, timeout: float = 20.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_wikipedia_page_media(title: str, lang: str = "en", thumb_width: int = 1200) -> dict[str, Any]:
    """Resolve the lead image and license metadata for a Wikipedia article."""
    api = f"https://{lang}.wikipedia.org/w/api.php"
    params = urllib.parse.urlencode({
        "action": "query",
        "titles": title,
        "prop": "pageimages",
        "piprop": "name|original|thumbnail",
        "pithumbsize": str(thumb_width),
        "redirects": "1",
        "format": "json",
    })
    data = _http_get_json(f"{api}?{params}")
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        thumb = page.get("thumbnail", {})
        img_url = thumb.get("source") or page.get("original", {}).get("source")
        page_image = page.get("pageimage")
        if img_url:
            credit = f"Wikimedia / Wikipedia ({page.get('title', title)})"
            license_name = "Wikimedia/Wikipedia"
            if page_image:
                info_params = urllib.parse.urlencode({
                    "action": "query",
                    "titles": f"File:{page_image}",
                    "prop": "imageinfo",
                    "iiprop": "extmetadata|url",
                    "format": "json",
                })
                try:
                    info_data = _http_get_json(f"{api}?{info_params}")
                    for fpage in info_data.get("query", {}).get("pages", {}).values():
                        iinfo = (fpage.get("imageinfo") or [{}])[0]
                        ext = iinfo.get("extmetadata") or {}
                        lic = _clean_html((ext.get("LicenseShortName") or {}).get("value", ""))
                        artist = _clean_html((ext.get("Artist") or {}).get("value", ""))
                        if lic:
                            license_name = lic
                        if artist and lic:
                            credit = f"{artist} ({lic}, Wikimedia)"
                        elif lic:
                            credit = f"Wikimedia ({lic})"
                except Exception:
                    pass
            return {
                "url": img_url,
                "title": str(page.get("title") or title),
                "credit": credit,
                "license": license_name,
            }
    raise InputError(f"no lead pageimage found on {lang}.wikipedia.org for '{title}'")


def fetch_commons_file(file_title: str, thumb_width: int = 1280) -> dict[str, Any]:
    """Fetch a specific File:... from Wikimedia Commons with license and artist metadata."""
    clean_name = file_title if file_title.startswith("File:") else f"File:{file_title}"
    api = "https://commons.wikimedia.org/w/api.php"
    params = urllib.parse.urlencode({
        "action": "query",
        "titles": clean_name,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|dimensions|mime",
        "iiurlwidth": str(thumb_width),
        "format": "json",
    })
    data = _http_get_json(f"{api}?{params}")
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        iinfo = (page.get("imageinfo") or [{}])[0]
        img_url = iinfo.get("thumburl") or iinfo.get("url")
        if not img_url:
            continue
        ext = iinfo.get("extmetadata") or {}
        lic = _clean_html((ext.get("LicenseShortName") or {}).get("value", "")) or "CC / Public Domain"
        artist = _clean_html((ext.get("Artist") or {}).get("value", ""))
        desc = _clean_html((ext.get("ObjectName") or ext.get("ImageDescription") or {}).get("value", ""))
        clean_title = desc or str(page.get("title", "")).removeprefix("File:")
        credit = f"{artist} ({lic}, Wikimedia Commons)" if artist else f"Wikimedia Commons ({lic})"
        return {
            "url": img_url,
            "title": clean_title[:160],
            "credit": credit[:180],
            "license": lic[:80],
        }
    raise InputError(f"file not found on Wikimedia Commons: {clean_name}")


def search_commons_media(query: str, limit: int = 3, thumb_width: int = 1200) -> list[dict[str, Any]]:
    """Search Wikimedia Commons for bitmap images matching query."""
    api = "https://commons.wikimedia.org/w/api.php"
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": "6",
        "gsrlimit": str(max(1, min(limit, 10))),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|dimensions|mime",
        "iiurlwidth": str(thumb_width),
        "format": "json",
    })
    data = _http_get_json(f"{api}?{params}")
    pages = data.get("query", {}).get("pages", {})
    results: list[dict[str, Any]] = []
    for page in sorted(pages.values(), key=lambda item: item.get("index", 999)):
        iinfo = (page.get("imageinfo") or [{}])[0]
        mime = str(iinfo.get("mime") or "")
        if not any(tok in mime for tok in ("jpeg", "png")):
            continue
        img_url = iinfo.get("thumburl") or iinfo.get("url")
        if not img_url:
            continue
        ext = iinfo.get("extmetadata") or {}
        lic = _clean_html((ext.get("LicenseShortName") or {}).get("value", "")) or "CC / Public Domain"
        artist = _clean_html((ext.get("Artist") or {}).get("value", ""))
        desc = _clean_html((ext.get("ObjectName") or ext.get("ImageDescription") or {}).get("value", ""))
        clean_title = desc or str(page.get("title", "")).removeprefix("File:")
        credit = f"{artist} ({lic}, Wikimedia Commons)" if artist else f"Wikimedia Commons ({lic})"
        results.append({
            "url": img_url,
            "title": clean_title[:160],
            "credit": credit[:180],
            "license": lic[:80],
        })
    return results


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD),
        ("Type", wt.DWORD),
        ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR),
        ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wt.DWORD),
        ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wt.LPWSTR),
        ("UserName", wt.LPWSTR),
    ]


def read_key_from_windows_vault(target_name: str = DEFAULT_CRED_TARGET) -> str | None:
    """Read the APIM subscription key directly from Windows Credential Manager (CredReadW)."""
    if os.name != "nt":
        return None
    adv = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    pcred = ctypes.POINTER(_CREDENTIAL)()
    if adv.CredReadW(target_name, 1, 0, ctypes.byref(pcred)):
        try:
            cred = pcred.contents
            raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            try:
                text = raw.decode("utf-16-le").rstrip("\x00")
                if text and all(32 <= ord(c) < 127 for c in text):
                    return text
            except UnicodeDecodeError:
                pass
            return raw.decode("utf-8", errors="ignore").rstrip("\x00")
        finally:
            adv.CredFree(pcred)
    return None


def resolve_apim_key(vault_target: str = DEFAULT_CRED_TARGET) -> str:
    """Resolve the APIM subscription key from env vars, Windows Credential Manager, or Azure CLI fallback."""
    for env_name in ("DOCSWARM_FLUX_API_KEY", "APIM_SUBSCRIPTION_KEY", "AZURE_API_KEY"):
        val = os.environ.get(env_name, "").strip()
        if val:
            return val

    for target in (vault_target, DEFAULT_CRED_TARGET):
        if target:
            vault_key = read_key_from_windows_vault(target)
            if vault_key:
                return vault_key

    cmd = "az.cmd" if os.name == "nt" else "az"
    url = (
        "https://management.azure.com/subscriptions/f254aaba-69b8-47c5-9841-78ca415b1f91"
        "/resourceGroups/rg4inteligencia/providers/Microsoft.ApiManagement"
        "/service/apim-private-marvin-f254aaba/subscriptions/marvin-private-client"
        "/listSecrets?api-version=2022-08-01"
    )
    try:
        out = subprocess.check_output(
            [cmd, "rest", "--method", "post", "--url", url, "--query", "primaryKey", "-o", "tsv"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            if os.name == "nt":
                subprocess.run(
                    ["cmdkey", f"/generic:{vault_target or DEFAULT_CRED_TARGET}", "/user:apim-client", f"/pass:{out}"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return out
    except Exception:
        pass
    raise InputError(
        f"APIM key not found in environment or Windows Credential Manager (target '{vault_target}')"
    )


def generate_flux_media(
    prompt: str,
    *,
    style: str = "engraving",
    width: int = 1024,
    height: int = 768,
    input_image_path: str | None = None,
    endpoint_url: str = DEFAULT_FLUX_ENDPOINT,
    vault_target: str = DEFAULT_CRED_TARGET,
    timeout: float = 180.0,
) -> tuple[bytes, str]:
    """Generate an editorial engraving or illustration via FLUX.2-pro on Azure APIM."""
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise InputError("--flux-prompt must be a non-empty string")
    style_key = (style or "").strip().lower()
    prefix = FLUX_STYLE_PRESETS.get(style_key, "")
    full_prompt = f"{prefix}{clean_prompt}" if prefix else clean_prompt

    apim_key = resolve_apim_key(vault_target)
    payload: dict[str, Any] = {
        "model": "FLUX.2-pro",
        "prompt": full_prompt,
        "width": max(256, min(int(width), 1440)),
        "height": max(256, min(int(height), 1440)),
        "output_format": "png",
    }
    if input_image_path:
        if input_image_path.startswith(("http://", "https://")):
            ref_raw = _http_get_bytes(input_image_path)
        else:
            ref_raw = Path(input_image_path).read_bytes()
        norm_ref, _, _, _ = normalize_image_bytes(ref_raw, preferred_ext=".png", max_dim=1024)
        payload["input_image"] = base64.b64encode(norm_ref).decode("ascii")

    req = urllib.request.Request(
        endpoint_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "api-key": apim_key,
            "Ocp-Apim-Subscription-Key": apim_key,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if "sample" in data:
        val = data["sample"]
        if isinstance(val, str) and val.startswith("http"):
            return _http_get_bytes(val, timeout=60.0), full_prompt
        return base64.b64decode(val), full_prompt
    if "result" in data and isinstance(data["result"], dict) and "sample" in data["result"]:
        val = data["result"]["sample"]
        if isinstance(val, str) and val.startswith("http"):
            return _http_get_bytes(val, timeout=60.0), full_prompt
        return base64.b64decode(val), full_prompt
    if "data" in data and isinstance(data["data"], list) and data["data"]:
        item = data["data"][0]
        if "b64_json" in item:
            return base64.b64decode(item["b64_json"]), full_prompt
        if "url" in item:
            return _http_get_bytes(item["url"], timeout=60.0), full_prompt
    if "image" in data:
        return base64.b64decode(data["image"]), full_prompt
    raise InputError(f"unexpected FLUX.2-pro response shape: {list(data.keys())}")


def save_media_item(
    output_dir: Path,
    *,
    media_id: str,
    raw_bytes: bytes,
    source_url: str,
    title: str,
    credit: str,
    license_name: str = "verified",
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", media_id):
        raise InputError(f"invalid media id '{media_id}'")
    output_dir.mkdir(parents=True, exist_ok=True)
    norm_bytes, ext, width, height = normalize_image_bytes(raw_bytes)
    filename = f"{media_id}{ext}"
    file_path = output_dir / filename
    file_path.write_bytes(norm_bytes)
    sha256 = hashlib.sha256(norm_bytes).hexdigest()

    manifest_path = output_dir / "media-manifest.json"
    manifest: dict[str, Any] = {"schema_version": 1, "items": []}
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("items"), list):
                manifest = loaded
        except Exception:
            pass
    entry: dict[str, Any] = {
        "id": media_id,
        "file": filename,
        "path": f"sources/media/{filename}",
        "source_url": source_url,
        "title": title,
        "credit": credit,
        "license": license_name,
        "width": width,
        "height": height,
        "sha256": sha256,
    }
    if extra_metadata:
        entry.update(extra_metadata)
    items = [item for item in manifest["items"] if isinstance(item, dict) and item.get("id") != media_id]
    items.append(entry)
    manifest["items"] = sorted(items, key=lambda item: str(item.get("id", "")))
    write_json_atomic(manifest_path, manifest)
    return entry


def verify_media_dir(media_dir: Path) -> dict[str, Any]:
    manifest_path = media_dir / "media-manifest.json"
    if not manifest_path.is_file():
        raise InputError(f"media manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("items")
    if not isinstance(items, list):
        raise InputError("invalid media-manifest.json: items list required")
    errors = []
    verified = []
    for item in items:
        if not isinstance(item, dict) or not item.get("file"):
            errors.append({"id": str(item), "error": "invalid_entry"})
            continue
        fpath = media_dir / str(item["file"])
        if not fpath.is_file():
            errors.append({"id": item.get("id"), "error": "missing_file", "file": str(item["file"])})
            continue
        raw = fpath.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != item.get("sha256"):
            errors.append({"id": item.get("id"), "error": "sha256_mismatch", "file": str(item["file"])})
            continue
        try:
            _, w, h = _inspect_image_bytes(raw)
        except Exception as exc:
            errors.append({"id": item.get("id"), "error": str(exc), "file": str(item["file"])})
            continue
        verified.append({"id": item.get("id"), "file": item["file"], "width": w, "height": h, "sha256": actual_sha})
    return {
        "schema_version": 1,
        "status": "fail" if errors else "pass",
        "verified_count": len(verified),
        "error_count": len(errors),
        "errors": errors,
        "items": verified,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch, normalize, and verify images/engravings for Document Swarm.")
    parser.add_argument("--output-dir", type=Path, default=Path("sources/media"), help="Directory to store images and media-manifest.json")
    parser.add_argument("--id", help="Identifier for the downloaded image (e.g. M01, cover-photo)")
    parser.add_argument("--url", help="Direct image URL to download and normalize")
    parser.add_argument("--wikipedia-page", help="Wikipedia article title to fetch lead image from")
    parser.add_argument("--lang", default="en", help="Wikipedia language code (default: en)")
    parser.add_argument("--commons-file", help="Exact Wikimedia Commons File:... title to download with license metadata")
    parser.add_argument("--commons-search", help="Wikimedia Commons search query for bitmap photos/engravings")
    parser.add_argument("--limit", type=int, default=1, help="Max images to download from Commons search (default: 1)")
    parser.add_argument("--flux-prompt", help="Generate a bespoke editorial engraving/illustration via FLUX.2-pro on Azure APIM")
    parser.add_argument(
        "--flux-style",
        default="engraving",
        choices=["engraving", "manga-engraving", "technical-cutaway", "editorial-illustration", "raw"],
        help="Style preset for FLUX.2-pro generation (default: engraving)",
    )
    parser.add_argument("--flux-input-image", help="Optional local path or URL of reference image for FLUX.2-pro")
    parser.add_argument("--width", type=int, default=1024, help="Target width for FLUX.2-pro generation (default: 1024)")
    parser.add_argument("--height", type=int, default=768, help="Target height for FLUX.2-pro generation (default: 768)")
    parser.add_argument(
        "--flux-endpoint",
        default=os.environ.get("DOCSWARM_FLUX_URL", DEFAULT_FLUX_ENDPOINT),
        help="Azure APIM FLUX.2-pro endpoint URL",
    )
    parser.add_argument(
        "--vault-target",
        default=os.environ.get("DOCSWARM_FLUX_VAULT_TARGET", DEFAULT_CRED_TARGET),
        help="Windows Credential Manager target name for the APIM subscription key",
    )
    parser.add_argument("--title", default="", help="Override image title")
    parser.add_argument("--credit", default="", help="Override image credit/attribution")
    parser.add_argument("--license", default="", help="Override license label")
    parser.add_argument("--verify", action="store_true", help="Verify all images in --output-dir/media-manifest.json")
    args = parser.parse_args(argv)

    try:
        if args.verify:
            report = verify_media_dir(args.output_dir)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report["status"] == "pass" else 1

        saved = []
        if args.flux_prompt:
            if not args.id:
                raise InputError("--id is required when using --flux-prompt")
            raw, full_prompt = generate_flux_media(
                args.flux_prompt,
                style="" if args.flux_style == "raw" else args.flux_style,
                width=args.width,
                height=args.height,
                input_image_path=args.flux_input_image,
                endpoint_url=args.flux_endpoint,
                vault_target=args.vault_target,
            )
            entry = save_media_item(
                args.output_dir,
                media_id=args.id,
                raw_bytes=raw,
                source_url="apim://blackforestlabs/flux-2-pro",
                title=args.title or args.id,
                credit=args.credit or "Gravura editorial original desta edição",
                license_name=args.license or "Gravura editorial original",
                extra_metadata={
                    "generator": "FLUX.2-pro",
                    "style": args.flux_style,
                    "prompt": full_prompt[:400],
                },
            )
            saved.append(entry)
        elif args.url:
            if not args.id:
                raise InputError("--id is required when using --url")
            raw = _http_get_bytes(args.url)
            entry = save_media_item(
                args.output_dir,
                media_id=args.id,
                raw_bytes=raw,
                source_url=args.url,
                title=args.title or args.id,
                credit=args.credit or "Fonte verificada",
                license_name=args.license or "verified",
            )
            saved.append(entry)
        elif args.wikipedia_page:
            media_id = args.id or re.sub(r"[^A-Za-z0-9_-]+", "-", args.wikipedia_page).strip("-").lower()
            meta = fetch_wikipedia_page_media(args.wikipedia_page, lang=args.lang)
            raw = _http_get_bytes(meta["url"])
            entry = save_media_item(
                args.output_dir,
                media_id=media_id,
                raw_bytes=raw,
                source_url=meta["url"],
                title=args.title or meta["title"],
                credit=args.credit or meta["credit"],
                license_name=args.license or meta["license"],
            )
            saved.append(entry)
        elif args.commons_file:
            media_id = args.id or re.sub(r"[^A-Za-z0-9_-]+", "-", args.commons_file.removeprefix("File:")).strip("-").lower()[:48]
            meta = fetch_commons_file(args.commons_file)
            raw = _http_get_bytes(meta["url"])
            entry = save_media_item(
                args.output_dir,
                media_id=media_id,
                raw_bytes=raw,
                source_url=meta["url"],
                title=args.title or meta["title"],
                credit=args.credit or meta["credit"],
                license_name=args.license or meta["license"],
            )
            saved.append(entry)
        elif args.commons_search:
            hits = search_commons_media(args.commons_search, limit=args.limit)
            if not hits:
                raise InputError(f"no Wikimedia Commons bitmap images found for '{args.commons_search}'")
            base_id = args.id or "media"
            for idx, hit in enumerate(hits, 1):
                item_id = base_id if len(hits) == 1 else f"{base_id}-{idx:02d}"
                raw = _http_get_bytes(hit["url"])
                entry = save_media_item(
                    args.output_dir,
                    media_id=item_id,
                    raw_bytes=raw,
                    source_url=hit["url"],
                    title=args.title or hit["title"],
                    credit=args.credit or hit["credit"],
                    license_name=args.license or hit["license"],
                )
                saved.append(entry)
        else:
            raise InputError("specify one of --url, --wikipedia-page, --commons-search, or --verify")

        print(json.dumps({"status": "ok", "saved": saved}, indent=2, ensure_ascii=True))
        return 0
    except (InputError, urllib.error.URLError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
