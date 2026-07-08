#pragma once
#include <glad/glad.h>
#include <string>
#include <functional>
#include "../audio/audio_stats.h"
#include "../audio/segment.h"
#include "../video/overlay_manager.h"


// Идентификаторы эффектов. Совместимость пресетов держится на СТРОКЕ из
// fx_key(), а не на числовом значении, так что список можно свободно
// переставлять и дополнять - старые пресеты просто ищутся по имени, а всё
// незнакомое остаётся выключенным.
// Waveshaper выпилен (визуально был слабым); старые ключи "fx_waveshaper"
// при загрузке пресета просто игнорируются.
enum class FxId {
    DERIVWARP   = 0,   // замена fx_rgb - warp по производной (похоже на datamosh)
    FLASH,
    STUTTER,
    PIXEL_SORT,
    GHOST,
    SCANLINES,
    BITCRUSH,
    BLOCKGLITCH,
    NEGATIVE,
    COLORBLEED,
    INTERLACE,
    BADSIGNAL,
    ZOOMGLITCH,
    MOSAIC,
    PHASESHIFT,
    DITHER,
    FEEDBACK,
    TEMPORALRGB,
    OVERLAYS,
    VORTEX,          // спиральный warp
    FRACTALNOISE,    // FBM-искажение с доменным warp'ом
    SELFDISP,        // предыдущий кадр как карта смещений (ближе всего к настоящему datamosh)
    ASCII,           // ASCII-фильтр на GPU
    // ── Классика ────────────────────────────────────────────────────────────
    RGBSHIFT,        // хроматическое разделение RGB-каналов
    KALI,            // калейдоскоп / зеркальная симметрия
    FISHEYE,         // бочкообразная линза "рыбий глаз"
    VHSTRACK,        // проезд VHS-трекинга
    PIXELDRIFT,      // горизонтальный дрейф по строкам
    // ── Семейство datamosh (временная деградация; питается от предыдущего кадра) ──
    PFRAME_LAG,      // отставание P-кадра / подтаивание застывших блоков
    MVEC_BLOOM,      // ошибочные векторы движения / смазанное цветение
    SELF_CANNIBALIZE,// самопоглощающее смещение
    // ── Генеративные визуализаторы (рисуют картинку ИЗ звука поверх канвы) ────
    VIZ_PLASMA,
    VIZ_RADIAL,
    VIZ_BARS,
    VIZ_ALCHEMY,
    COUNT
};

// ── Метаданные эффектов: единственный источник истины (см. kFxInfo в effect_chain.cpp) ─
// Ключ JSON-пресета, подпись в GUI и категория в GUI для каждого эффекта берутся
// из одной таблицы, поэтому массивы key/label/group не могут разъехаться между
// собой. Чтобы добавить эффект: добавить строку в kFxInfo, добавить FxId,
// подключить шейдер (include + program + один pass-блок в apply()). Больше
// нигде параллельного списка нет.
const char* fx_key(FxId id);    // ключ JSON-пресета, напр. "fx_ghost"
const char* fx_label(FxId id);  // отображаемое имя в GUI, напр. "Ghost Trails"
const char* fx_group(FxId id);  // категория в GUI, напр. "CORE"
const char* fx_tip(FxId id);    // всплывающая подсказка в GUI: как эффект ВЫГЛЯДИТ

// Порядок отображения категорий в панели эффектов GUI (секции рендерятся в этом
// порядке; эффект без категории в списке попадает в хвостовую "OTHER").
extern const char* const kFxGroupOrder[];
extern const int         kFxGroupOrderCount;

// Порядок для клавиатуры / отображения. Эффекты раскладываются сгруппированными
// по категориям (в kFxGroupOrder), а НЕ в порядке enum, чтобы клавиши Q..P
// совпадали со сгруппированным списком в GUI, а не прыгали вразнобой. И обработчик
// клавиатуры, и подсветка в GUI читают эти функции, чтобы оставаться в синхроне.
//   fx_slot_to_id(slot) : слот 0..COUNT-1  -> индекс FxId, или -1, если вне диапазона
//   fx_id_to_slot(id)   : индекс FxId       -> слот 0..COUNT-1
int fx_slot_to_id(int slot);
int fx_id_to_slot(int id);

// Как управляется аудио-реактивная огибающая эффекта.
//   Auto      - атака на музыкальных акцентах (бит ИЛИ смена сегмента), потом спад.
//   Beat      - атака строго на детектированных битах, потом спад.
//   Sustained - огибающая непрерывно следит за громкостью (без стробирования).
//   Manual    - всегда включён на полную, пока активен (VJ держит вручную); звук игнорируется.
enum class TriggerMode { Auto = 0, Beat = 1, Sustained = 2, Manual = 3 };

struct EffectParams {
    bool  enabled   = false;
    float intensity = 1.0f;   // сила от пользователя 0..1 (масштабирует эффект шейдера)
    float chance    = 0.6f;   // вероятность сработать на подходящем событии (Auto/Beat)
    int   mode      = (int)TriggerMode::Auto;
};

enum class AspectMode { Contain = 0, Cover = 1, Stretch = 2, Native = 3 };

// Пара ping-pong фреймбуферов для проходов шейдера
struct FboPair {
    GLuint fbo[2]  = {};
    GLuint tex[2]  = {};
    int    current = 0;
    int    width   = 0, height = 0;

