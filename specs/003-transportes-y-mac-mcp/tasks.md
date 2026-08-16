# Tasks: Bluetooth + Mac como MCP

- [ ] Spike: BLE GATT mínimo en el ESP32-S3-1.28-BOX (advertising + un
      characteristic de escritura) — confirmar que es viable antes de seguir.
- [ ] `components/bt/`: recibir al menos `vista` y `notifica` por BLE.
- [ ] `servidor/puente_bt.py`: puente Mac↔BLE, registrado como MCP (usa 002).
- [ ] Política de selección de transporte en firmware (BLE si hay, si no WiFi).
- [ ] Reusar `mcps/mac.py`/`sistema.py` como tools del puente.
- [ ] (Depende de 001) Estado del nodo Mac visible en el panel.
- [ ] Documentar emparejamiento en `CONECTAR_AGENTES.md`.
- [ ] Whitelist de MAC / PIN mínimo para el emparejamiento BLE.
