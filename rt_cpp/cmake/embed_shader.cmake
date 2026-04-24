# Embeds a GLSL .frag file as a C++ raw-string-literal header.
# Args: SRC, DST, NAME
# Output: static const char* k_<NAME>_frag = R"glsl(...)glsl";
cmake_minimum_required(VERSION 3.20)

file(READ "${SRC}" CONTENT)

# Safety: replace any occurrence of )glsl" in shader source (extremely unlikely
# in GLSL, but we guard it just in case)
string(REPLACE ")glsl\"" ")_glsl\"" CONTENT "${CONTENT}")

file(WRITE "${DST}"
"#pragma once\n"
"// Auto-generated from ${SRC} — do not edit\n"
"static const char* k_${NAME}_frag = R\"glsl(\n"
"${CONTENT}"
")glsl\";\n"
)
