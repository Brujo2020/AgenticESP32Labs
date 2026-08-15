idf_component_register(
    SRCS "hud.c"
    INCLUDE_DIRS "include"
    REQUIRES display audio touch net voice esp_timer
)
