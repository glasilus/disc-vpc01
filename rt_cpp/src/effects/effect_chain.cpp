#include "effect_chain.h"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <cstdlib>
#include <functional>

// Исходники шейдеров зашиты в бинарник - заголовки генерирует CMake
#include "passthrough_frag.h"
#include "canvas_place_frag.h"
#include "deriv_warp_frag.h"
#include "flash_frag.h"
#include "stutter_frag.h"
#include "pixel_sort_frag.h"
#include "ghost_trails_frag.h"
#include "scanlines_frag.h"
#include "bitcrush_frag.h"
#include "block_glitch_frag.h"
#include "negative_frag.h"
#include "color_bleed_frag.h"
#include "interlace_frag.h"
#include "bad_signal_frag.h"
#include "zoom_glitch_frag.h"
#include "mosaic_frag.h"
#include "phase_shift_frag.h"
#include "dither_frag.h"
#include "feedback_loop_frag.h"
#include "temporal_rgb_frag.h"
#include "chroma_key_frag.h"
#include "vortex_frag.h"
#include "fractal_noise_frag.h"
#include "self_disp_frag.h"
#include "ascii_frag.h"
// Классика, которую наконец подключили (шейдеры лежали, но не собирались)
#include "rgb_shift_frag.h"
#include "kali_mirror_frag.h"
#include "fisheye_frag.h"
#include "vhs_tracking_frag.h"
#include "pixel_drift_frag.h"
// Семейство datamosh-эффектов (временные, берут кадр из предыдущего прохода цепочки)
#include "pframe_lag_frag.h"
#include "mvec_bloom_frag.h"
#include "self_cannibalize_frag.h"
// Генеративные визуализаторы (написаны под фиксированный набор audio-uniform'ов)
#include "viz_plasma_frag.h"
#include "viz_radial_frag.h"
#include "viz_bars_frag.h"
#include "viz_alchemy_frag.h"

// ── таблица fx_key ────────────────────────────────────────────────────────────

// Единственный источник метаданных по эффектам. Порядок строк ДОЛЖЕН совпадать
// с enum FxId. "fx_derivwarp" заменил старый "fx_rgb" - в старых пресетах
// этого ключа просто нет.
// tip - короткое описание того, как эффект ВЫГЛЯДИТ (тултип в GUI при наведении),
// а не как он устроен внутри.
struct FxInfo { const char* key; const char* label; const char* group; const char* tip; };
static const FxInfo kFxInfo[(int)FxId::COUNT] = {
    { "fx_derivwarp",   "Deriv Warp",       "WARP",       "Picture flows and tears along its own motion, a liquid datamosh-style warp." },  // 0
    { "fx_flash",       "Flash",            "CORE",       "A hard white or black frame blinks over the video, like a camera flash." },  // 1
    { "fx_stutter",     "Stutter",          "CORE",       "The image judders in place, machine-gunning a frozen slice." },  // 2
    { "fx_pixel_sort",  "Pixel Sort",       "GLITCH",     "Bright pixels melt into long smooth colour streaks." },  // 3
    { "fx_ghost",       "Ghost Trails",     "CORE",       "Motion leaves a soft translucent echo trailing behind it." },  // 4
    { "fx_scanlines",   "Scanlines",        "DEGRADE",    "Thin dark horizontal lines lie over the picture, an old CRT look." },  // 5
    { "fx_bitcrush",    "Bitcrush",         "DEGRADE",    "Colour collapses into a few flat posterised bands." },  // 6
    { "fx_blockglitch", "Block Glitch",     "GLITCH",     "Rectangular chunks jump to the wrong place, like a corrupted stream." },  // 7
    { "fx_negative",    "Negative",         "COLOR",      "Colours flip to photographic negative, a jarring inverted blink." },  // 8
    { "fx_colorbleed",  "Color Bleed",      "COLOR",      "One colour channel smears sideways and bleeds off the picture (VHS)." },  // 9
    { "fx_interlace",   "Interlace",        "DEGRADE",    "Fast motion tears into a fine horizontal comb of interlaced teeth." },  // 10
    { "fx_badsignal",   "Bad Signal",       "GLITCH",     "Coloured noise bars flicker and rows jump, a dying broadcast." },  // 11
    { "fx_zoomglitch",  "Zoom Glitch",      "GLITCH",     "The frame gets yanked bigger on a hit, then springs elastically back." },  // 12
    { "fx_mosaic",      "Mosaic",           "GLITCH",     "The picture pixelates into chunky blocks that pump with the bass." },  // 13
    { "fx_phaseshift",  "Phase Shift",      "GLITCH",     "Horizontal bands slide opposite ways, shearing into offset ribbons." },  // 14
    { "fx_dither",      "Dither",           "DEGRADE",    "Smooth shading breaks into a fine stipple of dots, a 1-bit look." },  // 15
    { "fx_feedback",    "Feedback",         "WARP",       "Long glowing wash-trails smear the image into itself (video feedback)." },  // 16
    { "fx_temporalrgb", "Temporal RGB",     "COLOR",      "Colours lag behind motion, trailing red, green and blue ghosts." },  // 17
    { "fx_overlays",    "Overlays",         "OVERLAY",    "Composites overlay images from your folder on top of the canvas." },  // 18
    { "fx_vortex",      "Vortex",           "WARP",       "Pixels swirl around the centre into a spiral whirlpool." },  // 19
    { "fx_fractalnoise","Fractal Noise",    "WARP",       "The image ripples through an organic, ever-shifting noise field." },  // 20
    { "fx_selfdisp",    "Self Displace",    "WARP",       "The image warps by its own colours, a flowing self-eating distortion." },  // 21
    { "fx_ascii",       "ASCII",            "DEGRADE",    "The frame is rebuilt out of text characters, a terminal render." },  // 22
    { "fx_rgbshift",    "RGB Shift",        "COLOR",      "Colours split into red and blue fringes, a 3D-glasses glitch." },  // 23
    { "fx_kali",        "Kaleidoscope",     "WARP",       "The frame folds into a mirror-symmetric kaleidoscope mandala." },  // 24
    { "fx_fisheye",     "Fisheye",          "WARP",       "The image bulges outward through a rounded fisheye lens." },  // 25
    { "fx_vhstrack",    "VHS Tracking",     "DEGRADE",    "The picture tears into shifted bands with a rolling strip of hiss." },  // 26
    { "fx_pixeldrift",  "Pixel Drift",      "GLITCH",     "Rows slide sideways so the image ripples like water." },  // 27
    { "fx_pframe_lag",  "P-Frame Lag",      "DATAMOSH",   "Static blocks freeze, so movement smears the background into a stuck mosaic." },  // 28
    { "fx_mvec_bloom",  "MVec Bloom",       "DATAMOSH",   "Blocks drag along fake motion vectors into swimming, blooming streaks." },  // 29
    { "fx_self_cannibalize","Self Cannibalize","DATAMOSH","The image warps by its own content, flowing and eating itself." }, // 30
    { "fx_viz_plasma",  "Plasma",           "VISUALIZER", "Liquid demoscene plasma colour field driven by the audio." },  // 31
    { "fx_viz_radial",  "Radial Spectrum",  "VISUALIZER", "A polar audio-sun: spectrum petals radiating from a pulsing core." },  // 32
    { "fx_viz_bars",    "Spectrum Bars",    "VISUALIZER", "Glowing 16-band spectrum bars with a mirrored reflection." },  // 33
    { "fx_viz_alchemy", "Alchemy",          "VISUALIZER", "A kaleidoscopic glowing mandala that pulses with the music." },  // 34
};

