#!/usr/bin/env python3
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from nucleo.entorno import carga_env
carga_env()   # servidor/.env, si existe — ver panel.py

from nucleo.agente import Agente
from nucleo.config import Config
from nucleo.mcp_pool import MCPPool

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

class ConsolaAsistente:
    def __init__(self):
        self.config = Config()
        self.mcp_pool = MCPPool()
        self.agente = Agente(config=self.config, mcp_pool=self.mcp_pool)
        self.running = True

    async def initialize(self):
        logger.info("Inicializando...")
        try:
            await self.agente.initialize()
            logger.info("✅ Listo")
        except Exception as e:
            logger.error(f"❌ {e}")
            sys.exit(1)

    async def run(self):
        await self.initialize()
        print("\n" + "="*50)
        print("🎙️  Asistente ESP32")
        print("="*50)
        print("Comandos: /modelos, /modelo <nombre>, /herramientas, /limpiar, /salir\n")

        while self.running:
            try:
                user_input = input(">>> ").strip()
                if not user_input:
                    continue
                if user_input.startswith("/"):
                    await self._handle_command(user_input)
                else:
                    await self._send_message(user_input)
            except KeyboardInterrupt:
                print("\n👋 Saliendo...")
                self.running = False

    async def _handle_command(self, command: str):
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/modelos":
            modelos = self.agente.get_available_models()
            print(f"\nModelos (actual: {self.agente.current_model}):")
            for m in modelos:
                marker = "📌" if m["name"] == self.agente.current_model else "  "
                print(f"  {marker} {m['name']:15} - {m['description']}")
            print()
        elif cmd == "/modelo":
            if not arg:
                print("❌ Uso: /modelo <nombre>\n")
                return
            try:
                self.agente.set_model(arg)
                print(f"✅ {arg}\n")
            except ValueError as e:
                print(f"❌ {e}\n")
        elif cmd == "/herramientas":
            tools = self.mcp_pool.list_available_tools()
            if tools:
                print(f"\nHerramientas ({len(tools)}):")
                for tool in sorted(tools):
                    print(f"  - {tool}")
                print()
            else:
                print("❌ No hay\n")
        elif cmd == "/limpiar":
            self.agente.limpiar()
            print("✅ Limpiado\n")
        elif cmd == "/salir":
            self.running = False
        else:
            print(f"❌ Desconocido\n")

    async def _send_message(self, user_input: str):
        print()
        try:
            response = await self.agente.chat(user_input)
            print(f"\n🤖 {response}\n")
        except Exception as e:
            logger.error(f"{e}\n")

async def main():
    consola = ConsolaAsistente()
    await consola.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Saliendo...")
        sys.exit(0)
