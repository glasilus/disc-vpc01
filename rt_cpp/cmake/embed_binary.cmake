# Embeds an arbitrary binary file (e.g. AUDIO.png) as a C array header.
# Args: SRC, DST, NAME
# Output:
#   static const unsigned char k_<NAME>[] = { 0x.., 0x.., ... };
#   static const unsigned int  k_<NAME>_len = N;
cmake_minimum_required(VERSION 3.20)

file(READ "${SRC}" HEX_CONTENT HEX)
string(LENGTH "${HEX_CONTENT}" HEX_LEN)
math(EXPR BYTE_LEN "${HEX_LEN} / 2")

# Convert ABCDEF... → 0xAB,0xCD,0xEF,...
# CMake's regex is line-buffered so we do this in one pass.
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," BYTES "${HEX_CONTENT}")
# Drop the trailing comma left by the global replace.
string(REGEX REPLACE ",$" "" BYTES "${BYTES}")

file(WRITE "${DST}"
"#pragma once\n"
"// Auto-generated from ${SRC} — do not edit\n"
"static const unsigned char k_${NAME}[] = { ${BYTES} };\n"
"static const unsigned int  k_${NAME}_len = ${BYTE_LEN};\n"
)
