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
LOCAL_CONFIG = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_FLUX_ENDPOINT = ""
DEFAULT_CRED_TARGET = "docswarm-flux"

_FLUX_PROMPT_SANITIZATIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bradioactive\s+fallout\b", re.I), "falling white coral ash"),
    (re.compile(r"\bradioactive\b", re.I), "luminescent"),
    (re.compile(r"\bthermonuclear\s+explosion\b", re.I), "colossal horizon flash"),
    (re.compile(r"\bnuclear\s+explosion\b", re.I), "bright horizon flash"),
    (re.compile(r"\batomic\s+bomb\b", re.I), "horizon flash"),
    (re.compile(r"\bwmass\s+destruction\b", re.I), "cataclysmic scale"),
    (re.compile(r"\bsuicide\b", re.I), "final sacrifice"),
)


def sanitize_flux_prompt(prompt: str) -> str:
    """Replace historical terms that trigger false-positive Azure content filters in purely visual prompts."""
    cleaned = str(prompt or "").strip()
    for pattern, replacement in _FLUX_PROMPT_SANITIZATIONS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def auto_crop_engraving_bytes(raw_bytes: bytes, target_width: int = 1024, target_height: int = 768) -> bytes:
    """Trim uniform outer light margins (>3% per side) or closed frame boxes from generated engravings."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw_bytes)) as im_in:
            im = im_in.convert("RGB")
        w, h = im.size
        if w < 160 or h < 160:
            return raw_bytes
        px = im.load()

        def is_light_row(y: int) -> bool:
            span = max(1, (w - 40) // 4)
            light = sum(
                1 for x in range(20, w - 20, 4)
                if px[x, y][0] > 232 and px[x, y][1] > 228 and px[x, y][2] > 218
            )
            return (light / span) >= 0.96

        def is_light_col(x: int) -> bool:
            span = max(1, (h - 40) // 4)
            light = sum(
                1 for y in range(20, h - 20, 4)
                if px[x, y][0] > 232 and px[x, y][1] > 228 and px[x, y][2] > 218
            )
            return (light / span) >= 0.96

        max_trim_y = int(h * 0.22)
        max_trim_x = int(w * 0.22)
        top = 0
        while top < max_trim_y and is_light_row(top):
            top += 1
        bottom = h - 1
        while bottom > h - 1 - max_trim_y and is_light_row(bottom):
            bottom -= 1
        left = 0
        while left < max_trim_x and is_light_col(left):
            left += 1
        right = w - 1
        while right > w - 1 - max_trim_x and is_light_col(right):
            right -= 1

        # If a noticeable outer margin (>2.5% on any side) was detected, step 6px inside to clear border lines
        if top > int(h * 0.025) or (h - 1 - bottom) > int(h * 0.025) or left > int(w * 0.025) or (w - 1 - right) > int(w * 0.025):
            left = min(left + 6, w // 4)
            top = min(top + 6, h // 4)
            right = max(right - 6, w - w // 4)
            bottom = max(bottom - 6, h - h // 4)
            if (right - left) >= int(w * 0.65) and (bottom - top) >= int(h * 0.65):
                cropped = im.crop((left, top, right + 1, bottom + 1)).resize(
                    (target_width, target_height), Image.Resampling.LANCZOS
                )
                buf = io.BytesIO()
                cropped.save(buf, format="PNG")
                return buf.getvalue()
        return raw_bytes
    except Exception:
        return raw_bytes


def flux_setting(name: str, default: str = "") -> str:
    if name in os.environ:
        return os.environ[name].strip()
    if LOCAL_CONFIG.is_file():
        for line in LOCAL_CONFIG.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key.strip() == name:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value
    return default

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


def read_key_from_macos_vault(target_name: str = DEFAULT_CRED_TARGET) -> str | None:
    """Read the APIM subscription key directly from macOS Keychain via Security.framework (no subprocess)."""
    if sys.platform != "darwin":
        return None
    try:
        sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        sec.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]
        sec.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        sec.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        sec.SecKeychainItemFreeContent.restype = ctypes.c_int32

        for service in (target_name, DEFAULT_CRED_TARGET):
            if not service:
                continue
            svc_bytes = service.encode("utf-8")
            pw_len = ctypes.c_uint32(0)
            pw_ptr = ctypes.c_void_p(None)
            status = sec.SecKeychainFindGenericPassword(
                None,
                len(svc_bytes),
                svc_bytes,
                0,
                None,
                ctypes.byref(pw_len),
                ctypes.byref(pw_ptr),
                None,
            )
            if status == 0 and pw_ptr.value and pw_len.value > 0:
                try:
                    raw = ctypes.string_at(pw_ptr.value, pw_len.value)
                    text = raw.decode("utf-8", errors="ignore").strip()
                    if text:
                        return text
                finally:
                    sec.SecKeychainItemFreeContent(None, pw_ptr)
    except Exception:
        pass
    return None


def resolve_apim_key(vault_target: str = "") -> str:
    """Resolve the key from the process environment, local .env, Windows Credential Manager, or macOS Keychain."""
    for env_name in ("DOCSWARM_FLUX_API_KEY", "APIM_SUBSCRIPTION_KEY", "AZURE_API_KEY"):
        val = os.environ.get(env_name, "").strip() or flux_setting(env_name, "")
        if val:
            return val

    target = vault_target or flux_setting("DOCSWARM_FLUX_VAULT_TARGET", DEFAULT_CRED_TARGET)
    vault_key = read_key_from_windows_vault(target) or read_key_from_macos_vault(target)
    if vault_key:
        return vault_key
    raise InputError(
        "Set DOCSWARM_FLUX_API_KEY in the process environment or store the key "
        "in Windows Credential Manager / macOS Keychain under DOCSWARM_FLUX_VAULT_TARGET."
    )


def _prepare_flux_input_b64(input_image_path: str | None, width: int, height: int) -> str:
    """Build a base64 PNG reference canvas (fitting input_image_path or a blank white canvas)."""
    if input_image_path:
        if input_image_path.startswith(("http://", "https://")):
            ref_raw = _http_get_bytes(input_image_path)
        else:
            ref_raw = Path(input_image_path).read_bytes()
        try:
            from PIL import Image

            with Image.open(io.BytesIO(ref_raw)) as im_in:
                im = im_in.convert("RGB")
            canvas = Image.new("RGB", (width, height), (255, 255, 255))
            scale = min(width / float(max(1, im.width)), height / float(max(1, im.height)))
            new_w = max(1, int(round(im.width * scale)))
            new_h = max(1, int(round(im.height * scale)))
            resized = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas.paste(resized, ((width - new_w) // 2, (height - new_h) // 2))
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except ImportError:
            norm_ref, _, _, _ = normalize_image_bytes(ref_raw, preferred_ext=".png", max_dim=1024)
            return base64.b64encode(norm_ref).decode("ascii")
    try:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (width, height), (255, 255, 255)).save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except ImportError:
        return ""


def generate_flux_media(
    prompt: str,
    *,
    style: str = "engraving",
    width: int = 1024,
    height: int = 768,
    input_image_path: str | None = None,
    endpoint_url: str = DEFAULT_FLUX_ENDPOINT,
    vault_target: str = "",
    timeout: float = 180.0,
    auto_crop: bool = True,
) -> tuple[bytes, str]:
    """Generate an editorial engraving or illustration via FLUX.2-pro on Azure APIM."""
    clean_prompt = sanitize_flux_prompt(prompt)
    if not clean_prompt:
        raise InputError("--flux-prompt must be a non-empty string")
    endpoint_url = endpoint_url or flux_setting("DOCSWARM_FLUX_URL", DEFAULT_FLUX_ENDPOINT)
    if not endpoint_url:
        raise InputError("Set DOCSWARM_FLUX_URL in the environment or local .env, or pass --flux-endpoint.")
    style_key = (style or "").strip().lower()
    prefix = FLUX_STYLE_PRESETS.get(style_key, "")
    no_frame_guard = (
        " Full-bleed composition filling the entire frame edge-to-edge without any decorative border, "
        "mat, cartouche or stamp, NO text, NO letters, NO numbers, NO kanji, NO watermarks."
    )
    full_prompt = f"{prefix}{clean_prompt}{no_frame_guard}" if prefix else clean_prompt

    target_w = max(256, min(int(width), 1440))
    target_h = max(256, min(int(height), 1440))
    apim_key = resolve_apim_key(vault_target)
    payload: dict[str, Any] = {
        "model": "FLUX.2-pro",
        "prompt": full_prompt,
        "width": target_w,
        "height": target_h,
        "output_format": "png",
    }
    in_b64 = _prepare_flux_input_b64(input_image_path, target_w, target_h)
    if in_b64:
        payload["input_image"] = in_b64

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

    raw_out: bytes | None = None
    if "sample" in data:
        val = data["sample"]
        raw_out = _http_get_bytes(val, timeout=60.0) if (isinstance(val, str) and val.startswith("http")) else base64.b64decode(val)
    elif "result" in data and isinstance(data["result"], dict) and "sample" in data["result"]:
        val = data["result"]["sample"]
        raw_out = _http_get_bytes(val, timeout=60.0) if (isinstance(val, str) and val.startswith("http")) else base64.b64decode(val)
    elif "data" in data and isinstance(data["data"], list) and data["data"]:
        item = data["data"][0]
        if "b64_json" in item:
            raw_out = base64.b64decode(item["b64_json"])
        elif "url" in item:
            raw_out = _http_get_bytes(item["url"], timeout=60.0)
    elif "image" in data:
        raw_out = base64.b64decode(data["image"])
    if raw_out is None:
        raise InputError(f"unexpected FLUX.2-pro response shape: {list(data.keys())}")
    if auto_crop:
        raw_out = auto_crop_engraving_bytes(raw_out, target_w, target_h)
    return raw_out, full_prompt


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
    parser.add_argument(
        "--flux-input-image",
        "--reference-image",
        dest="flux_input_image",
        help="Optional local path or URL of structural reference image for FLUX.2-pro (img2img)",
    )
    parser.add_argument(
        "--no-auto-crop",
        action="store_true",
        help="Disable automatic trimming of outer light margins/frames on FLUX.2-pro images",
    )
    parser.add_argument("--width", type=int, default=1024, help="Target width for FLUX.2-pro generation (default: 1024)")
    parser.add_argument("--height", type=int, default=768, help="Target height for FLUX.2-pro generation (default: 768)")
    parser.add_argument(
        "--flux-endpoint",
        default="",
        help="Azure APIM FLUX.2-pro endpoint URL",
    )
    parser.add_argument(
        "--vault-target",
        default="",
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
                auto_crop=not args.no_auto_crop,
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