const char* fx_key  (FxId id) { return kFxInfo[(int)id].key;   }
const char* fx_label(FxId id) { return kFxInfo[(int)id].label; }
const char* fx_group(FxId id) { return kFxInfo[(int)id].group; }
const char* fx_tip  (FxId id) { return kFxInfo[(int)id].tip;   }

const char* const kFxGroupOrder[] = {
    "CORE", "GLITCH", "WARP", "DATAMOSH", "COLOR", "DEGRADE", "VISUALIZER", "OVERLAY",
};
const int kFxGroupOrderCount = (int)(sizeof(kFxGroupOrder) / sizeof(kFxGroupOrder[0]));

// ── Порядок клавиш / отображения ────────────────────────────────────────────
// Строится один раз из kFxGroupOrder: для каждой группы по порядку добавляются
// все её эффекты (в порядке enum); эффект без известной группы уходит в конец,
// чтобы не стать недостижимым. Это тот же порядок, в котором GUI рисует список
// эффектов, поэтому индекс слота == позиция на экране, и каждая банка клавиш
// Q..P подсвечивает непрерывный диапазон.
struct FxKeyOrder {
    int slot_to_id[(int)FxId::COUNT];
    int id_to_slot[(int)FxId::COUNT];
    FxKeyOrder() {
        int n = 0;
        for (int g = 0; g < kFxGroupOrderCount; ++g)
            for (int i = 0; i < (int)FxId::COUNT; ++i)
                if (std::strcmp(fx_group((FxId)i), kFxGroupOrder[g]) == 0)
                    slot_to_id[n++] = i;
        // Подстраховка: любой эффект без группы добавляется в конец.
        for (int i = 0; i < (int)FxId::COUNT; ++i) {
            bool seen = false;
            for (int k = 0; k < n; ++k) if (slot_to_id[k] == i) { seen = true; break; }
            if (!seen) slot_to_id[n++] = i;
        }
        for (int k = 0; k < (int)FxId::COUNT; ++k) id_to_slot[slot_to_id[k]] = k;
    }
};
static const FxKeyOrder kKeyOrder;

int fx_slot_to_id(int slot) {
    if (slot < 0 || slot >= (int)FxId::COUNT) return -1;
    return kKeyOrder.slot_to_id[slot];
}
int fx_id_to_slot(int id) {
    if (id < 0 || id >= (int)FxId::COUNT) return -1;
    return kKeyOrder.id_to_slot[id];
}

// ── FboPair ──────────────────────────────────────────────────────────────────

void FboPair::create(int w, int h) {
    width = w; height = h;
    glGenFramebuffers(2, fbo);
    glGenTextures(2, tex);
    for (int i = 0; i < 2; ++i) {
        glBindTexture(GL_TEXTURE_2D, tex[i]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo[i]);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex[i], 0);
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glBindTexture(GL_TEXTURE_2D, 0);
}

void FboPair::destroy() {
    if (fbo[0]) { glDeleteFramebuffers(2, fbo); fbo[0] = fbo[1] = 0; }
    if (tex[0]) { glDeleteTextures(2, tex);     tex[0] = tex[1] = 0; }
    width = height = 0;
}

// ── EffectChain ──────────────────────────────────────────────────────────────

static GLuint compile_shader_src(GLenum type, const char* src) {
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, nullptr);
    glCompileShader(s);
    GLint ok = 0;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024]; glGetShaderInfoLog(s, sizeof(log), nullptr, log);
        fprintf(stderr, "[shader] compile error:\n%s\n", log);
    }
    return s;
}

static const char* k_vert =
    "#version 330 core\n"
    "layout(location=0) in vec2 aPos;\n"
    "layout(location=1) in vec2 aUV;\n"
    "out vec2 vUV;\n"
    "void main(){ vUV=aUV; gl_Position=vec4(aPos,0.0,1.0); }\n";

GLuint EffectChain::compile_program(const char* vert, const char* frag) {
    GLuint v = compile_shader_src(GL_VERTEX_SHADER,   vert);
    GLuint f = compile_shader_src(GL_FRAGMENT_SHADER, frag);
    GLuint p = glCreateProgram();
    glAttachShader(p, v); glAttachShader(p, f);
    glLinkProgram(p);
    GLint ok = 0; glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[512]; glGetProgramInfoLog(p, sizeof(log), nullptr, log);
        fprintf(stderr, "[shader] link error: %s\n", log);
    }
    glDeleteShader(v); glDeleteShader(f);
    return p;
}

EffectChain::EffectChain()  = default;
EffectChain::~EffectChain() { destroy(); }

