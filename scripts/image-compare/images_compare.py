#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026-present Team LibreELEC (https://libreelec.tv)

"""LibreELEC Image Comparison Tool.

Compares two LibreELEC images (.img.gz or .img) by extracting and analyzing
their SquashFS contents. Provides detailed reports of added, removed, renamed, and
changed files with size information.
"""

import argparse
import contextlib
import hashlib
import io
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from prettytable import PrettyTable

# Minimum file size change (in bytes) to include in summary reports
MIN_SUMMARY_CHANGE_BYTES = 1024


def run_command(command: list[str], cwd: str | None = None) -> None:
    """Execute a shell command, suppressing output and raising an exception on failure.

    Args:
        command: List of command-line arguments to execute.
        cwd: Working directory where the command should be run.

    Raises:
        subprocess.CalledProcessError: If the command returns a non-zero exit status.
    """
    subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def is_squashfs(path: str) -> bool:
    """Check if a file is a SquashFS filesystem using the 'file' command.

    Args:
        path: Path to the file to check.

    Returns:
        True if the file is a SquashFS filesystem, False otherwise.
    """
    try:
        result = subprocess.run(
            ["file", path], check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError:
        return False
    return "Squashfs filesystem" in result.stdout


def extract_squashfs_from_image(image_path: str, extract_dir: str) -> str:
    """Extract a SquashFS filesystem from a LibreELEC image file.

    Handles various image formats: .gz archives, raw .img files, and partitioned images.

    Args:
        image_path: Path to the input image file (e.g., .img or .img.gz).
        extract_dir: Directory where temporary extraction output should be stored.

    Returns:
        The path to the extracted SquashFS directory.

    Raises:
        RuntimeError: If no SquashFS filesystem could be found in the image.
    """
    os.makedirs(extract_dir, exist_ok=True)

    # Step 1: Extract archive contents (handles .gz or raw .img)
    run_command(["7z", "x", "-y", f"-o{extract_dir}", image_path])

    extracted_files = []
    for root, _, files in os.walk(extract_dir):
        for filename in files:
            extracted_files.append(os.path.join(root, filename))

    # Step 2: If one of the extracted files is already a squashfs image, use it directly.
    for candidate in sorted(extracted_files, key=os.path.getsize, reverse=True):
        if is_squashfs(candidate):
            squashfs_dir = os.path.join(extract_dir, "squashfs")
            os.makedirs(squashfs_dir, exist_ok=True)
            run_command(["unsquashfs", "-f", "-d", squashfs_dir, candidate])
            return squashfs_dir

    # Step 3: For partitioned disk images, extract the partitions first.
    partition_files = []
    for candidate in sorted(extracted_files, key=os.path.getsize, reverse=True):
        if os.path.getsize(candidate) == 0:
            continue

        partition_dir = os.path.join(
            extract_dir, "partitions", os.path.basename(candidate)
        )
        os.makedirs(partition_dir, exist_ok=True)
        try:
            run_command(["7z", "x", "-y", f"-o{partition_dir}", candidate])
        except subprocess.CalledProcessError:
            continue

        for root, _, files in os.walk(partition_dir):
            for filename in files:
                partition_files.append(os.path.join(root, filename))

    # Step 4: Inspect the extracted partition files for a SYSTEM payload.
    for candidate in sorted(partition_files, key=os.path.getsize, reverse=True):
        if os.path.getsize(candidate) == 0:
            continue

        if is_squashfs(candidate):
            squashfs_dir = os.path.join(extract_dir, "squashfs")
            os.makedirs(squashfs_dir, exist_ok=True)
            run_command(["unsquashfs", "-f", "-d", squashfs_dir, candidate])
            return squashfs_dir

        system_dir = os.path.join(extract_dir, "system")
        os.makedirs(system_dir, exist_ok=True)
        try:
            run_command(["7z", "e", "-y", f"-o{system_dir}", candidate, "SYSTEM"])
        except subprocess.CalledProcessError:
            continue

        system_file = os.path.join(system_dir, "SYSTEM")
        if os.path.exists(system_file):
            squashfs_dir = os.path.join(extract_dir, "squashfs")
            os.makedirs(squashfs_dir, exist_ok=True)
            run_command(["unsquashfs", "-f", "-d", squashfs_dir, system_file])
            return squashfs_dir

    raise RuntimeError(f"Unable to find a SquashFS filesystem inside {image_path}")


def format_size(size: int | float) -> str:
    """Convert a byte size to a human-readable format (KiB or MiB).

    Args:
        size: The size in bytes to format.

    Returns:
        A human-readable string representation of the size.
    """
    value = float(size)
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


def format_size_change(size: int | float) -> str:
    """Format a size change value with a +/- sign and human-readable format.

    Args:
        size: The size difference in bytes.

    Returns:
        A formatted string indicating the size change (e.g., "+1.5 MiB" or "-300.0 KiB").
    """
    sign = "+" if size >= 0 else "-"
    return f"{sign}{format_size(abs(size))}"


def normalize_path(path: str) -> str:
    """Normalize file paths by replacing version numbers and dates with placeholders.

    This allows matching files across images that differ only in version numbers.
    Handles .so library versions, version strings in filenames, dates, and kernel modules.

    Args:
        path: The original file path.

    Returns:
        The normalized file path with version numbers/dates replaced by "<version>".
    """
    # Replace version numbers in .so library files (e.g., .so.1.2.3 -> .so.<version>)
    path = re.sub(r"(?<=\.so\.)\d+(?:\.\d+)*", "<version>", path)
    # Replace version numbers before .so (e.g., libc-2.36.so -> libc-<version>.so)
    path = re.sub(r"(?<=-)\d+(?:\.\d+)*(?=\.so(?:\.|$))", "<version>", path)
    # Replace date strings in format YYYY-MM-DD
    path = re.sub(
        r"(?<![A-Za-z0-9])\d{4}-\d{2}-\d{2}(?![A-Za-z0-9])", "<version>", path
    )
    # Replace numeric path components (e.g., /1.2.3/ -> /<version>/)
    path = re.sub(r"(?<=/)\d+(?:\.\d+)*(?=/|$)", "<version>", path)

    # Handle kernel module versioning (e.g., /usr/lib/kernel-overlays/base/lib/modules/6.1.0/...)
    prefix = "usr/lib/kernel-overlays/base/lib/modules/"
    if path.startswith(prefix):
        remainder = path[len(prefix) :]
        if remainder:
            parts = remainder.split("/", 1)
            if len(parts) == 2:
                return f"{prefix}<version>/{parts[1]}"
            return f"{prefix}<version>"
    return path


def find_version_renames(
    map1: dict[str, tuple[int, str]], map2: dict[str, tuple[int, str]]
) -> list[tuple[str, str]]:
    """Identify files that were renamed due to version number changes.

    Maps normalized paths (with version placeholders) to actual paths in both images,
    then finds files with the same normalized path but different actual paths.

    Args:
        map1: File map for the first image.
        map2: File map for the second image.

    Returns:
        A list of tuples, each containing (old_path, new_path) for renamed files.
    """
    # Create mappings from normalized paths to actual paths
    norm_to_paths1 = defaultdict(list)
    norm_to_paths2 = defaultdict(list)

    for path in map1:
        norm_to_paths1[normalize_path(path)].append(path)
    for path in map2:
        norm_to_paths2[normalize_path(path)].append(path)

    # Find single-file renames (paths that match when normalized but differ literally)
    renames = []
    for norm in set(norm_to_paths1) & set(norm_to_paths2):
        paths1 = norm_to_paths1[norm]
        paths2 = norm_to_paths2[norm]
        # Only match if there's exactly one file on each side with the same normalized path
        if len(paths1) == 1 and len(paths2) == 1:
            old_path = paths1[0]
            new_path = paths2[0]
            if old_path != new_path:
                renames.append((old_path, new_path))
    return renames


def build_file_map(root_dir: str) -> dict[str, tuple[int, str]]:
    """Build a map of all files in a directory with their sizes and SHA256 hashes.

    Skips symlinks and handles file permission errors gracefully.

    Args:
        root_dir: The directory to scan.

    Returns:
        A dictionary mapping relative paths to a tuple containing (size_in_bytes, sha256_hash).
    """
    file_map = {}
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, root_dir)

            # Skip symbolic links
            if os.path.islink(full_path):
                continue

            # Only process regular files
            if not os.path.isfile(full_path):
                continue

            try:
                # Calculate file size and SHA256 hash
                size = os.path.getsize(full_path)
                digest = hashlib.sha256()
                with open(full_path, "rb") as handle:
                    # Read file in 1MB chunks to handle large files efficiently
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except (PermissionError, OSError):
                # Skip files that can't be read
                continue

            file_map[rel_path] = (size, digest.hexdigest())
    return file_map


