#!/bin/bash
set -e
cd "$(dirname "$0")"

# Try tectonic first (used by the DoPE paper), fallback to pdflatex
if command -v tectonic &>/dev/null; then
    XDG_CACHE_HOME=/tmp/tectonic_cache tectonic neurips_2026_conference.tex
elif [ -f ../69d48542de10a633840335ca/tectonic-0.15.0-x86_64-unknown-linux-gnu.tar.gz ]; then
    # Extract bundled tectonic
    tar xzf /home/xiongjing/69d48542de10a633840335ca/tectonic-0.15.0-x86_64-unknown-linux-gnu.tar.gz -C /tmp/
    XDG_CACHE_HOME=/tmp/tectonic_cache /tmp/tectonic neurips_2026_conference.tex
else
    pdflatex -interaction=nonstopmode neurips_2026_conference.tex
    bibtex neurips_2026_conference || true
    pdflatex -interaction=nonstopmode neurips_2026_conference.tex
    pdflatex -interaction=nonstopmode neurips_2026_conference.tex
fi
echo "Done: neurips_2026_conference.pdf"
