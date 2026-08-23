idf_component_register(
    SRCS "audio.c"
    INCLUDE_DIRS "include"
    REQUIRES board esp_driver_i2c esp_driver_i2s esp_driver_gpio
    # esp_timer: lo usa el guardian que apaga el amplificador tras 3 s de
    # silencio (esp_timer_get_time en audio_pa/tarea_pa).
    PRIV_REQUIRES esp_timer
)
