#!/usr/bin/env bash
#
# export_model.sh - Export a trained Piper checkpoint to ONNX format
#
# Usage: ./export_model.sh checkpoint.ckpt output_name
#
# Arguments:
#   checkpoint.ckpt - Path to the trained PyTorch Lightning checkpoint
#   output_name     - Base name for the output ONNX file (e.g., "rocky-medium")
#

set -euo pipefail

# --- Usage ---
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 checkpoint.ckpt output_name" >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  checkpoint.ckpt - Trained checkpoint file" >&2
    echo "  output_name     - Output name (e.g., 'rocky-medium')" >&2
    echo "" >&2
    echo "Example:" >&2
    echo "  $0 lightning_logs/version_0/checkpoints/epoch=2999.ckpt rocky-medium" >&2
    exit 1
fi

CHECKPOINT="$(realpath "$1")"
OUTPUT_NAME="$2"

# --- Validate ---
if [[ ! -f "$CHECKPOINT" ]]; then
    echo "Error: Checkpoint not found: $CHECKPOINT" >&2
    exit 1
fi

OUTPUT_ONNX="${OUTPUT_NAME}.onnx"

echo "=========================================="
echo "  Export Piper Model to ONNX"
echo "=========================================="
echo "Checkpoint: $CHECKPOINT"
echo "Output:     $OUTPUT_ONNX"
echo ""

# --- Export ---
echo "Exporting to ONNX..."
python3 -m piper_train.export_onnx \
    "$CHECKPOINT" \
    "$OUTPUT_ONNX"

echo ""

if [[ -f "$OUTPUT_ONNX" ]]; then
    echo "Export successful: $OUTPUT_ONNX"
    FILE_SIZE=$(du -h "$OUTPUT_ONNX" | cut -f1)
    echo "File size: $FILE_SIZE"
else
    echo "Error: Export failed, ONNX file not created." >&2
    exit 1
fi

echo ""
echo "=========================================="
echo "  Deployment to Raspberry Pi"
echo "=========================================="
echo ""
echo "1. Copy the model to your Pi:"
echo "   scp ${OUTPUT_ONNX} pi@<pi-address>:/path/to/models/"
echo ""
echo "2. Copy the config JSON (if generated alongside the ONNX):"
echo "   scp ${OUTPUT_NAME}.onnx.json pi@<pi-address>:/path/to/models/"
echo ""
echo "3. Update config.json on the Pi to point to the new model:"
echo "   \"piper_model\": \"/path/to/models/${OUTPUT_ONNX}\""
echo ""
echo "4. Test with:"
echo "   echo 'Good morning friend!' | piper --model /path/to/models/${OUTPUT_ONNX} --output_file test.wav"
echo ""