    void   create(int w, int h);
    void   destroy();
    GLuint read_tex()  const { return tex[current]; }
    GLuint read_fbo()  const { return fbo[current]; }
    GLuint write_fbo() const { return fbo[1 - current]; }
    void   swap()            { current = 1 - current; }
};

class EffectChain {
public:
    EffectChain();
    ~EffectChain();

    bool init(int width, int height);
    void resize(int w, int h);
    void destroy();

    // Применяет все включённые эффекты. Возвращает итоговую GL-текстуру.
    // Вызывается на каждом кадре рендера из потока OpenGL.
    // src_w/src_h - исходные размеры input_tex; используются проходом
    // размещения на канве с учётом соотношения сторон.
    GLuint apply(
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
        EffectParams        params[(int)FxId::COUNT]
    );

    int width()  const { return main_fbo_.width; }
    int height() const { return main_fbo_.height; }

private:
    GLuint compile_program(const char* vert, const char* frag);
    void   setup_quad();

    // Блитит src_tex в dst_fbo через passthrough-шейдер, затем свопает main_fbo_
    void   pass(GLuint prog, GLuint src_tex,
                const std::function<void(GLuint prog)>& set_uniforms);

    // Копирует текущий main_fbo read_tex в слот истории
    void   push_history();
    // history[0] = кадр 1 назад, history[1] = кадр 2 назад, history[2] = кадр 3 назад
    GLuint history_tex(int age) const; // age 0..kHistoryLen-1

    // ── Фреймбуферы ──────────────────────────────────────────────────────────
    FboPair main_fbo_;
    FboPair accum_fbo_;  // накопитель fx_feedback (живёт между кадрами)

    // Кольцо истории: kHistoryLen заранее выделенных пар FBO/текстура
    static constexpr int kHistoryLen = 4;
    GLuint hist_fbo_[kHistoryLen] = {};
    GLuint hist_tex_[kHistoryLen] = {};
    int    hist_idx_ = 0;  // слот, который будет записан следующим
    bool   hist_full_ = false;

    // ── Шейдерные программы ──────────────────────────────────────────────────
    GLuint prog_pass_   = 0;
    GLuint prog_place_  = 0;   // размещение на канве с учётом соотношения сторон
    GLuint prog_mix_    = 0;   // dry/wet-смешение для master_intensity
    // Сухая копия размещённого на канве изображения, снятая до применения
    // любых эффектов. Используется финальным смешением master_intensity,
    // чтобы затухать обратно к необработанному изображению.
    GLuint dry_fbo_ = 0;
    GLuint dry_tex_ = 0;
    GLuint prog_derivwarp_   = 0;
    GLuint prog_flash_       = 0;
    GLuint prog_stutter_     = 0;
    GLuint prog_pixsort_     = 0;
    GLuint prog_ghost_       = 0;
    GLuint prog_scanlines_   = 0;
    GLuint prog_bitcrush_    = 0;
    GLuint prog_blockglitch_ = 0;
    GLuint prog_negative_    = 0;
    GLuint prog_colorbleed_  = 0;
    GLuint prog_interlace_   = 0;
    GLuint prog_badsignal_   = 0;
    GLuint prog_zoomglitch_  = 0;
    GLuint prog_mosaic_      = 0;
    GLuint prog_phaseshift_  = 0;
    GLuint prog_dither_      = 0;
    GLuint prog_feedback_    = 0;
    GLuint prog_temporalrgb_ = 0;
    GLuint prog_overlay_     = 0;
    GLuint prog_vortex_      = 0;
    GLuint prog_fractalnoise_= 0;
    GLuint prog_selfdisp_    = 0;
    GLuint prog_ascii_       = 0;
    // Классика
    GLuint prog_rgbshift_    = 0;
    GLuint prog_kali_        = 0;
    GLuint prog_fisheye_     = 0;
    GLuint prog_vhstrack_    = 0;
    GLuint prog_pixeldrift_  = 0;
    // Семейство datamosh
    GLuint prog_pframe_lag_  = 0;
    GLuint prog_mvec_bloom_  = 0;
    GLuint prog_self_cannib_ = 0;
    // Генеративные визуализаторы
    GLuint prog_viz_plasma_  = 0;
    GLuint prog_viz_radial_  = 0;
    GLuint prog_viz_bars_    = 0;
    GLuint prog_viz_alchemy_ = 0;

    // ── Состояние огибающей на эффект (runtime, не сохраняется) ───────────────
    // У каждого эффекта есть огибающая 0..1, которая атакует на триггерных
    // событиях и затухает со временем (либо непрерывно следит за громкостью
    // в режимах Sustained/Manual). Эффект применяется с силой = env * intensity;
    // это заменяет покадровое срабатывание по Бернулли, из-за которого всё стробило.
    float env_[(int)FxId::COUNT] = {};
    bool  prev_beat_ = false;    // детект переднего фронта по stats.beat
    int   prev_seg_  = -1;       // SegmentType предыдущего кадра (детект смены)
    void  update_envelopes(const Segment& seg, const AudioStats& stats,
                           float chaos, float dt, EffectParams params[]);
    // True, если хотя бы один включённый эффект сейчас использует кольцо истории
    // кадров - позволяет пропускать покадровый блит истории, когда он не нужен.
    bool  needs_history(EffectParams params[]) const;

    GLuint quad_vao_ = 0, quad_vbo_ = 0;

    // Текстура ASCII-шрифта (80×8, загружается один раз)
    GLuint ascii_font_tex_ = 0;
    void   create_ascii_font_tex();
};
