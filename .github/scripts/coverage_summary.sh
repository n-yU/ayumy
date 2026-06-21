#!/usr/bin/env bash
set -euo pipefail

read_rate() {
  python3 -c "import sys, xml.etree.ElementTree as ET; r=ET.parse(sys.argv[1]).getroot(); print(f\"{float(r.get('line-rate'))*100:.1f}%\t{r.get('lines-covered')}\t{r.get('lines-valid')}\")" "$1"
}

py_xml=coverage-python.xml
sh_xml=$(find coverage-shell -name cobertura.xml -path '*bats*' 2>/dev/null | head -1 || true)

if [ -f "$py_xml" ]; then
  IFS=$'\t' read -r py_rate py_covered py_valid <<<"$(read_rate "$py_xml")"
else
  py_rate="-"; py_covered="-"; py_valid="-"
fi

if [ -n "${sh_xml:-}" ] && [ -f "$sh_xml" ]; then
  IFS=$'\t' read -r sh_rate sh_covered sh_valid <<<"$(read_rate "$sh_xml")"
else
  sh_rate="-"; sh_covered="-"; sh_valid="-"
fi

cat >> "$GITHUB_STEP_SUMMARY" <<MD
## Coverage summary

| Component | Coverage | Lines covered |
|---|---|---|
| Python (\`lambda/\`) | ${py_rate} | ${py_covered} / ${py_valid} |
| Shell (\`scripts\`, \`hooks\`, \`bin\`) | ${sh_rate} | ${sh_covered} / ${sh_valid} |
MD