def compare_images(
    img1: str,
    img2: str,
    min_summary_change_bytes: int = MIN_SUMMARY_CHANGE_BYTES,
) -> None:
    """Compare two LibreELEC images by extracting and analyzing their SquashFS contents.

    Generates detailed reports of file changes including additions, removals, renames,
    and content changes, with both detailed and summary views.

    Args:
        img1: Path to the first image file.
        img2: Path to the second image file.
        min_summary_change_bytes: Minimum file size change (in bytes) to include in summary reports.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Extract both images
        dir1 = extract_squashfs_from_image(img1, os.path.join(tmpdir, "img1"))
        dir2 = extract_squashfs_from_image(img2, os.path.join(tmpdir, "img2"))

        # Build file maps (path -> size and hash)
        map1 = build_file_map(dir1)
        map2 = build_file_map(dir2)

        # Minimum size difference to track changes (filters out trivial updates)
        min_size_diff_bytes = 50

        # Identify added and removed files
        raw_added = set(map2.keys()) - set(map1.keys())
        raw_removed = set(map1.keys()) - set(map2.keys())

        # Find files that were renamed (version updates)
        renamed_paths = [
            (old_path, new_path)
            for old_path, new_path in find_version_renames(map1, map2)
            if old_path in raw_removed and new_path in raw_added
        ]
        # Remove renamed files from added/removed sets to avoid double-counting
        for old_path, new_path in renamed_paths:
            raw_removed.discard(old_path)
            raw_added.discard(new_path)

        # Filter files by minimum size threshold
        added = {path for path in raw_added if map2[path][0] >= min_size_diff_bytes}
        removed = {path for path in raw_removed if map1[path][0] >= min_size_diff_bytes}

        # Find files that changed (different size or content)
        common = set(map1.keys()) & set(map2.keys())
        changed = {
            path
            for path in common
            if abs(map2[path][0] - map1[path][0]) >= min_size_diff_bytes
            and map1[path] != map2[path]
        }

        # Separate normal and kernel files (under usr/lib/kernel-overlays)
        def is_kernel(path: str) -> bool:
            return path.startswith("usr/lib/kernel-overlays")

        added_normal = sorted([p for p in added if not is_kernel(p)])
        added_kernel = sorted([p for p in added if is_kernel(p)])

        removed_normal = sorted([p for p in removed if not is_kernel(p)])
        removed_kernel = sorted([p for p in removed if is_kernel(p)])

        renamed_normal = sorted(
            [
                (old, new)
                for old, new in renamed_paths
                if not (is_kernel(old) or is_kernel(new))
            ],
            key=lambda item: (item[1], item[0]),
        )
        renamed_kernel = sorted(
            [
                (old, new)
                for old, new in renamed_paths
                if is_kernel(old) or is_kernel(new)
            ],
            key=lambda item: (item[1], item[0]),
        )

        changed_normal = sorted([p for p in changed if not is_kernel(p)])
        changed_kernel = sorted([p for p in changed if is_kernel(p)])

        # Print normal added files
        if added_normal:
            print("Added Files")
            print("-----------")
            added_table = PrettyTable(["File", "Size"])
            added_table.align["File"] = "l"
            added_table.align["Size"] = "r"
            added_table.padding_width = 1
            for path in added_normal:
                added_table.add_row([path, format_size(map2[path][0])])
            print(added_table)
            print()

        # Print normal removed files
        if removed_normal:
            print("Removed Files")
            print("-------------")
            removed_table = PrettyTable(["File", "Size"])
            removed_table.align["File"] = "l"
            removed_table.align["Size"] = "r"
            removed_table.padding_width = 1
            for path in removed_normal:
                removed_table.add_row([path, format_size(map1[path][0])])
            print(removed_table)
            print()

        # Print normal renamed files
        if renamed_normal:
            print("Renamed Files")
            print("-------------")
            renamed_table = PrettyTable(["Old File", "New File", "Size Change"])
            renamed_table.align["Old File"] = "l"
            renamed_table.align["New File"] = "l"
            renamed_table.align["Size Change"] = "r"
            renamed_table.padding_width = 1
            for old_path, new_path in renamed_normal:
                size_diff = map2[new_path][0] - map1[old_path][0]
                renamed_table.add_row(
                    [old_path, new_path, format_size_change(size_diff)]
                )
            print(renamed_table)
            print()

        # Print normal changed files
        if changed_normal:
            print("Changed Files")
            print("-------------")
            changed_table = PrettyTable(["File", "Size Change"])
            changed_table.align["File"] = "l"
            changed_table.align["Size Change"] = "r"
            changed_table.padding_width = 1
            for path in changed_normal:
                size_diff = map2[path][0] - map1[path][0]
                changed_table.add_row([path, format_size_change(size_diff)])
            print(changed_table)
            print()

        # Print kernel added files
        if added_kernel:
            print("Kernel Files Added")
            print("------------------")
            added_table = PrettyTable(["File", "Size"])
            added_table.align["File"] = "l"
            added_table.align["Size"] = "r"
            added_table.padding_width = 1
            for path in added_kernel:
                added_table.add_row([path, format_size(map2[path][0])])
            print(added_table)
            print()

        # Print kernel removed files
        if removed_kernel:
            print("Kernel Files Removed")
            print("--------------------")
            removed_table = PrettyTable(["File", "Size"])
            removed_table.align["File"] = "l"
            removed_table.align["Size"] = "r"
            removed_table.padding_width = 1
            for path in removed_kernel:
                removed_table.add_row([path, format_size(map1[path][0])])
            print(removed_table)
            print()

        # Print kernel renamed files
        if renamed_kernel:
            print("Kernel Files Renamed")
            print("--------------------")
            renamed_table = PrettyTable(["Old File", "New File", "Size Change"])
            renamed_table.align["Old File"] = "l"
            renamed_table.align["New File"] = "l"
            renamed_table.align["Size Change"] = "r"
            renamed_table.padding_width = 1
            for old_path, new_path in renamed_kernel:
                size_diff = map2[new_path][0] - map1[old_path][0]
                renamed_table.add_row(
                    [old_path, new_path, format_size_change(size_diff)]
                )
            print(renamed_table)
            print()

        # Print kernel changed files
        if changed_kernel:
            print("Kernel Files Changed")
            print("--------------------")
            changed_table = PrettyTable(["File", "Size Change"])
            changed_table.align["File"] = "l"
            changed_table.align["Size Change"] = "r"
            changed_table.padding_width = 1
            for path in changed_kernel:
                size_diff = map2[path][0] - map1[path][0]
                changed_table.add_row([path, format_size_change(size_diff)])
            print(changed_table)

        # Collect size changes for summary report
        changed_diffs = [(path, map2[path][0] - map1[path][0]) for path in changed]
        renamed_diffs = [
            (new_path, map2[new_path][0] - map1[old_path][0])
            for old_path, new_path in renamed_paths
        ]

        # Sort files by size increase (largest increases first)
        increased_items = sorted(
            (
                (path, diff)
                for path, diff in changed_diffs + renamed_diffs
                if diff >= min_summary_change_bytes
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        # Sort files by size decrease (largest decreases first)
        decreased_items = sorted(
            (
                (path, -diff)
                for path, diff in changed_diffs + renamed_diffs
                if diff <= -min_summary_change_bytes
            ),
            key=lambda item: item[1],
            reverse=True,
        )

        # Calculate total size difference
        overall_diff = sum(diff for _, diff in changed_diffs + renamed_diffs)

        # Print summary section
        print()
        print(f"Compared images: {img1} vs {img2}")
        print("Summary")
        print("-------")
        print(f"Overall size difference: {format_size_change(overall_diff)}")

        # Print top files with size increases
        increase_summary = PrettyTable(["Rank", "File", "Increase"])
        increase_summary.align["File"] = "l"
        increase_summary.align["Increase"] = "r"
        increase_summary.padding_width = 1
        for idx, (path, size_diff) in enumerate(increased_items, start=1):
            increase_summary.add_row([idx, path, f"+{format_size(size_diff)}"])
        if not increased_items:
            increase_summary.add_row(["-", "None", "-"])

        # Print top files with size decreases
        decrease_summary = PrettyTable(["Rank", "File", "Decrease"])
        decrease_summary.align["File"] = "l"
        decrease_summary.align["Decrease"] = "r"
        decrease_summary.padding_width = 1
        for idx, (path, size_diff) in enumerate(decreased_items, start=1):
            decrease_summary.add_row([idx, path, f"-{format_size(size_diff)}"])
        if not decreased_items:
            decrease_summary.add_row(["-", "None", "-"])

        print(increase_summary)
        print()
        print(decrease_summary)


def get_monospaced_font(size: int = 14):
    """Retrieve a monospaced TrueType font from common system paths or fall back to default."""
    from PIL import ImageFont

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_text_to_png(text: str, output_path: str) -> None:
    """Render a text block (like console tables) to a clean dark-themed PNG file."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Error: The 'Pillow' library is required to output to a PNG file.")
        print("Please install it with: pip install pillow")
        return

    font = get_monospaced_font(14)
    lines = text.splitlines()

    # Calculate line and image sizes
    dummy = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(dummy)

    try:
        if hasattr(font, "getbbox"):
            bbox = font.getbbox("Ay")
            line_height = int((bbox[3] - bbox[1]) * 1.4)
        elif hasattr(draw, "textbbox"):
            bbox = draw.textbbox((0, 0), "Ay", font=font)
            line_height = int((bbox[3] - bbox[1]) * 1.4)
        else:
            w, h = font.getsize("Ay")
            line_height = int(h * 1.4)
    except Exception:
        line_height = 20

    widths = []
    for line in lines:
        try:
            if hasattr(font, "getbbox"):
                bbox = font.getbbox(line)
                widths.append(bbox[2] - bbox[0])
            elif hasattr(draw, "textbbox"):
                bbox = draw.textbbox((0, 0), line, font=font)
                widths.append(bbox[2] - bbox[0])
            else:
                w, h = font.getsize(line)
                widths.append(w)
        except Exception:
            widths.append(len(line) * 8)

    max_width = max(widths) if widths else 100
    padding = 20
    image_width = max_width + (padding * 2)
    image_height = (line_height * len(lines)) + (padding * 2)

    # Use a premium dark-themed color scheme (charcoal bg, soft white fg)
    background_color = (30, 30, 36)
    text_color = (227, 227, 230)

    image = Image.new("RGB", (image_width, image_height), color=background_color)
    draw = ImageDraw.Draw(image)

    y = padding
    for line in lines:
        try:
            draw.text((padding, y), line, font=font, fill=text_color)
        except Exception:
            draw.text((padding, y), line, fill=text_color)
        y += line_height

    # Quantize to 16 colors (4-bit palette) to drastically reduce file size
    try:
        quantized = image.quantize(colors=16)
        quantized.save(output_path, "PNG", optimize=True)
    except Exception:
        # Fallback to standard RGB save if quantization fails
        image.save(output_path, "PNG", optimize=True)
    print(f"Report successfully saved as 4-bit PNG image to: {output_path}")


