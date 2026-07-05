# LibreELEC Image Comparison Tool

This tool (`images_compare.py`) allows you to compare two LibreELEC image files (`.img.gz`) by extracting and analyzing their SquashFS contents (`SYSTEM`).

It provides detailed tables and summary reports showing added, removed, renamed, and changed files along with their size differences.

---

## Dependencies

To run the script, you need to install both system utility packages and a Python package.

### 1. System Dependencies

*assuming Ubuntu 26.04*

The script invokes external command-line utilities via `subprocess`. Ensure you have the following installed:
* **`7z`** (from `7zip`): To extract `.img.gz` archives.
* **`unsquashfs`** (from `squashfs-tools`): To extract and unpack the SquashFS system partition (`SYSTEM`).
* **`file`** (from `file`): To check if a candidate payload is indeed a SquashFS filesystem.

#### Installation on Ubuntu / Debian:
```bash
sudo apt update
sudo apt install -y 7zip squashfs-tools file
```

### 2. Python Dependencies

The script requires:
* `prettytable` to format report tables.
* `pillow` (PIL) to optionally output the report to a PNG file.

#### Installation apt managed:
```bash
sudo apt install python3-prettytable python3-pil
```
*(Or run `pip install -r requirements.txt` directly)*

---

## Usage

Run the script from your terminal:

```bash
python3 images_compare.py <image1> <image2> [options]
```

### Arguments

* **`image1`** (Positional): Path to the first image file (older version / baseline).
* **`image2`** (Positional): Path to the second image file (newer version / target).
* **`--min-summary-change <bytes>`** (Optional): Only include changes in the summary tables that are larger than or equal to this threshold in bytes (default: `1024` bytes / 1 KiB).
* **`--png <path>`** (Optional): Path to render and save the full comparison report as a highly compressed 4-bit PNG image file (to save file size).

---

## Examples

### 1. Basic Comparison

Compare two nightly build images:
```bash
python3 images_compare.py \
  LibreELEC-RPi5.aarch64-13.0-nightly-20260616-ee5dfbf.img.gz \
  LibreELEC-RPi5.aarch64-13.0-nightly-20260624-b52cea4.img.gz
```

#### Example Output:
```text
Kernel Files Renamed
--------------------
+--------------------------------------------------------------------+--------------------------------------------------------------------+-------------+
| Old File                                                           | New File                                                           | Size Change |
+--------------------------------------------------------------------+--------------------------------------------------------------------+-------------+
| usr/lib/kernel-overlays/base/lib/modules/6.18.35/modules.alias     | usr/lib/kernel-overlays/base/lib/modules/6.18.36/modules.alias     |    +0.1 KiB |
| usr/lib/kernel-overlays/base/lib/modules/6.18.35/modules.alias.bin | usr/lib/kernel-overlays/base/lib/modules/6.18.36/modules.alias.bin |    +0.1 KiB |
| usr/lib/kernel-overlays/base/lib/modules/6.18.35/modules.symbols   | usr/lib/kernel-overlays/base/lib/modules/6.18.36/modules.symbols   |    +0.1 KiB |
+--------------------------------------------------------------------+--------------------------------------------------------------------+-------------+

Kernel Files Changed
--------------------
+-------------------------------------------------------------------------+-------------+
| File                                                                    | Size Change |
+-------------------------------------------------------------------------+-------------+
| usr/lib/kernel-overlays/base/lib/firmware/rtl_bt/rtl8852au_fw.bin       |    -3.7 KiB |
| usr/lib/kernel-overlays/base/lib/firmware/rtw89/rtw8852b_fw-2.bin       |    +0.4 KiB |
| usr/lib/kernel-overlays/base/lib/firmware/rtw89/rtw8852bt_fw.bin        |    +0.2 KiB |
+-------------------------------------------------------------------------+-------------+

Compared images: LibreELEC-RPi5.aarch64-13.0-nightly-20260616-ee5dfbf.img.gz vs LibreELEC-RPi5.aarch64-13.0-nightly-20260624-b52cea4.img.gz

Summary
-------
Overall size difference: +889.1 KiB
+------+-------------------------------------------------------------------+------------+
| Rank | File                                                              |   Increase |
+------+-------------------------------------------------------------------+------------+
|  1   | usr/lib/systemd/libsystemd-shared-261.so                          | +321.1 KiB |
|  2   | usr/lib/systemd/libsystemd-core-261.so                            | +128.2 KiB |
|  3   | usr/bin/systemd-vmspawn                                           |  +64.2 KiB |
|  4   | usr/bin/bootctl                                                   |  +64.2 KiB |
|  5   | usr/lib/systemd/systemd                                           |  +64.2 KiB |
|  6   | usr/lib/systemd/systemd-oomd                                      |  +64.1 KiB |
|  7   | usr/bin/systemd-dissect                                           |  +64.1 KiB |
|  8   | usr/lib/libsystemd.so.0.44.0                                      |  +64.1 KiB |
|  9   | usr/lib/libgallium-26.1.3.so                                      |  +64.0 KiB |
|  10  | usr/lib/systemd/systemd-logind                                    |  +64.0 KiB |
+------+-------------------------------------------------------------------+------------+

+------+-------------------------------------------------------------------+------------+
| Rank | File                                                              |   Decrease |
+------+-------------------------------------------------------------------+------------+
|  1   | usr/lib/libtextstyle.so.0.2.7                                     | -224.9 KiB |
|  2   | usr/lib/kernel-overlays/base/lib/firmware/rtl_bt/rtl8852au_fw.bin |   -3.7 KiB |
|  3   | usr/share/xkeyboard-config-2/rules/base                           |   -3.3 KiB |
|  4   | usr/share/xkeyboard-config-2/rules/evdev                          |   -2.9 KiB |
+------+-------------------------------------------------------------------+------------+
```

### 2. Custom Size Threshold Filter

To only list files in the summary that have changed by at least 10 KiB (10240 bytes):
```bash
python3 images_compare.py \
  LibreELEC-RPi5.aarch64-13.0-nightly-20260616-ee5dfbf.img.gz \
  LibreELEC-RPi5.aarch64-13.0-nightly-20260624-b52cea4.img.gz \
  --min-summary-change 10240
```
