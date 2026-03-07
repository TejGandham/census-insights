#!/usr/bin/env bash
# Downloads ACS 5-Year Summary Files for years 2019-2023 from Census FTP.
#
# Run once manually before starting Docker:
#   bash scripts/download_acs_bulk.sh
#
# Files saved to: data/raw/acs/{year}/
# Total download: ~53 GB across all years.
#
# Uses wget -c for resumable downloads (critical for multi-GB files).
# Falls back to curl if wget is unavailable.

set -euo pipefail

BASE="https://www2.census.gov/programs-surveys/acs/summary_file"
DEST="${1:-data/raw/acs}"

# ---------------------------------------------------------------------------
# Download helper — prefers wget (supports resume), falls back to curl
# ---------------------------------------------------------------------------
download() {
    local url="$1"
    local out="$2"

    if [ -f "$out" ]; then
        echo "  Already exists: $out"
        return 0
    fi

    echo "  Downloading: $url"
    echo "           -> $out"

    if command -v wget &>/dev/null; then
        wget -c -q --show-progress -O "$out" "$url"
    elif command -v curl &>/dev/null; then
        curl -C - -L -o "$out" "$url"
    else
        echo "ERROR: Neither wget nor curl found. Install one and retry."
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
for YEAR in 2019 2020 2021 2022 2023; do
    echo ""
    echo "=========================================="
    echo "Year: $YEAR"
    echo "=========================================="

    mkdir -p "$DEST/$YEAR"

    if [[ $YEAR -le 2020 ]]; then
        # Prototype format (2019-2020)
        PREFIX="$BASE/$YEAR/prototype"

        download "$PREFIX/5YRData/5YRData.zip" "$DEST/$YEAR/5YRData.zip"
        download "$PREFIX/Geos${YEAR}5YR.csv" "$DEST/$YEAR/geos.csv"
        download "$PREFIX/ACS${YEAR}_Table_Shells.csv" "$DEST/$YEAR/shells.csv"
    else
        # Table-based format (2021+)
        PREFIX="$BASE/$YEAR/table-based-SF"

        download "$PREFIX/data/5YRData/5YRData.zip" "$DEST/$YEAR/5YRData.zip"
        download "$PREFIX/documentation/Geos${YEAR}5YR.txt" "$DEST/$YEAR/geos.txt"
        download "$PREFIX/documentation/ACS${YEAR}5YR_Table_Shells.txt" "$DEST/$YEAR/shells.txt"
    fi

    # Unzip data files
    if [ -f "$DEST/$YEAR/5YRData.zip" ] && [ ! -d "$DEST/$YEAR/5YRData" ]; then
        echo "  Extracting 5YRData.zip..."
        unzip -o -q "$DEST/$YEAR/5YRData.zip" -d "$DEST/$YEAR/5YRData/"
        echo "  Done."
    elif [ -d "$DEST/$YEAR/5YRData" ]; then
        echo "  Already extracted: $DEST/$YEAR/5YRData/"
    fi
done

echo ""
echo "=========================================="
echo "All downloads complete."
echo "Files in: $DEST/"
echo "=========================================="