void EffectChain::setup_quad() {
    static const float verts[] = {
        -1.f,-1.f, 0.f,0.f,
         1.f,-1.f, 1.f,0.f,
        -1.f, 1.f, 0.f,1.f,
         1.f,-1.f, 1.f,0.f,
         1.f, 1.f, 1.f,1.f,
        -1.f, 1.f, 0.f,1.f,
    };
    glGenVertexArrays(1, &quad_vao_);
    glGenBuffers(1, &quad_vbo_);
    glBindVertexArray(quad_vao_);
    glBindBuffer(GL_ARRAY_BUFFER, quad_vbo_);
    glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STATIC_DRAW);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4*sizeof(float), (void*)0);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4*sizeof(float), (void*)(2*sizeof(float)));
    glEnableVertexAttribArray(1);
    glBindVertexArray(0);
}

// ASCII-символы от плотного к разреженному (16 уровней).
// Каждая запись - 8×8 битмап-шрифт, закодированный построчно.
// Самодельный минимальный шрифт: @#%=+-. (пробел) + ещё 8 символов плотности.
// 8 строк × 8 байт на символ, всего 16 символов, от DENSE (@) до SPARSE (пробел).
static const uint8_t kFontData[16][8][8] = {
    // 0: @ (very dense)
    {{0,0,0,0,0,0,0,0},{0,0,1,1,1,1,0,0},{0,1,1,0,0,1,1,0},{0,1,0,1,1,1,1,0},
     {0,1,0,1,0,1,1,0},{0,1,0,1,1,1,0,0},{0,1,1,0,0,0,0,0},{0,0,1,1,1,1,0,0}},
    // 1: #
    {{0,0,0,0,0,0,0,0},{0,1,0,1,0,1,0,0},{0,1,0,1,0,1,0,0},{1,1,1,1,1,1,1,0},
     {0,1,0,1,0,1,0,0},{1,1,1,1,1,1,1,0},{0,1,0,1,0,1,0,0},{0,0,0,0,0,0,0,0}},
    // 2: &
    {{0,0,1,1,0,0,0,0},{0,1,0,0,1,0,0,0},{0,1,0,0,1,0,0,0},{0,0,1,1,0,0,0,0},
     {0,1,0,1,0,1,0,0},{0,1,0,0,1,0,0,0},{0,1,0,0,1,1,0,0},{0,0,1,1,0,1,1,0}},
    // 3: %
    {{1,1,0,0,0,0,1,0},{1,1,0,0,0,1,0,0},{0,0,0,0,1,0,0,0},{0,0,0,1,0,0,0,0},
     {0,0,1,0,0,0,0,0},{0,1,0,0,0,1,1,0},{1,0,0,0,0,1,1,0},{0,0,0,0,0,0,0,0}},
    // 4: $
    {{0,0,1,0,0,0,0,0},{0,1,1,1,1,0,0,0},{1,0,1,0,0,0,0,0},{0,1,1,1,0,0,0,0},
     {0,0,1,0,1,0,0,0},{0,1,1,1,1,0,0,0},{0,0,1,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 5: *
    {{0,0,0,0,0,0,0,0},{0,0,1,0,1,0,0,0},{0,0,0,1,0,0,0,0},{0,1,1,1,1,1,0,0},
     {0,0,0,1,0,0,0,0},{0,0,1,0,1,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 6: o
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,1,1,1,0,0,0},{0,1,0,0,0,1,0,0},
     {0,1,0,0,0,1,0,0},{0,1,0,0,0,1,0,0},{0,0,1,1,1,0,0,0},{0,0,0,0,0,0,0,0}},
    // 7: =
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,1,1,1,1,1,0,0},{0,0,0,0,0,0,0,0},
     {0,1,1,1,1,1,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 8: +
    {{0,0,0,0,0,0,0,0},{0,0,0,1,0,0,0,0},{0,0,0,1,0,0,0,0},{0,1,1,1,1,1,0,0},
     {0,0,0,1,0,0,0,0},{0,0,0,1,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 9: -
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,1,1,1,1,1,0,0},
     {0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 10: ~
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,1,0,0,1,0,0,0},{1,0,1,0,0,1,0,0},
     {0,0,0,1,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 11: :
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,1,1,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 12: .
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 13: '
    {{0,0,0,1,1,0,0,0},{0,0,0,1,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 14: `
    {{0,0,1,0,0,0,0,0},{0,0,0,1,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 15: (space) - completely empty
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
};

void EffectChain::create_ascii_font_tex() {
    // Собираем текстуру 128×8 R8: 16 символов по 8px шириной, 8 строк высотой
    const int CHARS = 16, CHAR_W = 8, CHAR_H = 8;
    const int W = CHARS * CHAR_W, H = CHAR_H;
    uint8_t pixels[H][W] = {};

    for (int c = 0; c < CHARS; c++) {
        for (int row = 0; row < CHAR_H; row++) {
            for (int col = 0; col < CHAR_W; col++) {
                pixels[row][c * CHAR_W + col] =
                    kFontData[c][row][col] ? 255 : 0;
            }
        }
    }

    glGenTextures(1, &ascii_font_tex_);
    glBindTexture(GL_TEXTURE_2D, ascii_font_tex_);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, W, H, 0, GL_RED, GL_UNSIGNED_BYTE, pixels);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glBindTexture(GL_TEXTURE_2D, 0);
}

bool EffectChain::init(int w, int h) {
    setup_quad();
    main_fbo_.create(w, h);
    accum_fbo_.create(w, h);

    // Dry-буфер: один FBO+текстура размером с канвас. Перед запуском любых
    // эффектов в него блитится уже размещённый на канвасе кадр, чтобы потом
    // взять его в финальном dry/wet-миксе по master_intensity.
    glGenTextures(1, &dry_tex_);
    glBindTexture(GL_TEXTURE_2D, dry_tex_);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glGenFramebuffers(1, &dry_fbo_);
    glBindFramebuffer(GL_FRAMEBUFFER, dry_fbo_);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, dry_tex_, 0);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glBindTexture(GL_TEXTURE_2D, 0);

    // Заранее выделяем текстуры и FBO для истории кадров
    glGenTextures(kHistoryLen, hist_tex_);
    glGenFramebuffers(kHistoryLen, hist_fbo_);
    for (int i = 0; i < kHistoryLen; ++i) {
        glBindTexture(GL_TEXTURE_2D, hist_tex_[i]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glBindFramebuffer(GL_FRAMEBUFFER, hist_fbo_[i]);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, hist_tex_[i], 0);
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glBindTexture(GL_TEXTURE_2D, 0);

    create_ascii_font_tex();

    // Компилируем все программы
    prog_pass_        = compile_program(k_vert, k_passthrough_frag);
    prog_place_       = compile_program(k_vert, k_canvas_place_frag);

    // Inline-шейдер dry/wet микса: лерп двух текстур по uMix.
    // Если uGatingMode != 0, дополнительно гейтится хромакеем по uDry.
    static const char* k_mix_frag =
        "#version 330 core\n"
        "in vec2 vUV; out vec4 fragColor;\n"
        "uniform sampler2D uWet;\n"
        "uniform sampler2D uDry;\n"
        "uniform float uMix;\n"
        "uniform int uGatingMode;\n" // 0=none, 1=Foreground, 2=Background
        "uniform vec3 uKeyColor;\n"
        "uniform float uTolerance;\n"
        "uniform float uSoftness;\n"
        "\n"
        "vec3 rgb2hsv(vec3 c) {\n"
        "    vec4 K = vec4(0.0, -1.0/3.0, 2.0/3.0, -1.0);\n"
        "    vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));\n"
        "    vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));\n"
        "    float d = q.x - min(q.w, q.y);\n"
        "    float e = 1.0e-10;\n"
        "    return vec3(abs(q.z + (q.w - q.y)/(6.0*d + e)), d/(q.x + e), q.x);\n"
        "}\n"
        "\n"
        "void main(){\n"
        "  vec3 w = texture(uWet, vUV).rgb;\n"
        "  vec3 d = texture(uDry, vUV).rgb;\n"
        "  float blend = clamp(uMix, 0.0, 1.0);\n"
        "  if (uGatingMode != 0) {\n"
        "    vec3 key_hsv = rgb2hsv(uKeyColor);\n"
        "    vec3 dry_hsv = rgb2hsv(d);\n"
        "    float hue_diff = abs(dry_hsv.x - key_hsv.x);\n"
        "    hue_diff = min(hue_diff, 1.0 - hue_diff);\n"
        "    float alpha = smoothstep(uTolerance - uSoftness, uTolerance, hue_diff);\n"
        "    if (uGatingMode == 1) {\n"
        "      blend *= alpha;\n"
        "    } else {\n"
        "      blend *= (1.0 - alpha);\n"
        "    }\n"
        "  }\n"
        "  fragColor = vec4(mix(d, w, blend), 1.0);\n"
        "}\n";
    prog_mix_ = compile_program(k_vert, k_mix_frag);
    prog_derivwarp_   = compile_program(k_vert, k_deriv_warp_frag);
    prog_flash_       = compile_program(k_vert, k_flash_frag);
    prog_stutter_     = compile_program(k_vert, k_stutter_frag);
    prog_pixsort_     = compile_program(k_vert, k_pixel_sort_frag);
    prog_ghost_       = compile_program(k_vert, k_ghost_trails_frag);
    prog_scanlines_   = compile_program(k_vert, k_scanlines_frag);
    prog_bitcrush_    = compile_program(k_vert, k_bitcrush_frag);
    prog_blockglitch_ = compile_program(k_vert, k_block_glitch_frag);
    prog_negative_    = compile_program(k_vert, k_negative_frag);
    prog_colorbleed_  = compile_program(k_vert, k_color_bleed_frag);
    prog_interlace_   = compile_program(k_vert, k_interlace_frag);
    prog_badsignal_   = compile_program(k_vert, k_bad_signal_frag);
    prog_zoomglitch_  = compile_program(k_vert, k_zoom_glitch_frag);
    prog_mosaic_      = compile_program(k_vert, k_mosaic_frag);
    prog_phaseshift_  = compile_program(k_vert, k_phase_shift_frag);
    prog_dither_      = compile_program(k_vert, k_dither_frag);
    prog_feedback_    = compile_program(k_vert, k_feedback_loop_frag);
    prog_temporalrgb_ = compile_program(k_vert, k_temporal_rgb_frag);
    prog_overlay_     = compile_program(k_vert, k_chroma_key_frag);
    prog_vortex_      = compile_program(k_vert, k_vortex_frag);
    prog_fractalnoise_= compile_program(k_vert, k_fractal_noise_frag);
    prog_selfdisp_    = compile_program(k_vert, k_self_disp_frag);
    prog_ascii_       = compile_program(k_vert, k_ascii_frag);
    prog_rgbshift_    = compile_program(k_vert, k_rgb_shift_frag);
    prog_kali_        = compile_program(k_vert, k_kali_mirror_frag);
    prog_fisheye_     = compile_program(k_vert, k_fisheye_frag);
    prog_vhstrack_    = compile_program(k_vert, k_vhs_tracking_frag);
    prog_pixeldrift_  = compile_program(k_vert, k_pixel_drift_frag);
    prog_pframe_lag_  = compile_program(k_vert, k_pframe_lag_frag);
    prog_mvec_bloom_  = compile_program(k_vert, k_mvec_bloom_frag);
    prog_self_cannib_ = compile_program(k_vert, k_self_cannibalize_frag);
    prog_viz_plasma_  = compile_program(k_vert, k_viz_plasma_frag);
    prog_viz_radial_  = compile_program(k_vert, k_viz_radial_frag);
    prog_viz_bars_    = compile_program(k_vert, k_viz_bars_frag);
    prog_viz_alchemy_ = compile_program(k_vert, k_viz_alchemy_frag);

    return true;
}

void EffectChain::resize(int w, int h) {
    main_fbo_.destroy();  main_fbo_.create(w, h);
    accum_fbo_.destroy(); accum_fbo_.create(w, h);
    // Меняем размер текстур истории
    for (int i = 0; i < kHistoryLen; ++i) {
        glBindTexture(GL_TEXTURE_2D, hist_tex_[i]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
    }
    if (dry_tex_) {
        glBindTexture(GL_TEXTURE_2D, dry_tex_);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
    }
    glBindTexture(GL_TEXTURE_2D, 0);
    hist_idx_  = 0;
    hist_full_ = false;
}

void EffectChain::destroy() {
    main_fbo_.destroy();
    accum_fbo_.destroy();
    if (hist_tex_[0]) { glDeleteTextures(kHistoryLen, hist_tex_);    std::memset(hist_tex_, 0, sizeof(hist_tex_)); }
    if (hist_fbo_[0]) { glDeleteFramebuffers(kHistoryLen, hist_fbo_);std::memset(hist_fbo_, 0, sizeof(hist_fbo_)); }
    if (dry_fbo_) { glDeleteFramebuffers(1, &dry_fbo_); dry_fbo_ = 0; }
    if (dry_tex_) { glDeleteTextures(1, &dry_tex_);     dry_tex_ = 0; }
    if (ascii_font_tex_) { glDeleteTextures(1, &ascii_font_tex_); ascii_font_tex_ = 0; }

    auto del = [](GLuint& p){ if(p){ glDeleteProgram(p); p=0; } };
    del(prog_pass_); del(prog_place_); del(prog_mix_); del(prog_derivwarp_); del(prog_flash_);
    del(prog_stutter_); del(prog_pixsort_); del(prog_ghost_);
    del(prog_scanlines_); del(prog_bitcrush_); del(prog_blockglitch_);
    del(prog_negative_); del(prog_colorbleed_); del(prog_interlace_);
    del(prog_badsignal_); del(prog_zoomglitch_); del(prog_mosaic_);
    del(prog_phaseshift_); del(prog_dither_); del(prog_feedback_);
    del(prog_temporalrgb_); del(prog_overlay_);
    del(prog_vortex_); del(prog_fractalnoise_); del(prog_selfdisp_);
    del(prog_ascii_);
    del(prog_rgbshift_); del(prog_kali_); del(prog_fisheye_);
    del(prog_vhstrack_); del(prog_pixeldrift_);
    del(prog_pframe_lag_); del(prog_mvec_bloom_); del(prog_self_cannib_);
    del(prog_viz_plasma_); del(prog_viz_radial_);
    del(prog_viz_bars_); del(prog_viz_alchemy_);

    if (quad_vao_) { glDeleteVertexArrays(1, &quad_vao_); quad_vao_ = 0; }
    if (quad_vbo_) { glDeleteBuffers(1, &quad_vbo_);      quad_vbo_ = 0; }
}

// ── Управление историей кадров ─────────────────────────────────────────────

void EffectChain::push_history() {
    // Копия на GPU: блитим main_fbo_.read_tex() в hist_fbo_[hist_idx_] через
    // glBlitFramebuffer - быстро, без читбэка пикселей на CPU.
    int w = main_fbo_.width, h = main_fbo_.height;

    // Нужен исходный FBO. Ping-pong FBO принадлежат main_fbo_,
    // текущая сторона для чтения - main_fbo_.fbo[main_fbo_.current].
    GLuint src_fbo = main_fbo_.fbo[main_fbo_.current];
    GLuint dst_fbo = hist_fbo_[hist_idx_];

    glBindFramebuffer(GL_READ_FRAMEBUFFER, src_fbo);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, dst_fbo);
    glBlitFramebuffer(0,0,w,h, 0,0,w,h, GL_COLOR_BUFFER_BIT, GL_NEAREST);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);

    hist_idx_ = (hist_idx_ + 1) % kHistoryLen;
    if (!hist_full_ && hist_idx_ == 0) hist_full_ = true;
}

GLuint EffectChain::history_tex(int age) const {
    // age 0 = самый свежий кадр, age 1 = на кадр старше, и т.д.
    if (!hist_full_ && age >= hist_idx_) return main_fbo_.read_tex(); // ещё не накопили историю
    int slot = (hist_idx_ - 1 - age + kHistoryLen * 2) % kHistoryLen;
    return hist_tex_[slot];
}

// ── Хелпер для шейдерного прохода ────────────────────────────────────────────

void EffectChain::pass(GLuint prog, GLuint src_tex,
                       const std::function<void(GLuint)>& set_uniforms) {
    glBindFramebuffer(GL_FRAMEBUFFER, main_fbo_.write_fbo());
    glViewport(0, 0, main_fbo_.width, main_fbo_.height);
    glUseProgram(prog);

    // Привязываем src как texture unit 0 (uTex)
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, src_tex);
    glUniform1i(glGetUniformLocation(prog, "uTex"), 0);

    set_uniforms(prog);

    glBindVertexArray(quad_vao_);
    glDrawArrays(GL_TRIANGLES, 0, 6);
    glBindVertexArray(0);
    main_fbo_.swap();
}

// Короткие обёртки для установки uniform-ов
static inline void u1f(GLuint p, const char* n, float v)           { glUniform1f(glGetUniformLocation(p,n),v); }
static inline void u1i(GLuint p, const char* n, int   v)           { glUniform1i(glGetUniformLocation(p,n),v); }
static inline void u2f(GLuint p, const char* n, float a, float b)  { glUniform2f(glGetUniformLocation(p,n),a,b); }
static inline void u3f(GLuint p, const char* n, float a, float b, float c) { glUniform3f(glGetUniformLocation(p,n),a,b,c); }

static inline void bind_tex(GLuint prog, int unit, GLuint tex, const char* name) {
    glActiveTexture(GL_TEXTURE0 + unit);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(glGetUniformLocation(prog, name), unit);
}

static bool fires(float chance) {
    return ((float)rand() / (float)RAND_MAX) < chance;
}

// Постоянная времени затухания огибающей для каждого эффекта (в секундах).
// Резкие удары (flash) гаснут быстро; смазы/feedback тянутся дольше.
// Всё остальное - на музыкальном хвосте ~180 мс.
static float fx_decay_tau(FxId id) {
    switch (id) {
        case FxId::FLASH:       return 0.05f;
        case FxId::STUTTER:     return 0.08f;
        case FxId::BLOCKGLITCH: return 0.10f;
        case FxId::BADSIGNAL:   return 0.10f;
        case FxId::GHOST:       return 0.30f;
        case FxId::TEMPORALRGB: return 0.25f;
        case FxId::FEEDBACK:    return 0.45f;
        // Datamosh накапливается от кадра к кадру - даём ему тянуться дольше,
        // чтобы "плавление" успевало нарастать.
        case FxId::PFRAME_LAG:
        case FxId::MVEC_BLOOM:
        case FxId::SELF_CANNIBALIZE: return 0.40f;
        default:                return 0.18f;
    }
}

// ── Модель огибающей ─────────────────────────────────────────────────────────
// Заменяет старую покадровую схему Бернулли (из-за неё эффекты стробили и
// зависели от frame rate). У каждого включённого эффекта есть огибающая 0..1:
//   Beat/Auto  - атака до trig_level по музыкальному событию (с шансом chance),
//                затем экспоненциальный спад - плавный удар, а не мерцание.
//   Sustained  - непрерывно следит за громкостью звука (без строба).
//   Manual     - всегда включён на максимум (VJ держит вручную), звук игнорирует.
void EffectChain::update_envelopes(const Segment& seg, const AudioStats& stats,
                                   float chaos, float dt, EffectParams params[]) {
    bool beat_edge = stats.beat && !prev_beat_;
    prev_beat_ = stats.beat;
    bool seg_changed = ((int)seg.type != prev_seg_);
    prev_seg_ = (int)seg.type;
    bool accent = beat_edge ||
                  (seg_changed && (seg.type == SegmentType::IMPACT ||
                                   seg.type == SegmentType::DROP  ||
                                   seg.type == SegmentType::BUILD));
    float seg_env    = std::sqrt(std::clamp(seg.intensity, 0.f, 1.f));
    float trig_level = std::clamp(0.5f + 0.5f * chaos, 0.f, 1.f);
    dt = std::clamp(dt, 0.f, 0.1f);
    float attack = 1.f - std::exp(-dt / 0.04f);   // сглаживание ~40 мс

    for (int i = 0; i < (int)FxId::COUNT; ++i) {
        EffectParams& p = params[i];
        if (!p.enabled) { env_[i] = 0.f; continue; }
        float decay = std::exp(-dt / std::max(0.01f, fx_decay_tau((FxId)i)));
        switch ((TriggerMode)p.mode) {
            case TriggerMode::Manual:
                env_[i] += (1.f - env_[i]) * attack; break;
            case TriggerMode::Sustained:
                env_[i] += (seg_env * trig_level - env_[i]) * attack; break;
            case TriggerMode::Beat:
                if (beat_edge && fires(p.chance)) env_[i] = trig_level;
                else                              env_[i] *= decay;
                break;
            case TriggerMode::Auto:
            default:
                if (accent && fires(p.chance)) env_[i] = trig_level;
                else                           env_[i] *= decay;
                break;
        }
        if (env_[i] < 1e-4f) env_[i] = 0.f;
    }
}

// Только эти эффекты читают кольцо истории кадров; если ни один не включён,
// покадровый блит истории вообще пропускаем (иначе это копия всего канваса
// каждый кадр впустую).
bool EffectChain::needs_history(EffectParams params[]) const {
    static const FxId consumers[] = {
        FxId::GHOST, FxId::STUTTER, FxId::INTERLACE,
        FxId::TEMPORALRGB, FxId::DERIVWARP, FxId::SELFDISP,
        FxId::PFRAME_LAG, FxId::MVEC_BLOOM, FxId::SELF_CANNIBALIZE,
    };
    for (FxId id : consumers) if (params[(int)id].enabled) return true;
    return false;
}

// ── Main apply ────────────────────────────────────────────────────────────────

GLuint EffectChain::apply(
    GLuint              input_tex,
    int                 src_w, int src_h,
    AspectMode          aspect,
    GLuint              overlay_tex,
    float               overlay_x, float overlay_y,
    float               overlay_w, float overlay_h,
    const ChromaKeyParams& chroma,
    float               overlay_alpha,
    const Segment&      seg,
    const AudioStats&   stats,
    float               chaos,
    float               master_intensity,
    float               time_sec,
    float               dt,
    EffectParams        params[(int)FxId::COUNT])
{
    const int W = main_fbo_.width, H = main_fbo_.height;
    constexpr float kEps = 0.004f;

    // Продвигаем аудио-огибающую каждого эффекта на этот кадр. После этого
    // strength(id) = env_[id] * intensity дает плавную величину применения 0..1.
    update_envelopes(seg, stats, chaos, dt, params);
    auto strength = [&](FxId id) -> float {
        return std::clamp(env_[(int)id] * params[(int)id].intensity, 0.f, 1.f);
    };

    // Нормализованные аудио-агрегаты для генеративных визуализаторов (0..1).
    float vbass = 0.f, vmid = 0.f, vtreb = 0.f;
    for (int i = 0; i < 4;  ++i) vbass += stats.bins[i];       vbass *= 0.25f;
    for (int i = 4; i < 10; ++i) vmid  += stats.bins[i];       vmid  /= 6.f;
    for (int i = 10;i < 16; ++i) vtreb += stats.bins[i];       vtreb /= 6.f;
    float vbeat = stats.beat ? 1.f : 0.f;
    auto viz_pass = [&](GLuint prog, float fi){
        pass(prog, main_fbo_.read_tex(), [&](GLuint p){
            u2f(p,"uResolution",(float)W,(float)H);
            u1f(p,"uTime",      time_sec);
            u1f(p,"uIntensity", fi);
            u1f(p,"uBass",      vbass);
            u1f(p,"uMid",       vmid);
            u1f(p,"uTreble",    vtreb);
            u1f(p,"uLevel",     stats.level);
            u1f(p,"uBeat",      vbeat);
            glUniform1fv(glGetUniformLocation(p,"uBins"), kVizBins, stats.bins);
        });
    };

    // Размещаем входное изображение на канвасе с учётом aspect ratio. Если
    // размеры ещё не готовы (в этот тик кадр не декодирован) или шейдер
    // размещения не скомпилировался - откатываемся к простому блиту.
    if (src_w > 0 && src_h > 0 && input_tex != 0 && prog_place_ != 0) {
        pass(prog_place_, input_tex, [&](GLuint p){
            glUniform2f(glGetUniformLocation(p, "uSrcSize"),    (float)src_w, (float)src_h);
            glUniform2f(glGetUniformLocation(p, "uCanvasSize"), (float)W,     (float)H);
            glUniform1i(glGetUniformLocation(p, "uMode"),       (int)aspect);
        });
    } else {
        pass(prog_pass_, input_tex, [](GLuint){});
    }

    // Сохраняем размещённый на канвасе кадр как "dry"-референс для финального
    // микса по master_intensity. glBlitFramebuffer корректно работает для
    // блитов одинакового размера и не требует лишнего fullscreen quad.
    if (dry_fbo_ != 0) {
        glBindFramebuffer(GL_READ_FRAMEBUFFER, main_fbo_.read_fbo());
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, dry_fbo_);
        glBlitFramebuffer(0, 0, W, H, 0, 0, W, H,
                          GL_COLOR_BUFFER_BIT, GL_NEAREST);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
    }

    // Берём ссылки на историю (безопасно - все текстуры выделены заранее)
    GLuint h0 = history_tex(0);  // кадр назад
    GLuint h1 = history_tex(1);  // 2 кадра назад
    GLuint h2 = history_tex(2);  // 3 кадра назад
    GLuint h3 = history_tex(3);  // 4 кадра назад

    // ── Временные эффекты / смазы ─────────────────────────────────────────────

    if (float fi = strength(FxId::GHOST); fi > kEps) {
        pass(prog_ghost_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi);
        });
    }

    if (float fi = strength(FxId::STUTTER); fi > kEps) {
        pass(prog_stutter_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi);
        });
    }

    if (float fi = strength(FxId::INTERLACE); fi > kEps) {
        pass(prog_interlace_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (float fi = strength(FxId::TEMPORALRGB); fi > kEps) {
        // Огибающая пикует на бите и затем спадает - пока она сильная, берём
        // более старые кадры истории для широкого расщепления каналов,
        // которое сужается обратно по мере затухания.
        bool strong = env_[(int)FxId::TEMPORALRGB] > 0.5f;
        GLuint tex_g = strong ? h2 : h0;
        GLuint tex_r = strong ? h3 : h1;
        pass(prog_temporalrgb_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, tex_g, "uPrev1");
            bind_tex(p, 2, tex_r, "uPrev2");
            u1f(p,"uIntensity", fi);
        });
    }

    // ── Искажения в духе datamosh ─────────────────────────────────────────────

    if (float fi = strength(FxId::DERIVWARP); fi > kEps) {
        pass(prog_derivwarp_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (float fi = strength(FxId::SELFDISP); fi > kEps) {
        pass(prog_selfdisp_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            bind_tex(p, 2, h1, "uPrev2");
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
        });
    }

    if (float fi = strength(FxId::VORTEX); fi > kEps) {
        pass(prog_vortex_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
            u1f(p,"uBass",      stats.bass);
        });
    }

    if (float fi = strength(FxId::FRACTALNOISE); fi > kEps) {
        pass(prog_fractalnoise_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
            u1f(p,"uTreble",    stats.treble);
        });
    }

    // ── Аккумулятор feedback ──────────────────────────────────────────────────

    if (float fi = strength(FxId::FEEDBACK); fi > kEps) {
        GLuint cur   = main_fbo_.read_tex();
        GLuint prev_accum = accum_fbo_.read_tex();

        // Пишем новый аккумулятор = blend(cur, prev_accum)
        glBindFramebuffer(GL_FRAMEBUFFER, accum_fbo_.write_fbo());
        glViewport(0,0,W,H);
        glUseProgram(prog_feedback_);
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, cur);        glUniform1i(glGetUniformLocation(prog_feedback_,"uTex"),  0);
        glActiveTexture(GL_TEXTURE1); glBindTexture(GL_TEXTURE_2D, prev_accum); glUniform1i(glGetUniformLocation(prog_feedback_,"uAccum"),1);
        u1f(prog_feedback_,"uIntensity", fi);

        // Модулируем масштаб и поворот басами/мидами для эффекта аналогового видео-feedback
        float scale = 0.98f + stats.bass * 0.04f;   // пульсирующий zoom вокруг 1.0
        float rot = 0.005f + stats.mid * 0.03f;     // вращение, завязанное на средние частоты
        u1f(prog_feedback_,"uFeedbackScale", scale);
        u1f(prog_feedback_,"uFeedbackRotation", rot);

        glBindVertexArray(quad_vao_); glDrawArrays(GL_TRIANGLES,0,6); glBindVertexArray(0);
        accum_fbo_.swap();

        pass(prog_pass_, accum_fbo_.read_tex(), [](GLuint){});
    }

    // ── Канальные / цветовые эффекты ──────────────────────────────────────────

    if (float fi = strength(FxId::COLORBLEED); fi > kEps) {
        pass(prog_colorbleed_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (float fi = strength(FxId::BLOCKGLITCH); fi > kEps) {
        pass(prog_blockglitch_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
        });
    }

    if (float fi = strength(FxId::BADSIGNAL); fi > kEps) {
        pass(prog_badsignal_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (float fi = strength(FxId::PHASESHIFT); fi > kEps) {
        pass(prog_phaseshift_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
        });
    }

    if (float fi = strength(FxId::PIXEL_SORT); fi > kEps) {
        pass(prog_pixsort_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u2f(p,"uResolution",(float)W,(float)H);
            // Направление сортировки зависит от типа сегмента:
            // Noise -> горизонталь, Impact/Drop -> вертикаль
            if (seg.type == SegmentType::NOISE) {
                u2f(p,"uSortDir", 1.0f, 0.0f);
            } else {
                u2f(p,"uSortDir", 0.0f, 1.0f);
            }
        });
    }

    if (float fi = strength(FxId::ZOOMGLITCH); fi > kEps) {
        pass(prog_zoomglitch_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
        });
    }

    if (float fi = strength(FxId::MOSAIC); fi > kEps) {
        pass(prog_mosaic_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
        });
    }

    if (float fi = strength(FxId::NEGATIVE); fi > kEps) {
        pass(prog_negative_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
        });
    }

    if (float fi = strength(FxId::SCANLINES); fi > kEps) {
        pass(prog_scanlines_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (float fi = strength(FxId::BITCRUSH); fi > kEps) {
        pass(prog_bitcrush_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
        });
    }

    if (float fi = strength(FxId::DITHER); fi > kEps) {
        pass(prog_dither_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    // ── Штатная классика (калейдоскоп / rgb split / fisheye / vhs / drift) ────

    if (float fi = strength(FxId::RGBSHIFT); fi > kEps) {
        pass(prog_rgbshift_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
        });
    }

    if (float fi = strength(FxId::KALI); fi > kEps) {
        pass(prog_kali_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
        });
    }

    if (float fi = strength(FxId::FISHEYE); fi > kEps) {
        pass(prog_fisheye_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
        });
    }

    if (float fi = strength(FxId::VHSTRACK); fi > kEps) {
        pass(prog_vhstrack_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
        });
    }

    if (float fi = strength(FxId::PIXELDRIFT); fi > kEps) {
        pass(prog_pixeldrift_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
        });
    }

    // ── Семейство datamosh (питаются предыдущим выходом цепочки через историю) ──

    if (float fi = strength(FxId::PFRAME_LAG); fi > kEps) {
        pass(prog_pframe_lag_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (float fi = strength(FxId::MVEC_BLOOM); fi > kEps) {
        pass(prog_mvec_bloom_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (float fi = strength(FxId::SELF_CANNIBALIZE); fi > kEps) {
        pass(prog_self_cannib_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            bind_tex(p, 2, h1, "uPrev2");
            u1f(p,"uIntensity", fi);
            u1f(p,"uTime",      time_sec);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    // ── Flash (вспышка бело/чёрным) ───────────────────────────────────────────

    if (float fi = strength(FxId::FLASH); fi > kEps) {
        float white = (rand() % 2) ? 1.f : 0.f;
        pass(prog_flash_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi);
            u1f(p,"uWhite",     white);
        });
    }

    // ── ASCII (визуальная трансформация - идёт после всех глитчей) ───────────

    if (float fi = strength(FxId::ASCII); fi > kEps) {
        pass(prog_ascii_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, ascii_font_tex_, "uFontAtlas");
            u2f(p,"uResolution",(float)W,(float)H);
            u1f(p,"uIntensity", fi);
            u1f(p,"uColor",     1.0f);  // сохраняем исходные цвета
        });
    }

    // ── Генеративные визуализаторы (рисуют картинку ИЗ звука поверх канваса) ──

    if (float fi = strength(FxId::VIZ_PLASMA);  fi > kEps) viz_pass(prog_viz_plasma_,  fi);
    if (float fi = strength(FxId::VIZ_RADIAL);  fi > kEps) viz_pass(prog_viz_radial_,  fi);
    if (float fi = strength(FxId::VIZ_BARS);    fi > kEps) viz_pass(prog_viz_bars_,    fi);
    if (float fi = strength(FxId::VIZ_ALCHEMY); fi > kEps) viz_pass(prog_viz_alchemy_, fi);

    // ── Композитинг оверлея ────────────────────────────────────────────────────

    if (params[(int)FxId::OVERLAYS].enabled && overlay_tex && overlay_alpha > 0.01f) {
        pass(prog_overlay_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, overlay_tex, "uOverlay");
            u2f(p,"uOverlayPos",  overlay_x, overlay_y);
            u2f(p,"uOverlaySize", overlay_w, overlay_h);
            u1f(p,"uTolerance",   chroma.tolerance / 180.f);
            u1f(p,"uSoftness",    chroma.softness  / 180.f);
            u3f(p,"uKeyColor",    chroma.r/255.f, chroma.g/255.f, chroma.b/255.f);
            u1i(p,"uMode",        (int)chroma.mode);
            u1f(p,"uOverlayAlpha",overlay_alpha);
        });
    }

    // ── Микс по master intensity (dry/wet) ────────────────────────────────────
    // master_intensity = 1 -> полностью с эффектами; 0 -> исходный кадр.
    bool run_mix = (master_intensity < 0.999f) || (chroma.gate_fx && chroma.mode != ChromaMode::None);
    if (run_mix && prog_mix_ != 0 && dry_tex_ != 0) {
        GLuint wet = main_fbo_.read_tex();
        glBindFramebuffer(GL_FRAMEBUFFER, main_fbo_.write_fbo());
        glViewport(0, 0, W, H);
        glUseProgram(prog_mix_);
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, wet);
        glActiveTexture(GL_TEXTURE1); glBindTexture(GL_TEXTURE_2D, dry_tex_);
        u1i(prog_mix_, "uWet", 0);
        u1i(prog_mix_, "uDry", 1);
        u1f(prog_mix_, "uMix", master_intensity);

        int gating_mode = 0;
        if (chroma.gate_fx && chroma.mode != ChromaMode::None) {
            gating_mode = chroma.gate_mode + 1; // 1 = передний план, 2 = фон
        }
        u1i(prog_mix_, "uGatingMode", gating_mode);

        if (gating_mode != 0) {
            u1f(prog_mix_, "uTolerance", chroma.tolerance / 180.f);
            u1f(prog_mix_, "uSoftness",  chroma.softness / 180.f);
            u3f(prog_mix_, "uKeyColor",  chroma.r / 255.f, chroma.g / 255.f, chroma.b / 255.f);
        }

        glBindVertexArray(quad_vao_);
        glDrawArrays(GL_TRIANGLES, 0, 6);
        glBindVertexArray(0);
        glActiveTexture(GL_TEXTURE0);
        main_fbo_.swap();
    }

    // ── Кладём текущий результат в кольцо истории ─────────────────────────────
    // Полный блит канваса пропускаем, если ни один включённый эффект не читает
    // историю - иначе это ~6 МБ/кадр чистых трат на 1080p.
    if (needs_history(params)) push_history();

    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    return main_fbo_.read_tex();
}
