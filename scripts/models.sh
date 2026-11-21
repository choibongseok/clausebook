#!/usr/bin/env bash
# 공개 가중치 모델을 .models/ 에 내려받는다. 이름·크기·라이선스는 README 의 '모델' 절에 있다.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .models/llm .models/embed
get() { [ -s "$2" ] || { echo "받는 중: $2"; curl -fL --retry 3 -o "$2" "$1"; }; }
get https://huggingface.co/intfloat/multilingual-e5-small/resolve/main/onnx/model_qint8_avx512_vnni.onnx .models/embed/model_qint8.onnx
get https://huggingface.co/intfloat/multilingual-e5-small/resolve/main/onnx/tokenizer.json .models/embed/tokenizer.json
get https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf .models/llm/qwen2.5-0.5b-instruct-q4_k_m.gguf
if [ "${WITH_1_5B:-0}" = 1 ]; then
  get https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf .models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf
fi
