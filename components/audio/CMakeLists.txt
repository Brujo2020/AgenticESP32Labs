idf_component_register(
    SRCS "audio.c"
    INCLUDE_DIRS "include"
    REQUIRES board esp_driver_i2c esp_driver_i2s esp_driver_gpio
    # esp_timer: lo usa el guardian que apaga el amplificador tras 3 s de
    # silencio (esp_timer_get_time en audio_pa/tarea_pa).
    # heap: heap_caps_malloc/free para el buffer de upsample 16k->32k en
    # audio_play_pcm() (PSRAM primero, con fallback a interna).
    PRIV_REQUIRES esp_timer heap
)