if __name__ == "__main__":
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description=(
            "Compare two LibreELEC images (.img.gz or .img) "
            "by unpacking their SquashFS contents."
        )
    )
    parser.add_argument("image1", help="First image file")
    parser.add_argument("image2", help="Second image file")
    parser.add_argument(
        "--min-summary-change",
        type=int,
        default=MIN_SUMMARY_CHANGE_BYTES,
        help=(
            "Only include summary entries with size changes at least this "
            f"large, in bytes (default: {MIN_SUMMARY_CHANGE_BYTES})"
        ),
    )
    parser.add_argument(
        "--png",
        help="Path to save the comparison report as a PNG image"
    )
    args = parser.parse_args()

    # Verify input files exist
    import sys
    for path, name in [(args.image1, "First"), (args.image2, "Second")]:
        if not os.path.exists(path):
            print(f"Error: {name} image file not found: '{path}'", file=sys.stderr)
            sys.exit(1)
        if not os.path.isfile(path):
            print(f"Error: '{path}' is not a regular file", file=sys.stderr)
            sys.exit(1)

    # Run the comparison
    if args.png:
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            compare_images(
                args.image1,
                args.image2,
                min_summary_change_bytes=args.min_summary_change,
            )
        output_text = f.getvalue()
        print(output_text, end="")
        render_text_to_png(output_text, args.png)
    else:
        compare_images(
            args.image1,
            args.image2,
            min_summary_change_bytes=args.min_summary_change,
        )
